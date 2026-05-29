from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .evidence import RunRecord, close_run, create_run, record_verification_results, validate_close_evidence
from .io import dump_data, load_data
from .locks import acquire_file_locks, acquire_task_lock, release_locks_for_task, write_scope_locked
from .runner import VerificationResult, run_verification


TASK_STATES = {
    "proposed",
    "needs_clarification",
    "ready",
    "in_progress",
    "blocked",
    "review",
    "verified",
    "accepted",
    "done",
    "archived",
}
EXECUTABLE_STATES = {"ready", "in_progress", "review", "verified", "accepted", "done"}
ALLOWED_TRANSITIONS = {
    ("proposed", "needs_clarification"),
    ("proposed", "ready"),
    ("needs_clarification", "ready"),
    ("needs_clarification", "blocked"),
    ("ready", "in_progress"),
    ("ready", "blocked"),
    ("in_progress", "blocked"),
    ("in_progress", "review"),
    ("review", "in_progress"),
    ("review", "verified"),
    ("verified", "accepted"),
    ("accepted", "done"),
    ("done", "archived"),
    ("blocked", "needs_clarification"),
    ("blocked", "ready"),
}
REQUIRED_FIELDS = {
    "schema_version",
    "id",
    "title",
    "state",
    "priority",
    "type",
}


@dataclass(frozen=True)
class TaskRecord:
    path: Path
    task: dict[str, Any]


def task_root(root: Path, config: dict[str, Any]) -> Path:
    return root / config.get("paths", {}).get("tasks", "harness/tasks")


def validate_task(task: dict[str, Any], directory_state: str | None = None) -> list[str]:
    errors: list[str] = []
    missing = sorted(REQUIRED_FIELDS - set(task))
    if missing:
        errors.append(f"missing required fields: {', '.join(missing)}")

    state = task.get("state")
    if state not in TASK_STATES:
        errors.append(f"invalid state: {state!r}")
    if directory_state and state != directory_state:
        errors.append(f"directory state {directory_state!r} does not match task state {state!r}")

    if state in EXECUTABLE_STATES:
        _require_text(task, "purpose", state, errors)
        _require_list(task, "scope", state, errors)
        _require_list(task, "out_of_scope", state, errors)
        _require_list(task, "bdd_scenarios", state, errors)
        _require_list(task, "unit_tests", state, errors)
        _require_list(task, "acceptance", state, errors)
        write_files = task.get("files", {}).get("write") if isinstance(task.get("files"), dict) else None
        if not isinstance(write_files, list) or not write_files:
            errors.append(f"files.write must be a non-empty list when state is {state}")
        unresolved = task.get("requirements", {}).get("unresolved", [])
        if task.get("type") != "spike" and unresolved:
            errors.append(f"requirements.unresolved must be empty when state is {state}")
    return errors


def iter_tasks(root: Path, config: dict[str, Any]) -> list[TaskRecord]:
    base = task_root(root, config)
    records: list[TaskRecord] = []
    if not base.exists():
        return records
    for path in sorted(base.glob("*/*.yml")):
        task = load_data(path)
        records.append(TaskRecord(path=path, task=task))
    return records


def select_next_task(root: Path, config: dict[str, Any]) -> TaskRecord | None:
    completed = {
        str(record.task.get("id"))
        for record in iter_tasks(root, config)
        if record.task.get("state") in {"done", "archived"}
    }
    candidates: list[TaskRecord] = []
    for record in iter_tasks(root, config):
        task = record.task
        if task.get("state") != "ready":
            continue
        if validate_task(task, directory_state=record.path.parent.name):
            continue
        dependencies = task.get("dependencies", [])
        if any(dep not in completed for dep in dependencies):
            continue
        write_files = task.get("files", {}).get("write", [])
        if write_scope_locked(root, config, write_files):
            continue
        candidates.append(record)
    candidates.sort(key=lambda record: (int(record.task.get("priority", 999)), str(record.task["id"])))
    return candidates[0] if candidates else None


