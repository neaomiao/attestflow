from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import subprocess
from typing import Any


VERIFICATION_COMMANDS = ("bdd", "unit", "lint", "typecheck", "secret_scan", "project_verify")


@dataclass(frozen=True)
class CommandResult:
    name: str
    command: str
    exit_code: int
    log: Path
    started_at: str | None = None
    ended_at: str | None = None


@dataclass(frozen=True)
class VerificationResult:
    results: list[CommandResult]
    failed: list[str]


def run_logged(command: str, cwd: Path, log: Path, name: str = "command") -> CommandResult:
    log.parent.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(timezone.utc).isoformat()
    completed = subprocess.run(
        command,
        cwd=cwd,
        shell=True,
        text=True,
        capture_output=True,
        check=False,
    )
    ended_at = datetime.now(timezone.utc).isoformat()
    log.write_text((completed.stdout or "") + (completed.stderr or ""), encoding="utf-8")
    return CommandResult(
        name=name,
        command=command,
        exit_code=completed.returncode,
        log=log,
        started_at=started_at,
        ended_at=ended_at,
    )


def run_verification(root: Path, config: dict[str, Any], log_root: Path) -> VerificationResult:
    commands = config.get("commands", {})
    results: list[CommandResult] = []
    failed: list[str] = []
    for name in VERIFICATION_COMMANDS:
        command = commands.get(name)
        if not command:
            continue
        result = run_logged(str(command), root, log_root / f"{name}.log", name=name)
        results.append(result)
        if result.exit_code != 0:
            failed.append(name)
    return VerificationResult(results=results, failed=failed)
