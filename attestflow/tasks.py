from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any

from .evidence import (
    RunRecord,
    append_ledger,
    close_run,
    create_run,
    record_verification_results,
    update_run_workspace,
    validate_close_evidence,
    workspace_root_for_run,
)
from .io import dump_data, load_data
from .locks import acquire_file_locks, acquire_task_lock, normalize_file_path, release_locks_for_task, write_scope_locked
from .runner import VerificationResult, run_verification
from .sessions import create_agent_session
from .worktrees import apply_task_worktree, provision_task_worktree


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
TASK_ID_RE = re.compile(r"^TASK-\d+$")
EXECUTABLE_STATES = {"ready", "in_progress", "review", "verified", "accepted", "done"}
RUN_EVIDENCE_STATES = {"in_progress", "review", "verified", "accepted"}
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
    ("accepted", "in_progress"),
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
BLOCKER_REQUIRED_FIELDS = {
    "id",
    "type",
    "reason",
    "unblock_condition",
    "owner",
    "source",
    "status",
    "created_at",
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

    if "schema_version" in task and task.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if "id" in task and not TASK_ID_RE.match(str(task.get("id", ""))):
        errors.append("id must match TASK-<number>")
    state = task.get("state")
    if state not in TASK_STATES:
        errors.append(f"invalid state: {state!r}")
    if directory_state and state != directory_state:
        errors.append(f"directory state {directory_state!r} does not match task state {state!r}")
    if "priority" in task and type(task.get("priority")) is not int:
        errors.append("priority must be an integer")
    if "blockers" in task and not isinstance(task.get("blockers"), list):
        errors.append("blockers must be a list")
    _validate_optional_string_list(task, "context", "context", errors)
    _validate_optional_string_list(task, "blocks", "blocks", errors)
    _validate_optional_string_list(task, "risks", "risks", errors)
    _validate_optional_string_list(task, "notes", "notes", errors)
    requirements = task.get("requirements")
    if "requirements" in task and not isinstance(requirements, dict):
        errors.append("requirements must be a mapping")
        requirements = {}
    if isinstance(requirements, dict):
        for key in ("confirmed", "unresolved", "assumptions"):
            _validate_optional_string_list(requirements, key, f"requirements.{key}", errors)
    files = task.get("files")
    if "files" in task and not isinstance(files, dict):
        errors.append("files must be a mapping")
        files = {}
    agents = task.get("agents")
    if "agents" in task and not isinstance(agents, dict):
        errors.append("agents must be a mapping")
        agents = {}
    links = task.get("links")
    if "links" in task and not isinstance(links, dict):
        errors.append("links must be a mapping")
        links = {}
    if isinstance(files, dict):
        _validate_optional_string_list(files, "read", "files.read", errors)
    if isinstance(agents, dict):
        if "owner" in agents and not str(agents.get("owner", "")).strip():
            errors.append("agents.owner must be non-empty")
        _validate_optional_string_list(agents, "allowed_roles", "agents.allowed_roles", errors)
    if isinstance(links, dict):
        for key in ("issues", "prs", "docs"):
            _validate_optional_string_list(links, key, f"links.{key}", errors)

    active_blockers = _active_blockers(task)
    if state == "blocked":
        if not active_blockers:
            errors.append("blocked task must have at least one active blocker")
        errors.extend(_validate_blockers(task))
    elif active_blockers:
        errors.append(f"active blockers require state blocked, got {state}")

    evidence = task.get("evidence", {})
    if "evidence" in task and not isinstance(evidence, dict):
        errors.append("evidence must be a mapping")
        evidence = {}
    if state in RUN_EVIDENCE_STATES:
        if not isinstance(evidence, dict) or not evidence.get("run_id"):
            errors.append(f"{state} task requires evidence.run_id")
        if not isinstance(evidence, dict) or not evidence.get("session"):
            errors.append(f"{state} task requires evidence.session")
        if state in {"review", "verified", "accepted"} and (not isinstance(evidence, dict) or not evidence.get("packet")):
            errors.append(f"{state} task requires evidence.packet")
    if state in {"done", "archived"}:
        if not isinstance(evidence, dict) or not evidence.get("run_id") or not evidence.get("packet"):
            errors.append("completed task requires evidence.run_id and evidence.packet")

    if state in EXECUTABLE_STATES:
        external_inputs = task.get("external_inputs")
        if not isinstance(external_inputs, dict):
            errors.append(f"external_inputs must be a mapping when state is {state}")
        else:
            external_input_errors = _external_inputs_shape_errors(external_inputs)
            if external_input_errors:
                errors.extend(external_input_errors)
            elif _required_external_inputs(task):
                errors.append("external_inputs must be empty when state is ready; move task to blocked until inputs exist")
        _require_text(task, "purpose", state, errors)
        _require_non_empty_string_list(task, "scope", state, errors)
        _require_non_empty_string_list(task, "out_of_scope", state, errors)
        _require_non_empty_string_list(task, "bdd_scenarios", state, errors)
        _require_non_empty_string_list(task, "unit_tests", state, errors)
        _require_non_empty_string_list(task, "acceptance", state, errors)
        _require_list_field(task, "dependencies", state, errors)
        _require_non_empty_string_entries(task.get("dependencies"), "dependencies", errors)
        write_files = files.get("write") if isinstance(files, dict) else None
        if not isinstance(write_files, list) or not write_files:
            errors.append(f"files.write must be a non-empty list when state is {state}")
        elif not _all_non_empty_strings(write_files):
            errors.append("files.write entries must be non-empty strings")
        unresolved = requirements.get("unresolved", []) if isinstance(requirements, dict) and isinstance(requirements.get("unresolved"), list) else []
        if task.get("type") != "spike" and unresolved:
            errors.append(f"requirements.unresolved must be empty when state is {state}")
    return errors


def iter_tasks(root: Path, config: dict[str, Any]) -> list[TaskRecord]:
    base = task_root(root, config)
    records: list[TaskRecord] = []
    if not base.exists():
        return records
    seen: set[str] = set()
    for path in sorted(base.glob("*/*.json")):
        directory_state = path.parent.name
        if directory_state not in TASK_STATES:
            raise ValueError(f"unknown task state directory: {directory_state}")
        task = load_data(path)
        task_id = task.get("id")
        if task_id is not None and str(task_id) != path.stem:
            raise ValueError(f"task id {str(task_id)!r} does not match filename {path.stem!r}")
        if task_id is not None:
            normalized_id = str(task_id)
            if normalized_id in seen:
                raise ValueError(f"duplicate task id: {normalized_id}")
            seen.add(normalized_id)
        records.append(TaskRecord(path=path, task=task))
    return records


def select_next_task(root: Path, config: dict[str, Any]) -> TaskRecord | None:
    selected = select_dispatchable_tasks(root, config, limit=1)
    return selected[0] if selected else None


def select_dispatchable_tasks(root: Path, config: dict[str, Any], *, limit: int | None = None) -> list[TaskRecord]:
    records = iter_tasks(root, config)
    completed = {
        str(record.task.get("id"))
        for record in records
        if record.task.get("state") in {"done", "archived"}
        and not validate_task(record.task, directory_state=record.path.parent.name)
    }
    candidates: list[TaskRecord] = []
    for record in records:
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
    selected: list[TaskRecord] = []
    reserved_write_files: set[str] = set()
    for record in candidates:
        if limit is not None and len(selected) >= limit:
            break
        write_files = [normalize_file_path(str(item)) for item in record.task.get("files", {}).get("write", [])]
        if any(file_name in reserved_write_files for file_name in write_files):
            continue
        selected.append(record)
        reserved_write_files.update(write_files)
    return selected


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
    workspace_root = root
    worktree = provision_task_worktree(root, config, record.task, run.run_id)
    if worktree:
        workspace_root = worktree.path
        update_run_workspace(
            run.path,
            {
                "root": str(workspace_root),
                "branch": worktree.branch,
                "worktree": str(worktree.path),
                "commit_before": worktree.commit_before,
            },
        )
        append_ledger(
            run.path,
            "worktree_created",
            task_id,
            run.run_id,
            actor_role,
            {"path": str(worktree.path), "commit_before": worktree.commit_before},
        )

    updated = dict(record.task)
    updated["state"] = "in_progress"
    evidence = dict(updated.get("evidence", {}))
    evidence["run_id"] = run.run_id
    evidence["packet"] = str((run.path / "evidence.md").relative_to(root))
    if worktree:
        evidence["worktree"] = str(worktree.path)
    updated["evidence"] = evidence
    session = create_agent_session(root, config, updated, run, workspace_root=workspace_root)
    evidence["session"] = str(session.path.relative_to(root))
    updated["evidence"] = evidence
    target_state = "in_progress"
    if session.status == "blocked":
        session_data = load_data(session.path)
        updated = _add_blocker(
            updated,
            reason=str(session_data.get("summary") or session_data.get("failure") or "agent session blocked"),
            unblock_condition="Resolve the agent session prerequisite, then unblock and dispatch the task again.",
            owner="user",
            blocker_type="agent_session",
            source="session:launch",
        )
        release_locks_for_task(root, config, task_id)
        target_state = "blocked"
    target = task_root(root, config) / target_state / f"{task_id}.json"
    updated["state"] = target_state
    dump_data(updated, target)
    record.path.unlink()
    return run


def block_task(
    root: Path,
    config: dict[str, Any],
    task_id: str,
    reason: str,
    *,
    unblock_condition: str | None = None,
    owner: str = "user",
    blocker_type: str = "external_input",
    source: str = "cli",
) -> TaskRecord:
    record = _find_task(root, config, task_id, expected_state=None)
    current = str(record.task.get("state"))
    if (current, "blocked") not in ALLOWED_TRANSITIONS:
        raise ValueError(f"invalid transition: {current} -> blocked")
    updated = _add_blocker(
        record.task,
        reason=reason,
        unblock_condition=unblock_condition or f"Resolve blocker: {reason}",
        owner=owner,
        blocker_type=blocker_type,
        source=source,
    )
    notes = list(updated.get("notes", []))
    notes.append(reason)
    updated["notes"] = notes
    if record.task.get("state") == "in_progress":
        release_locks_for_task(root, config, task_id)
    return _move_task(root, config, record, updated, "blocked")


def unblock_task(
    root: Path,
    config: dict[str, Any],
    task_id: str,
    blocker_id: str,
    *,
    resolution: str,
) -> TaskRecord:
    record = _find_task(root, config, task_id, expected_state="blocked")
    blockers = _blockers(record.task)
    for blocker in blockers:
        if blocker.get("id") != blocker_id:
            continue
        if blocker.get("status") != "active":
            raise ValueError(f"blocker is not active: {blocker_id}")
        blocker["status"] = "resolved"
        blocker["resolution"] = resolution
        blocker["resolved_at"] = _utc_now()
        updated = dict(record.task)
        updated["blockers"] = blockers
        new_state = "blocked" if _active_blockers(updated) else "ready"
        if new_state == "ready":
            updated["external_inputs"] = _empty_external_inputs()
        return _move_task(root, config, record, updated, new_state)
    raise ValueError(f"blocker not found: {blocker_id}")


def transition_task(root: Path, config: dict[str, Any], task_id: str, new_state: str) -> TaskRecord:
    record = _find_task(root, config, task_id, expected_state=None)
    current = str(record.task.get("state"))
    if (current, new_state) not in ALLOWED_TRANSITIONS:
        raise ValueError(f"invalid transition: {current} -> {new_state}")
    updated = dict(record.task)
    updated["state"] = new_state
    errors = validate_task(updated, directory_state=new_state)
    if errors:
        raise ValueError("; ".join(errors))
    if new_state in {"verified", "accepted"}:
        _require_passing_verification_evidence(root, config, updated, new_state)
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
    evidence_errors = validate_close_evidence(run_path, config, task_id, packet_path=packet_path)
    if evidence_errors:
        raise ValueError("; ".join(evidence_errors))
    applied_worktree = apply_task_worktree(root, run_path, task_id)
    if applied_worktree:
        update_run_workspace(
            run_path,
            {
                "root": str(applied_worktree.path),
                "worktree": str(applied_worktree.path),
                "commit_before": applied_worktree.commit_before,
                "commit_after": applied_worktree.commit_after,
                "applied_to_control": applied_worktree.applied_to_control,
                "worktree_finalized": True,
            },
        )
        append_ledger(
            run_path,
            "worktree_applied",
            task_id,
            str(evidence["run_id"]),
            str(record.task.get("agents", {}).get("owner", "orchestrator")),
            {
                "path": str(applied_worktree.path),
                "commit_before": applied_worktree.commit_before,
                "commit_after": applied_worktree.commit_after,
                "applied_to_control": applied_worktree.applied_to_control,
            },
        )
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

    result = run_verification(workspace_root_for_run(run_path, root), config, run_path / "commands")
    record_verification_results(run_path, result)

    updated = dict(record.task)
    updated_evidence = dict(evidence)
    updated_evidence["verify"] = str((run_path / "metadata.yml").relative_to(root))
    updated["evidence"] = updated_evidence
    dump_data(updated, record.path)
    return result


def record_task_evidence_reference(
    root: Path,
    config: dict[str, Any],
    task_id: str,
    key: str,
    path: Path,
) -> TaskRecord:
    record = _find_task(root, config, task_id, expected_state=None)
    absolute_path = path if path.is_absolute() else root / path
    if not absolute_path.exists():
        raise ValueError(f"evidence reference does not exist: {path}")
    try:
        reference = str(absolute_path.relative_to(root))
    except ValueError:
        reference = str(absolute_path)
    updated = dict(record.task)
    evidence = dict(updated.get("evidence", {}))
    evidence[key] = reference
    updated["evidence"] = evidence
    return _move_task(root, config, record, updated, str(record.task.get("state")))


def _require_passing_verification_evidence(
    root: Path,
    config: dict[str, Any],
    task: dict[str, Any],
    target_state: str,
) -> None:
    task_id = str(task.get("id"))
    evidence = task.get("evidence", {})
    if not isinstance(evidence, dict) or not evidence.get("run_id") or not evidence.get("packet"):
        raise ValueError(f"verification evidence required before transition to {target_state}")
    run_path = root / config.get("paths", {}).get("runs", "harness/runs") / str(evidence["run_id"])
    packet_path = root / str(evidence["packet"])
    errors = validate_close_evidence(run_path, config, task_id, packet_path=packet_path)
    if errors:
        raise ValueError(
            f"verification evidence required before transition to {target_state}: " + "; ".join(errors)
        )


def _find_task(root: Path, config: dict[str, Any], task_id: str, expected_state: str | None) -> TaskRecord:
    for record in iter_tasks(root, config):
        if record.task.get("id") != task_id:
            continue
        directory_state = record.path.parent.name
        if record.task.get("state") != directory_state:
            raise ValueError(f"directory state {directory_state!r} does not match task state {record.task.get('state')!r}")
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
    errors = validate_task(updated, directory_state=new_state)
    if errors:
        raise ValueError("; ".join(errors))
    target = task_root(root, config) / new_state / f"{record.task['id']}.json"
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


def _require_non_empty_string_list(task: dict[str, Any], field: str, state: str, errors: list[str]) -> None:
    value = task.get(field)
    _require_list(task, field, state, errors)
    _require_non_empty_string_entries(value, field, errors)


def _require_list_field(task: dict[str, Any], field: str, state: str, errors: list[str]) -> None:
    if not isinstance(task.get(field), list):
        errors.append(f"{field} must be a list when state is {state}")


def _require_non_empty_string_entries(value: Any, field: str, errors: list[str]) -> None:
    if isinstance(value, list) and not _all_non_empty_strings(value):
        errors.append(f"{field} entries must be non-empty strings")


def _validate_optional_string_list(mapping: dict[str, Any], key: str, label: str, errors: list[str]) -> None:
    if key not in mapping:
        return
    value = mapping.get(key)
    if not isinstance(value, list):
        errors.append(f"{label} must be a list")
        return
    if not _all_non_empty_strings(value):
        errors.append(f"{label} entries must be non-empty strings")


def _all_non_empty_strings(value: list[Any]) -> bool:
    return all(isinstance(item, str) and item.strip() for item in value)


def _add_blocker(
    task: dict[str, Any],
    *,
    reason: str,
    unblock_condition: str,
    owner: str,
    blocker_type: str,
    source: str,
) -> dict[str, Any]:
    updated = dict(task)
    blockers = _blockers(updated)
    blockers.append(
        {
            "id": _next_blocker_id(blockers),
            "type": blocker_type,
            "reason": str(reason),
            "unblock_condition": str(unblock_condition),
            "owner": str(owner),
            "source": str(source),
            "status": "active",
            "created_at": _utc_now(),
            "resolved_at": None,
        }
    )
    updated["blockers"] = blockers
    return updated


def _blockers(task: dict[str, Any]) -> list[dict[str, Any]]:
    blockers = task.get("blockers", [])
    if not isinstance(blockers, list):
        return []
    return [dict(item) for item in blockers if isinstance(item, dict)]


def _active_blockers(task: dict[str, Any]) -> list[dict[str, Any]]:
    return [blocker for blocker in _blockers(task) if blocker.get("status") == "active"]


def _validate_blockers(task: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    raw_blockers = task.get("blockers", [])
    if not isinstance(raw_blockers, list):
        return ["blockers must be a list when state is blocked"]
    for index, blocker in enumerate(raw_blockers):
        if not isinstance(blocker, dict):
            errors.append(f"blockers[{index}] must be an object")
            continue
        missing = sorted(BLOCKER_REQUIRED_FIELDS - set(blocker))
        if missing:
            errors.append(f"blockers[{index}] missing required fields: {', '.join(missing)}")
        if blocker.get("status") not in {"active", "resolved"}:
            errors.append(f"blockers[{index}].status must be active or resolved")
        for key in ("id", "type", "reason", "unblock_condition", "owner", "source", "created_at"):
            if not str(blocker.get(key, "")).strip():
                errors.append(f"blockers[{index}].{key} must be non-empty")
    return errors


def _required_external_inputs(task: dict[str, Any]) -> list[str]:
    external_inputs = task.get("external_inputs", {})
    if not isinstance(external_inputs, dict):
        return []
    required: list[str] = []
    for value in external_inputs.values():
        if isinstance(value, list):
            required.extend(str(item) for item in value if str(item).strip())
        elif str(value or "").strip():
            required.append(str(value))
    return required


def _external_inputs_shape_errors(external_inputs: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key, value in external_inputs.items():
        if not isinstance(value, list):
            errors.append(f"external_inputs.{key} must be a list")
            continue
        if not _all_non_empty_strings(value):
            errors.append(f"external_inputs.{key} entries must be non-empty strings")
    return errors


def _empty_external_inputs() -> dict[str, list[str]]:
    return {"credentials": [], "services": [], "user_decisions": []}


def _next_blocker_id(blockers: list[dict[str, Any]]) -> str:
    highest = 0
    for blocker in blockers:
        raw_id = str(blocker.get("id", ""))
        if not raw_id.startswith("BLK-"):
            continue
        try:
            highest = max(highest, int(raw_id.removeprefix("BLK-")))
        except ValueError:
            continue
    return f"BLK-{highest + 1:04d}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
