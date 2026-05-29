from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from .io import dump_data, load_data
from .runner import VERIFICATION_COMMANDS, CommandResult, VerificationResult


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    path: Path


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def create_run(
    root: Path,
    config: dict[str, Any],
    task: dict[str, Any],
    actor_role: str,
    task_lock: Path,
    file_locks: list[Path],
) -> RunRecord:
    task_id = str(task["id"])
    run_id = f"{utc_timestamp()}-{task_id}"
    run_root = root / config.get("paths", {}).get("runs", "harness/runs")
    run_path = run_root / run_id
    (run_path / "commands").mkdir(parents=True, exist_ok=True)

    metadata = {
        "schema_version": 1,
        "run_id": run_id,
        "task_id": task_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "ended_at": None,
        "status": "in_progress",
        "actor": {"role": actor_role, "id": "local"},
        "workspace": {
            "root": str(root),
            "branch": None,
            "worktree": None,
            "commit_before": None,
            "commit_after": None,
        },
        "locks": {
            "task": str(task_lock.relative_to(root)),
            "files": [str(path.relative_to(root)) for path in file_locks],
        },
        "commands": {name: None for name in VERIFICATION_COMMANDS},
        "result": {"dor_passed": True, "dod_passed": False, "conclusion": None},
    }
    dump_data(metadata, run_path / "metadata.yml")
    write_evidence_packet(run_path / "evidence.md", task, run_id)
    append_ledger(
        run_path,
        "task_started",
        task_id,
        run_id,
        actor_role,
        {"state": "in_progress"},
    )
    return RunRecord(run_id=run_id, path=run_path)


def append_ledger(
    run_path: Path,
    event: str,
    task_id: str,
    run_id: str,
    actor_role: str,
    data: dict[str, Any],
) -> None:
    line = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "task_id": task_id,
        "run_id": run_id,
        "actor": {"role": actor_role, "id": "local"},
        "data": data,
    }
    with (run_path / "ledger.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line, ensure_ascii=False) + "\n")


def write_evidence_packet(path: Path, task: dict[str, Any], run_id: str) -> None:
    path.write_text(
        "\n".join(
            [
                "# Evidence Packet",
                "",
                "## Task",
                "",
                f"- ID: {task.get('id')}",
                f"- Title: {task.get('title')}",
                f"- Run: {run_id}",
                "- Branch:",
                "- Commit Before:",
                "- Commit After:",
                "",
                "## Requirement Boundary",
                "",
                f"- Purpose: {task.get('purpose')}",
                f"- Scope: {task.get('scope')}",
                f"- Out of Scope: {task.get('out_of_scope')}",
                f"- Unresolved Requirements: {task.get('requirements', {}).get('unresolved', [])}",
                "",
                "## BDD",
                "",
                "- Command:",
                "- Result:",
                "- Log:",
                "- Scenarios Covered:",
                "",
                "## Unit Tests",
                "",
                "- Command:",
                "- Result:",
                "- Log:",
                "- Tests Covered:",
                "",
                "## Risks",
                "",
                "- Remaining:",
                "- Follow-ups:",
                "",
            ]
        ),
        encoding="utf-8",
    )


def record_verification_results(run_path: Path, result: VerificationResult) -> None:
    metadata_path = run_path / "metadata.yml"
    metadata = load_data(metadata_path)
    commands = dict(metadata.get("commands", {}))

    for command_result in result.results:
        commands[command_result.name] = _command_metadata(run_path, command_result)
    metadata["commands"] = commands

    run_result = dict(metadata.get("result", {}))
    run_result["verification_passed"] = not result.failed
    run_result["conclusion"] = "verification_passed" if not result.failed else "verification_failed"
    metadata["result"] = run_result
    dump_data(metadata, metadata_path)

    task_id = str(metadata.get("task_id"))
    run_id = str(metadata.get("run_id"))
    actor_role = str(metadata.get("actor", {}).get("role", "orchestrator"))
    for command_result in result.results:
        data = {
            "name": command_result.name,
            "exit_code": command_result.exit_code,
            "log": _log_reference(run_path, command_result.log),
        }
        append_ledger(run_path, "command_finished", task_id, run_id, actor_role, data)
        gate_event = "gate_passed" if command_result.exit_code == 0 else "gate_failed"
        append_ledger(run_path, gate_event, task_id, run_id, actor_role, data)


def validate_close_evidence(run_path: Path, config: dict[str, Any], task_id: str) -> list[str]:
    metadata_path = run_path / "metadata.yml"
    if not metadata_path.exists():
        return ["run metadata does not exist"]

    metadata = load_data(metadata_path)
    errors: list[str] = []
    if str(metadata.get("task_id")) != task_id:
        errors.append("run metadata task_id does not match task")

    if not config.get("policies", {}).get("require_fresh_verify_for_done", True):
        return errors

    commands = metadata.get("commands", {})
    if not isinstance(commands, dict):
        return errors + ["run metadata commands must be a mapping"]

    for name in required_verification_commands(config):
        item = commands.get(name)
        if not isinstance(item, dict):
            errors.append(f"missing passing evidence for {name}")
            continue
        expected_command = str(config.get("commands", {}).get(name))
        if item.get("command") != expected_command:
            errors.append(f"{name} command does not match configured command")
        if item.get("fresh") is not True:
            errors.append(f"{name} evidence is not fresh")
        if item.get("exit_code") != 0:
            errors.append(f"{name} exit_code is {item.get('exit_code')}")
        log_ref = item.get("log")
        if not isinstance(log_ref, str) or not _log_exists(run_path, log_ref):
            errors.append(f"{name} log does not exist")
    return errors


def required_verification_commands(config: dict[str, Any]) -> list[str]:
    commands = config.get("commands", {})
    return [name for name in VERIFICATION_COMMANDS if commands.get(name)]


def close_run(run_path: Path, task_id: str) -> None:
    metadata_path = run_path / "metadata.yml"
    metadata = load_data(metadata_path)
    metadata["ended_at"] = datetime.now(timezone.utc).isoformat()
    metadata["status"] = "closed"
    result = dict(metadata.get("result", {}))
    result["dod_passed"] = True
    result["conclusion"] = "done"
    metadata["result"] = result
    dump_data(metadata, metadata_path)
    append_ledger(
        run_path,
        "closed",
        task_id,
        str(metadata.get("run_id")),
        str(metadata.get("actor", {}).get("role", "orchestrator")),
        {"state": "done"},
    )


def _command_metadata(run_path: Path, result: CommandResult) -> dict[str, Any]:
    timestamp = datetime.now(timezone.utc).isoformat()
    return {
        "command": result.command,
        "started_at": result.started_at or timestamp,
        "ended_at": result.ended_at or timestamp,
        "exit_code": result.exit_code,
        "log": _log_reference(run_path, result.log),
        "fresh": True,
        "ci_url": None,
    }


def _log_reference(run_path: Path, log: Path) -> str:
    try:
        return log.relative_to(run_path).as_posix()
    except ValueError:
        return str(log)


def _log_exists(run_path: Path, log_ref: str) -> bool:
    log_path = Path(log_ref)
    if not log_path.is_absolute():
        log_path = run_path / log_path
    return log_path.exists()
