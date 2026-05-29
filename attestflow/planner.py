from __future__ import annotations

from datetime import datetime, timezone
import re
from pathlib import Path
from typing import Any

from .io import dump_data
from .tasks import TaskRecord, iter_tasks, task_root, validate_task


TASK_ID_PATTERN = re.compile(r"^TASK-(\d+)$")


def import_planner_tasks(root: Path, config: dict[str, Any], plan: dict[str, Any]) -> list[TaskRecord]:
    raw_tasks = plan.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise ValueError("planner output must include a non-empty tasks list")

    next_number = _next_task_number(root, config)
    id_by_key: dict[str, str] = {}
    normalized: list[dict[str, Any]] = []

    for offset, raw_task in enumerate(raw_tasks):
        if not isinstance(raw_task, dict):
            raise ValueError("planner task entries must be objects")
        task_id = f"TASK-{next_number + offset:04d}"
        key = str(raw_task.get("key", "")).strip()
        if key:
            if key in id_by_key:
                raise ValueError(f"duplicate planner task key: {key}")
            id_by_key[key] = task_id
        normalized.append(_normalize_planner_task(raw_task, task_id))

    for task in normalized:
        task["dependencies"] = [id_by_key.get(str(dep), str(dep)) for dep in task.get("dependencies", [])]

    errors: list[str] = []
    for task in normalized:
        task_errors = validate_task(task, directory_state=str(task["state"]))
        errors.extend(f"{task['id']}: {error}" for error in task_errors)
        target = task_root(root, config) / str(task["state"]) / f"{task['id']}.yml"
        if target.exists():
            errors.append(f"{task['id']}: task file already exists")
    if errors:
        raise ValueError("; ".join(errors))

    records: list[TaskRecord] = []
    for task in normalized:
        target = task_root(root, config) / str(task["state"]) / f"{task['id']}.yml"
        dump_data(task, target)
        records.append(TaskRecord(path=target, task=task))
    return records


def _normalize_planner_task(raw_task: dict[str, Any], task_id: str) -> dict[str, Any]:
    timestamp = datetime.now(timezone.utc).isoformat()
    requirements = _dict(raw_task.get("requirements"))
    files = _dict(raw_task.get("files"))
    external_inputs = _dict(raw_task.get("external_inputs"))
    links = _dict(raw_task.get("links"))
    agents = _dict(raw_task.get("agents"))

    return {
        "schema_version": 1,
        "id": task_id,
        "title": str(raw_task.get("title", "")).strip(),
        "state": str(raw_task.get("state", "ready")).strip() or "ready",
        "priority": int(raw_task.get("priority", 100)),
        "type": str(raw_task.get("type", "feature")).strip() or "feature",
        "purpose": str(raw_task.get("purpose", "")).strip(),
        "context": _list(raw_task.get("context")),
        "scope": _list(raw_task.get("scope")),
        "out_of_scope": _list(raw_task.get("out_of_scope")),
        "requirements": {
            "confirmed": _list(requirements.get("confirmed")),
            "unresolved": _list(requirements.get("unresolved")),
            "assumptions": _list(requirements.get("assumptions")),
        },
        "bdd_scenarios": _list(raw_task.get("bdd_scenarios")),
        "unit_tests": _list(raw_task.get("unit_tests")),
        "acceptance": _list(raw_task.get("acceptance")),
        "dependencies": _list(raw_task.get("dependencies")),
        "blocks": _list(raw_task.get("blocks")),
        "files": {
            "read": _list(files.get("read")),
            "write": _list(files.get("write")),
        },
        "agents": {
            "owner": str(agents.get("owner", "orchestrator")).strip() or "orchestrator",
            "allowed_roles": _list(agents.get("allowed_roles")),
        },
        "external_inputs": {
            "credentials": _list(external_inputs.get("credentials")),
            "services": _list(external_inputs.get("services")),
            "user_decisions": _list(external_inputs.get("user_decisions")),
        },
        "evidence": {"run_id": None, "red": None, "green": None, "verify": None, "packet": None},
        "links": {
            "issues": _list(links.get("issues")),
            "prs": _list(links.get("prs")),
            "docs": _list(links.get("docs")),
        },
        "risks": _list(raw_task.get("risks")),
        "notes": _list(raw_task.get("notes")),
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def _next_task_number(root: Path, config: dict[str, Any]) -> int:
    highest = 0
    for record in iter_tasks(root, config):
        match = TASK_ID_PATTERN.match(str(record.task.get("id", "")))
        if match:
            highest = max(highest, int(match.group(1)))
    return highest + 1


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]
