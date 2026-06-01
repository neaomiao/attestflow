from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .io import load_data


def inspect_run(root: Path, config: dict[str, Any], run: str | None = None) -> dict[str, Any]:
    run_path = _resolve_run_path(root, config, run)
    metadata_path = run_path / "metadata.json"
    metadata = _load_metadata(root, metadata_path)
    run_id = str(metadata.get("run_id") or run_path.name)
    blockers = _collect_blockers(root, config, metadata)
    provider_failures = _collect_provider_failures(root, config, metadata)
    return {
        "schema_version": 1,
        "run_id": run_id,
        "status": metadata.get("status"),
        "steps": metadata.get("steps", 0),
        "pause_reason": metadata.get("pause_reason"),
        "path": _relative_to_root(root, run_path),
        "metadata_path": _relative_to_root(root, metadata_path),
        "actions": _string_list(metadata.get("actions")),
        "planned": _string_list(metadata.get("planned")),
        "dispatched": _string_list(metadata.get("dispatched")),
        "failed": _string_list(metadata.get("failed")),
        "blocked": _string_list(metadata.get("blocked")),
        "cancelled": _string_list(metadata.get("cancelled")),
        "release_status": metadata.get("release_status"),
        "release": metadata.get("release"),
        "timeline": _read_timeline(run_path / "ledger.jsonl"),
        "blockers": blockers,
        "provider_failures": provider_failures,
        "next_action": _next_action(metadata, blockers, provider_failures),
    }


def inspect_run_diff(root: Path, config: dict[str, Any], left: str, right: str) -> dict[str, Any]:
    left_metadata = _load_run_metadata(root, config, left)
    right_metadata = _load_run_metadata(root, config, right)
    left_id = str(left_metadata.get("run_id") or _resolve_run_path(root, config, left).name)
    right_id = str(right_metadata.get("run_id") or _resolve_run_path(root, config, right).name)
    scalar_changes: dict[str, dict[str, Any]] = {}
    for key in ("status", "pause_reason", "release_status"):
        before = left_metadata.get(key)
        after = right_metadata.get(key)
        if before != after:
            scalar_changes[key] = {"from": before, "to": after}
    list_changes: dict[str, dict[str, list[str]]] = {}
    for key in ("actions", "planned", "dispatched", "failed", "blocked", "cancelled", "releaser_tasks"):
        before = _string_list(left_metadata.get(key))
        after = _string_list(right_metadata.get(key))
        added = [item for item in after if item not in before]
        removed = [item for item in before if item not in after]
        if added or removed:
            list_changes[key] = {"added": added, "removed": removed}
    return {
        "schema_version": 1,
        "left_run_id": left_id,
        "right_run_id": right_id,
        "scalar_changes": scalar_changes,
        "list_changes": list_changes,
    }


def _load_run_metadata(root: Path, config: dict[str, Any], run: str) -> dict[str, Any]:
    return _load_metadata(root, _resolve_run_path(root, config, run) / "metadata.json")


def _load_metadata(root: Path, metadata_path: Path) -> dict[str, Any]:
    if not metadata_path.exists():
        raise FileNotFoundError(f"autopilot run metadata missing: {_relative_to_root(root, metadata_path)}")
    return load_data(metadata_path)


def _resolve_run_path(root: Path, config: dict[str, Any], run: str | None) -> Path:
    run_root = root / str(config.get("paths", {}).get("autopilot_runs", "harness/autopilot-runs"))
    if not run:
        metadata_paths = sorted(run_root.glob("*/metadata.json"))
        if not metadata_paths:
            raise FileNotFoundError("no autopilot runs found")
        return metadata_paths[-1].parent
    raw_path = Path(run)
    candidates: list[Path]
    if raw_path.is_absolute():
        candidates = [raw_path]
    else:
        candidates = [root / raw_path, run_root / raw_path]
    normalized = [_metadata_parent(candidate) for candidate in candidates]
    for candidate in normalized:
        if (candidate / "metadata.json").exists():
            return candidate
    return normalized[-1]


def _metadata_parent(path: Path) -> Path:
    if path.name == "metadata.json":
        return path.parent
    return path


