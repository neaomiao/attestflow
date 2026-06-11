from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import shlex
import subprocess
from typing import Any

from .provider_failures import redact_text


VERIFICATION_COMMANDS = ("bdd", "unit", "lint", "typecheck", "secret_scan", "project_verify")
DEFAULT_VERIFICATION_TIMEOUT_SECONDS = 600
DEFAULT_VERIFICATION_MAX_OUTPUT_BYTES = 1_048_576
TIMEOUT_EXIT_CODE = 124
COMMAND_ERROR_EXIT_CODE = 127


@dataclass(frozen=True)
class CommandResult:
    name: str
    command: str
    exit_code: int
    log: Path
    started_at: str | None = None
    ended_at: str | None = None
    timed_out: bool = False
    truncated: bool = False


@dataclass(frozen=True)
class VerificationResult:
    results: list[CommandResult]
    failed: list[str]


def run_logged(
    command: str,
    cwd: Path,
    log: Path,
    name: str = "command",
    *,
    timeout_seconds: float = DEFAULT_VERIFICATION_TIMEOUT_SECONDS,
    max_output_bytes: int = DEFAULT_VERIFICATION_MAX_OUTPUT_BYTES,
) -> CommandResult:
    log.parent.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(timezone.utc).isoformat()
    timed_out = False
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        ended_at = datetime.now(timezone.utc).isoformat()
        output, truncated = _prepare_log(f"[attestflow] invalid verification command: {exc}\n", max_output_bytes)
        log.write_text(output, encoding="utf-8")
        return CommandResult(
            name=name,
            command=command,
            exit_code=COMMAND_ERROR_EXIT_CODE,
            log=log,
            started_at=started_at,
            ended_at=ended_at,
            truncated=truncated,
        )
    if not argv:
        ended_at = datetime.now(timezone.utc).isoformat()
        output, truncated = _prepare_log("[attestflow] empty verification command\n", max_output_bytes)
        log.write_text(output, encoding="utf-8")
        return CommandResult(
            name=name,
            command=command,
            exit_code=COMMAND_ERROR_EXIT_CODE,
            log=log,
            started_at=started_at,
            ended_at=ended_at,
            truncated=truncated,
        )
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            shell=False,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
        exit_code = completed.returncode
        raw_output = (completed.stdout or "") + (completed.stderr or "")
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = TIMEOUT_EXIT_CODE
        raw_output = _output_text(exc.stdout) + _output_text(exc.stderr)
        raw_output += f"\n[attestflow] command timed out after {timeout_seconds:g} seconds\n"
    ended_at = datetime.now(timezone.utc).isoformat()
    output, truncated = _prepare_log(raw_output, max_output_bytes)
    log.write_text(output, encoding="utf-8")
    return CommandResult(
        name=name,
        command=command,
        exit_code=exit_code,
        log=log,
        started_at=started_at,
        ended_at=ended_at,
        timed_out=timed_out,
        truncated=truncated,
    )


def run_verification(root: Path, config: dict[str, Any], log_root: Path) -> VerificationResult:
    commands = config.get("commands", {})
    timeout_seconds, max_output_bytes = _verification_command_limits(config)
    results: list[CommandResult] = []
    failed: list[str] = []
    for name in VERIFICATION_COMMANDS:
        command = commands.get(name)
        if not command:
            continue
        result = run_logged(
            str(command),
            root,
            log_root / f"{name}.log",
            name=name,
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
        )
        results.append(result)
        if result.exit_code != 0:
            failed.append(name)
    return VerificationResult(results=results, failed=failed)


def _verification_command_limits(config: dict[str, Any]) -> tuple[float, int]:
    security = config.get("security", {}) if isinstance(config.get("security", {}), dict) else {}
    provider_commands = security.get("provider_commands", {}) if isinstance(security.get("provider_commands", {}), dict) else {}
    verification_commands = (
        security.get("verification_commands", {}) if isinstance(security.get("verification_commands", {}), dict) else {}
    )
    timeout_seconds = _positive_number(
        verification_commands.get("timeout_seconds"),
        DEFAULT_VERIFICATION_TIMEOUT_SECONDS,
    )
    max_output_bytes = int(
        _positive_number(
            verification_commands.get("max_output_bytes", provider_commands.get("max_output_bytes")),
            DEFAULT_VERIFICATION_MAX_OUTPUT_BYTES,
        )
    )
    return timeout_seconds, max_output_bytes


def _positive_number(value: Any, default: float) -> float:
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    return float(default)


def _prepare_log(output: str, max_output_bytes: int) -> tuple[str, bool]:
    redacted = redact_text(output)
    return _truncate_text(redacted, max_output_bytes)


def _truncate_text(text: str, max_bytes: int) -> tuple[str, bool]:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    marker = "\n<truncated>\n"
    marker_bytes = marker.encode("utf-8")
    if max_bytes <= len(marker_bytes):
        return marker[:max_bytes], True
    keep = max_bytes - len(marker_bytes)
    head_bytes = keep // 2
    tail_bytes = keep - head_bytes
    head = encoded[:head_bytes].decode("utf-8", errors="ignore")
    tail = encoded[-tail_bytes:].decode("utf-8", errors="ignore")
    return head + marker + tail, True


def _output_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    return value
