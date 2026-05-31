from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .io import load_data
from .locks import locks_root


def resume_summary(root: Path, config: dict[str, Any]) -> str:
    active_locks = _active_task_locks(root, config)
    if len(active_locks) > 1:
        raise ValueError(
            "multiple active task locks: "
            + ", ".join(active_locks)
            + "; generic resume is ambiguous"
        )

    runs_root = root / config.get("paths", {}).get("runs", "harness/runs")
    if not runs_root.exists():
        return "no unfinished runs"

    active: list[dict[str, Any]] = []
    for metadata_path in sorted(runs_root.glob("*/metadata.yml")):
        metadata = load_data(metadata_path)
        if metadata.get("ended_at") is None and metadata.get("status") != "closed":
            metadata["_path"] = str(metadata_path.parent)
            active.append(metadata)

    if not active:
        return "no unfinished runs"
    if len(active) > 1:
        ids = ", ".join(str(item.get("task_id")) for item in active)
        raise ValueError(f"multiple unfinished runs: {ids}; generic resume is ambiguous")

    item = active[0]
    task_id = item.get("task_id")
    run_id = item.get("run_id")
    run_path = Path(str(item.get("_path")))
    session = item.get("agent_session", {}) if isinstance(item.get("agent_session"), dict) else {}
    latest_event = _latest_ledger_event(run_path)
    lock_missing = _task_lock_missing(root, item)
    next_action = "repair task state or re-acquire lock" if lock_missing else _next_action(latest_event)
    latest_summary = _latest_event_summary(latest_event)
    lock_summary = "task lock missing; " if lock_missing else ""
    if session.get("session_id"):
        return (
            f"{task_id} is in progress in {run_id}; "
            f"session {session.get('session_id')} is {session.get('status')}; "
            f"{lock_summary}{latest_summary}; next action: {next_action}"
        )
    return f"{task_id} is in progress in {run_id}; {lock_summary}{latest_summary}; next action: {next_action}"


def _active_task_locks(root: Path, config: dict[str, Any]) -> list[str]:
    task_locks = locks_root(root, config) / "tasks"
    if not task_locks.exists():
        return []
    return sorted(path.stem for path in task_locks.glob("*.lock"))


def _latest_ledger_event(run_path: Path) -> dict[str, Any] | None:
    ledger_path = run_path / "ledger.jsonl"
    if not ledger_path.exists():
        return None
    lines = [line.strip() for line in ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        return None
    event = json.loads(lines[-1])
    if not isinstance(event, dict):
        raise ValueError("latest ledger event must be a JSON object")
    return event


def _task_lock_missing(root: Path, metadata: dict[str, Any]) -> bool:
    locks = metadata.get("locks", {})
    task_lock = locks.get("task") if isinstance(locks, dict) else None
    if not task_lock:
        return False
    task_lock_path = Path(str(task_lock))
    if not task_lock_path.is_absolute():
        task_lock_path = root / task_lock_path
    return not task_lock_path.exists()


def _latest_event_summary(event: dict[str, Any] | None) -> str:
    if not event:
        return "last event: none"
    data = event.get("data", {})
    command_name = data.get("name") if isinstance(data, dict) else None
    suffix = f" {command_name}" if command_name else ""
    return f"last event: {event.get('event')}{suffix}"


def _next_action(event: dict[str, Any] | None) -> str:
    if not event:
        return "inspect run ledger"
    data = event.get("data", {})
    command_name = str(data.get("name", "")) if isinstance(data, dict) else ""
    exit_code = data.get("exit_code") if isinstance(data, dict) else None
    event_name = str(event.get("event", ""))
    if command_name == "bdd" and (event_name == "gate_failed" or exit_code not in {None, 0}):
        return "repair BDD scenario or requirement boundary"
    if event_name == "gate_failed" and command_name:
        return f"fix failing {command_name} gate"
    if event_name == "gate_passed" and command_name == "bdd":
        return "run unit tests"
    if event_name == "gate_passed" and command_name == "unit":
        return "continue with remaining gates or implementation"
    return "run BDD"