def _read_timeline(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            events.append({"index": index, "event": "invalid_ledger_line", "error": str(exc)})
            continue
        if not isinstance(raw, dict):
            events.append({"index": index, "event": "invalid_ledger_line", "value": str(raw)})
            continue
        event = {"index": index, "event": str(raw.get("event") or "unknown")}
        if raw.get("timestamp"):
            event["timestamp"] = raw["timestamp"]
        data = raw.get("data")
        if isinstance(data, dict) and data:
            event["data"] = data
        events.append(event)
    return events


def _collect_blockers(root: Path, config: dict[str, Any], metadata: dict[str, Any]) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    for item in _list(metadata.get("blocked")):
        normalized = _normalize_metadata_blocker(item)
        if normalized:
            blockers.append(normalized)
    task_root = root / str(config.get("paths", {}).get("tasks", "harness/tasks")) / "blocked"
    if task_root.exists():
        for path in sorted(task_root.glob("*.json")):
            try:
                task = load_data(path)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                blockers.append(
                    {
                        "task_id": path.stem,
                        "blocker_id": None,
                        "reason": f"failed to read blocked task: {exc}",
                        "owner": None,
                        "status": "unknown",
                        "source": _relative_to_root(root, path),
                    }
                )
                continue
            blockers.extend(_normalize_task_blockers(root, path, task))
    return _dedupe_blockers(blockers)


def _normalize_metadata_blocker(item: Any) -> dict[str, Any] | None:
    if isinstance(item, dict):
        task_id = item.get("task_id") or item.get("id") or item.get("task")
        reasons = item.get("reasons")
        reason = item.get("reason")
        if not reason and isinstance(reasons, list) and reasons:
            reason = "; ".join(str(reason_item) for reason_item in reasons)
        return {
            "task_id": str(task_id or "unknown"),
            "blocker_id": item.get("blocker_id") or item.get("blocker") or item.get("id"),
            "reason": str(reason or "blocked"),
            "owner": item.get("owner"),
            "status": str(item.get("status") or "active"),
            "unblock_condition": item.get("unblock_condition"),
            "source": "metadata.blocked",
        }
    if item is None:
        return None
    return {
        "task_id": str(item),
        "blocker_id": None,
        "reason": "blocked",
        "owner": None,
        "status": "active",
        "unblock_condition": None,
        "source": "metadata.blocked",
    }


def _normalize_task_blockers(root: Path, path: Path, task: dict[str, Any]) -> list[dict[str, Any]]:
    task_id = str(task.get("id") or path.stem)
    raw_blockers = _list(task.get("blockers"))
    if not raw_blockers:
        return [
            {
                "task_id": task_id,
                "blocker_id": None,
                "reason": str(task.get("block_reason") or task.get("title") or "blocked"),
                "owner": None,
                "status": "active",
                "unblock_condition": None,
                "source": _relative_to_root(root, path),
            }
        ]
    blockers: list[dict[str, Any]] = []
    for raw in raw_blockers:
        if isinstance(raw, dict):
            status = str(raw.get("status") or "active")
            if status == "resolved":
                continue
            blockers.append(
                {
                    "task_id": task_id,
                    "blocker_id": raw.get("id") or raw.get("blocker_id"),
                    "reason": str(raw.get("reason") or raw.get("title") or "blocked"),
                    "owner": raw.get("owner"),
                    "status": status,
                    "unblock_condition": raw.get("unblock_condition"),
                    "source": _relative_to_root(root, path),
                }
            )
            continue
        blockers.append(
            {
                "task_id": task_id,
                "blocker_id": None,
                "reason": str(raw),
                "owner": None,
                "status": "active",
                "unblock_condition": None,
                "source": _relative_to_root(root, path),
            }
        )
    return blockers


def _dedupe_blockers(blockers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    result: list[dict[str, Any]] = []
    for blocker in blockers:
        key = (
            str(blocker.get("task_id") or ""),
            str(blocker.get("blocker_id") or ""),
            str(blocker.get("reason") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(blocker)
    return result


def _collect_provider_failures(root: Path, config: dict[str, Any], metadata: dict[str, Any]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for item in _list(metadata.get("failed")):
        failure = _normalize_metadata_failure(item)
        if failure:
            failures.append(failure)
    seen_paths: set[Path] = set()
    for base in _provider_failure_roots(root, config):
        if not base.exists():
            continue
        for path in sorted(base.rglob("*failure.json")):
            if path in seen_paths:
                continue
            seen_paths.add(path)
            try:
                raw = load_data(path)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                failures.append(
                    {
                        "source": _relative_to_root(root, path),
                        "provider": None,
                        "type": "unreadable_failure",
                        "summary": str(exc),
                        "automatic_action": "inspect_failure",
                        "recovery_strategy": [],
                        "retriable": False,
                    }
                )
                continue
            failures.append(_normalize_failure(raw, source=_relative_to_root(root, path)))
    return _dedupe_failures(failures)


def _normalize_metadata_failure(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    raw = item.get("provider_failure") or item.get("failure")
    if isinstance(raw, dict):
        failure = _normalize_failure(raw, source="metadata.failed")
        task_id = item.get("task_id") or item.get("id") or item.get("task")
        if task_id:
            failure["task_id"] = str(task_id)
        return failure
    if item.get("type") or item.get("summary") or item.get("automatic_action"):
        return _normalize_failure(item, source="metadata.failed")
    return None


def _normalize_failure(raw: dict[str, Any], *, source: str) -> dict[str, Any]:
    strategy = raw.get("recovery_strategy") or raw.get("recovery") or []
    if isinstance(strategy, str):
        strategy = [strategy]
    if not isinstance(strategy, list):
        strategy = [str(strategy)]
    return {
        "source": source,
        "provider": raw.get("provider"),
        "type": str(raw.get("type") or "failed"),
        "summary": str(raw.get("summary") or "provider failed"),
        "automatic_action": raw.get("automatic_action") or raw.get("action"),
        "recovery_strategy": [str(item) for item in strategy],
        "retriable": bool(raw.get("retriable", False)),
        "returncode": raw.get("returncode"),
    }


def _dedupe_failures(failures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str, str]] = set()
    result: list[dict[str, Any]] = []
    for failure in failures:
        key = (
            str(failure.get("source") or ""),
            str(failure.get("provider") or ""),
            str(failure.get("type") or ""),
            str(failure.get("summary") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(failure)
    return result


def _provider_failure_roots(root: Path, config: dict[str, Any]) -> list[Path]:
    paths = config.get("paths", {})
    defaults = {
        "capability_runs": "harness/capability-runs",
        "runs": "harness/runs",
        "ci_runs": "harness/ci-runs",
        "pr_runs": "harness/pr-runs",
        "release_runs": "harness/release-runs",
    }
    return [root / str(paths.get(key, default)) for key, default in defaults.items()]


def _next_action(
    metadata: dict[str, Any],
    blockers: list[dict[str, Any]],
    provider_failures: list[dict[str, Any]],
) -> str | None:
    if blockers:
        return (
            'resolve blockers with python -m attestflow unblock TASK --blocker BLOCKER_ID --resolution "...", '
            "then run python -m attestflow autopilot --resume"
        )
    if provider_failures or metadata.get("failed"):
        return "inspect provider failures, fix the root cause, then run python -m attestflow autopilot --resume"
    if metadata.get("cancelled"):
        return "inspect cancelled task sessions, then run python -m attestflow autopilot --resume"
    status = metadata.get("status")
    if metadata.get("pause_reason") or status == "paused":
        return "python -m attestflow autopilot --resume"
    if status in {"finished", "done"}:
        return None
    if status == "running":
        return "python -m attestflow autopilot --status"
    return None


def _relative_to_root(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _string_list(value: Any) -> list[str]:
    return [_stringify_item(item) for item in _list(value)]


def _stringify_item(item: Any) -> str:
    if isinstance(item, dict):
        for key in ("id", "task_id", "run_id", "event", "status"):
            if key in item:
                return str(item[key])
        return json.dumps(item, ensure_ascii=False, sort_keys=True)
    return str(item)


def _list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]
