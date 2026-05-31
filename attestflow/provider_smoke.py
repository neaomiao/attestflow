from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from typing import Any

from .agent_adapters import PROVIDER_DEFAULTS
from .provider_contracts import run_provider_contract_suite
from .provider_failures import classify_provider_failure, redact_text


DEFAULT_PROVIDER_TIMEOUT_SECONDS = 20
DEFAULT_PROVIDER_RETRIES = 1
VERSION_ARGS = {
    "codex": ["--version"],
    "claude-code": ["--version"],
    "opencode": ["--version"],
}


def run_provider_readiness_suite(
    root: Path,
    provider: str,
    *,
    command: str | None = None,
    timeout_seconds: float | None = None,
    retries: int = DEFAULT_PROVIDER_RETRIES,
    skip_contract: bool = False,
) -> dict[str, Any]:
    if provider not in PROVIDER_DEFAULTS:
        raise ValueError(f"unknown provider: {provider}")
    timeout = timeout_seconds or DEFAULT_PROVIDER_TIMEOUT_SECONDS
    version = probe_provider_version(root, provider, command=command, timeout_seconds=timeout)
    smoke = run_provider_live_smoke(
        root,
        provider,
        command=command,
        timeout_seconds=timeout,
        retries=retries,
    )
    contract = (
        {
            "schema_version": 1,
            "provider": provider,
            "status": "skipped",
            "fixtures": [],
        }
        if skip_contract
        else run_provider_contract_suite(root, provider, command=command)
    )
    status = _readiness_status(version, smoke, contract)
    return {
        "schema_version": 1,
        "provider": provider,
        "status": status,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "version": version,
        "smoke": smoke,
        "contract": contract,
    }