def start_task(root: Path, config: dict[str, Any], task_id: str, actor_role: str) -> RunRecord:
    record = _find_task(root, config, task_id, expected_state="ready")
    errors = validate_task(record.task, directory_state="ready")
    if errors:
        raise ValueError("; ".join(errors))

    run_id_preview = f"pending-{task_id}"
    task_lock = acquire_task_lock(root, config, task_id, run_id_preview)
    write_files = record.task.get("files", {}).get("write", [])
    file_locks = acquire_file_locks(root, config, write_files, task_id)
    run = create_run(root, config, record.task, actor_role, task_lock, file_locks)
    task_lock.write_text(run.run_id + "\n", encoding="utf-8")

    updated = dict(record.task)
    updated["state"] = "in_progress"
    evidence = dict(updated.get("evidence", {}))
    evidence["run_id"] = run.run_id
    evidence["packet"] = str((run.path / "evidence.md").relative_to(root))
    updated["evidence"] = evidence
    target = task_root(root, config) / "in_progress" / record.path.name
    dump_data(updated, target)
    record.path.unlink()
    return run


def block_task(root: Path, config: dict[str, Any], task_id: str, reason: str) -> TaskRecord:
    record = _find_task(root, config, task_id, expected_state=None)
    updated = dict(record.task)
    notes = list(updated.get("notes", []))
    notes.append(reason)
    updated["notes"] = notes
    return _move_task(root, config, record, updated, "blocked")


def transition_task(root: Path, config: dict[str, Any], task_id: str, new_state: str) -> TaskRecord:
    record = _find_task(root, config, task_id, expected_state=None)
    current = str(record.task.get("state"))
    if (current, new_state) not in ALLOWED_TRANSITIONS:
        raise ValueError(f"invalid transition: {current} -> {new_state}")
    updated = dict(record.task)
    return _move_task(root, config, record, updated, new_state)


def close_task(root: Path, config: dict[str, Any], task_id: str) -> TaskRecord:
    record = _find_task(root, config, task_id, expected_state="accepted")
    evidence = record.task.get("evidence", {})
    if not isinstance(evidence, dict) or not evidence.get("run_id") or not evidence.get("packet"):
        raise ValueError("accepted task requires evidence.run_id and evidence.packet before close")
    packet_path = root / str(evidence["packet"])
    if not packet_path.exists():
        raise ValueError("evidence.packet does not exist")
    run_path = root / config.get("paths", {}).get("runs", "harness/runs") / str(evidence["run_id"])
    evidence_errors = validate_close_evidence(run_path, config, task_id)
    if evidence_errors:
        raise ValueError("; ".join(evidence_errors))
    close_run(run_path, task_id)
    release_locks_for_task(root, config, task_id)
    updated = dict(record.task)
    return _move_task(root, config, record, updated, "done")


def verify_task(root: Path, config: dict[str, Any], task_id: str) -> VerificationResult:
    record = _find_task(root, config, task_id, expected_state=None)
    evidence = record.task.get("evidence", {})
    if not isinstance(evidence, dict) or not evidence.get("run_id"):
        raise ValueError("task requires evidence.run_id before verify")
    run_path = root / config.get("paths", {}).get("runs", "harness/runs") / str(evidence["run_id"])
    if not run_path.exists():
        raise ValueError("task evidence.run_id does not reference an existing run")

    result = run_verification(root, config, run_path / "commands")
    record_verification_results(run_path, result)

    updated = dict(record.task)
    updated_evidence = dict(evidence)
    updated_evidence["verify"] = str((run_path / "metadata.yml").relative_to(root))
    updated["evidence"] = updated_evidence
    dump_data(updated, record.path)
    return result


def _find_task(root: Path, config: dict[str, Any], task_id: str, expected_state: str | None) -> TaskRecord:
    for record in iter_tasks(root, config):
        if record.task.get("id") != task_id:
            continue
        if expected_state and record.task.get("state") != expected_state:
            raise ValueError(f"{task_id} is {record.task.get('state')}, expected {expected_state}")
        return record
    raise FileNotFoundError(f"task not found: {task_id}")


def _move_task(
    root: Path,
    config: dict[str, Any],
    record: TaskRecord,
    updated: dict[str, Any],
    new_state: str,
) -> TaskRecord:
    if new_state not in TASK_STATES:
        raise ValueError(f"invalid state: {new_state}")
    updated["state"] = new_state
    target = task_root(root, config) / new_state / record.path.name
    dump_data(updated, target)
    if record.path != target and record.path.exists():
        record.path.unlink()
    return TaskRecord(path=target, task=updated)


def _require_text(task: dict[str, Any], field: str, state: str, errors: list[str]) -> None:
    if not str(task.get(field, "")).strip():
        errors.append(f"{field} must be non-empty when state is {state}")


def _require_list(task: dict[str, Any], field: str, state: str, errors: list[str]) -> None:
    value = task.get(field)
    if not isinstance(value, list) or not value:
        errors.append(f"{field} must be a non-empty list when state is {state}")