def probe_provider_version(
    root: Path,
    provider: str,
    *,
    command: str | None = None,
    timeout_seconds: float = DEFAULT_PROVIDER_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    executable = command or str(PROVIDER_DEFAULTS[provider]["command"])
    if not _command_exists(executable):
        failure = classify_provider_failure(
            provider,
            reason="auth_missing",
            error=f"{provider} command not found: {executable}",
        )
        return {"schema_version": 1, "status": "blocked", "command": executable, "output": "", "failure": failure}
    argv = [executable, *VERSION_ARGS.get(provider, ["--version"])]
    try:
        completed = subprocess.run(
            argv,
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        failure = classify_provider_failure(
            provider,
            reason="timeout",
            returncode=-1,
            stdout=_output_text(exc.stdout),
            stderr=_output_text(exc.stderr),
            error=f"version probe timed out after {timeout_seconds:g} seconds",
        )
        return {"schema_version": 1, "status": "failed", "command": _display(argv), "output": "", "failure": failure}
    output = redact_text("\n".join(part for part in (completed.stdout, completed.stderr) if part.strip()))
    if completed.returncode != 0:
        failure = classify_provider_failure(
            provider,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            error=f"version probe failed with exit code {completed.returncode}",
        )
        return {"schema_version": 1, "status": "blocked", "command": _display(argv), "output": output, "failure": failure}
    return {"schema_version": 1, "status": "passed", "command": _display(argv), "output": output}


def run_provider_live_smoke(
    root: Path,
    provider: str,
    *,
    command: str | None = None,
    timeout_seconds: float = DEFAULT_PROVIDER_TIMEOUT_SECONDS,
    retries: int = DEFAULT_PROVIDER_RETRIES,
) -> dict[str, Any]:
    attempts = []
    max_attempts = max(1, retries + 1)
    for attempt in range(1, max_attempts + 1):
        result = _run_provider_live_smoke_once(root, provider, command=command, timeout_seconds=timeout_seconds, attempt=attempt)
        attempts.append(result)
        failure = result.get("failure")
        if result["status"] == "passed":
            return {**result, "attempts": attempts}
        if not isinstance(failure, dict) or not failure.get("retriable"):
            return {**result, "attempts": attempts}
    return {**attempts[-1], "attempts": attempts}


def _run_provider_live_smoke_once(
    root: Path,
    provider: str,
    *,
    command: str | None,
    timeout_seconds: float,
    attempt: int,
) -> dict[str, Any]:
    adapter_path = Path(__file__).resolve().parent / "agent_adapters.py"
    payload = {
        "schema_version": 1,
        "action": "launch",
        "agent_provider": provider,
        "root": str(root),
        "control_root": str(root),
        "session": {
            "session_id": "provider-live-smoke",
            "task_id": "PROVIDER-SMOKE",
            "run_id": "PROVIDER-SMOKE",
            "role": "doctor",
            "status": "prepared",
            "external_session_id": None,
        },
        "provider_options": {"command": command} if command else {},
        "prompt_packet": {
            "path": "provider-live-smoke.md",
            "absolute_path": str(root / "provider-live-smoke.md"),
            "content": "Provider live smoke. Return a minimal successful agent response for Attestflow.",
        },
    }
    try:
        completed = subprocess.run(
            [sys.executable, str(adapter_path)],
            cwd=root,
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        failure = classify_provider_failure(
            provider,
            reason="timeout",
            returncode=-1,
            stdout=_output_text(exc.stdout),
            stderr=_output_text(exc.stderr),
            error=f"live smoke timed out after {timeout_seconds:g} seconds",
        )
        return {
            "schema_version": 1,
            "status": "failed",
            "attempt": attempt,
            "failure": failure,
            "stdout": redact_text(_output_text(exc.stdout)),
            "stderr": redact_text(_output_text(exc.stderr)),
        }
    stdout = completed.stdout
    stderr = completed.stderr
    if completed.returncode != 0:
        failure = classify_provider_failure(
            provider,
            returncode=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            error=f"live smoke adapter failed with exit code {completed.returncode}",
        )
        return _smoke_failure_payload(attempt, failure, stdout, stderr)
    try:
        output = json.loads(stdout or "")
    except json.JSONDecodeError as exc:
        failure = classify_provider_failure(
            provider,
            reason="invalid_output",
            returncode=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            error=f"live smoke adapter returned invalid JSON: {exc}",
        )
        return _smoke_failure_payload(attempt, failure, stdout, stderr)
    if not isinstance(output, dict):
        failure = classify_provider_failure(
            provider,
            reason="invalid_output",
            returncode=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            error="live smoke adapter output must be a JSON object",
        )
        return _smoke_failure_payload(attempt, failure, stdout, stderr)
    status = str(output.get("status"))
    if status in {"launched", "resumed"}:
        return {
            "schema_version": 1,
            "status": "passed",
            "attempt": attempt,
            "adapter_status": status,
            "external_session_id": output.get("external_session_id"),
            "summary": output.get("summary", ""),
            "stdout": redact_text(stdout),
            "stderr": redact_text(stderr),
        }
    failure = classify_provider_failure(
        provider,
        returncode=completed.returncode,
        stdout=stdout,
        stderr=stderr,
        error=str(output.get("summary") or f"live smoke adapter status {status}"),
    )
    payload_status = "blocked" if failure["automatic_action"].startswith("block_") else "failed"
    return {
        "schema_version": 1,
        "status": payload_status,
        "attempt": attempt,
        "adapter_status": status,
        "failure": failure,
        "stdout": redact_text(stdout),
        "stderr": redact_text(stderr),
    }


def _smoke_failure_payload(attempt: int, failure: dict[str, Any], stdout: str, stderr: str) -> dict[str, Any]:
    payload_status = "blocked" if failure["automatic_action"].startswith("block_") else "failed"
    return {
        "schema_version": 1,
        "status": payload_status,
        "attempt": attempt,
        "failure": failure,
        "stdout": redact_text(stdout),
        "stderr": redact_text(stderr),
    }


def _readiness_status(version: dict[str, Any], smoke: dict[str, Any], contract: dict[str, Any]) -> str:
    statuses = {str(version.get("status")), str(smoke.get("status")), str(contract.get("status"))}
    if "blocked" in statuses:
        return "blocked"
    if "failed" in statuses:
        return "failed"
    return "passed"


def _command_exists(command: str) -> bool:
    return bool(shutil.which(command) or Path(command).exists())


def _output_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _display(argv: list[str]) -> str:
    return " ".join(shlex.quote(item) for item in argv)
