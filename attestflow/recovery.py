from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
from typing import Any

from .io import dump_data, load_data
from .sessions import resume_agent_session


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


def recover_runtime(
    root: Path,
    config: dict[str, Any],
    *,
    apply: bool = False,
    resume_interrupted: bool = False,
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    issues.extend(_orphan_autopilot_run_issues(root, config))
    issues.extend(_task_state_mismatch_issues(root, config))
    issues.extend(_stale_worktree_issues(root, config))
    issues.extend(_interrupted_session_issues(root, config))
    if apply:
        for issue in issues:
            action = _apply_issue(root, config, issue, resume_interrupted=resume_interrupted)
            if action:
                actions.append(action)
        actions.append(_write_ledger_snapshot(root, config))
    return {
        "schema_version": 1,
        "applied": apply,
        "issues": issues,
        "actions": actions,
    }


def _orphan_autopilot_run_issues(root: Path, config: dict[str, Any]) -> list[dict[str, Any]]:
    run_root = _path(root, config, "autopilot_runs", "harness/autopilot-runs")
    if not run_root.exists():
        return []
    issues = []
    for run_dir in sorted(path for path in run_root.iterdir() if path.is_dir() and not path.name.startswith(".")):
        if (run_dir / "metadata.json").exists():
            continue
        issues.append(
            {
                "type": "orphan_autopilot_run",
                "path": _relative(root, run_dir),
                "run_id": run_dir.name,
                "summary": "autopilot run directory is missing metadata.json",
                "repair": "write failed metadata that preserves ledger and makes the run inspectable",
            }
        )
    return issues


def _task_state_mismatch_issues(root: Path, config: dict[str, Any]) -> list[dict[str, Any]]:
    task_root = _path(root, config, "tasks", "harness/tasks")
    if not task_root.exists():
        return []
    issues = []
    for path in sorted(task_root.glob("*/*.json")):
        directory_state = path.parent.name
        if directory_state not in TASK_STATES:
            continue
        try:
            task = load_data(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            issues.append(
                {
                    "type": "unreadable_task",
                    "path": _relative(root, path),
                    "summary": f"task JSON could not be read: {exc}",
                    "repair": "quarantine invalid task JSON for manual inspection",
                }
            )
            continue
        task_state = task.get("state")
        if task_state in TASK_STATES and task_state != directory_state:
            issues.append(
                {
                    "type": "task_state_mismatch",
                    "path": _relative(root, path),
                    "task_id": str(task.get("id") or path.stem),
                    "directory_state": directory_state,
                    "task_state": str(task_state),
                    "summary": f"task file is under {directory_state} but records state {task_state}",
                    "repair": "move task JSON to the directory recorded by its state",
                }
            )
    return issues


def _stale_worktree_issues(root: Path, config: dict[str, Any]) -> list[dict[str, Any]]:
    runs_root = _path(root, config, "runs", "harness/runs")
    if not runs_root.exists():
        return []
    issues = []
    for metadata_path in sorted(runs_root.glob("*/metadata.yml")):
        try:
            metadata = load_data(metadata_path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        workspace = metadata.get("workspace", {})
        if not isinstance(workspace, dict) or not workspace.get("worktree"):
            continue
        worktree = Path(str(workspace["worktree"]))
        if not worktree.exists():
            continue
        finalized = workspace.get("worktree_finalized") is True and workspace.get("applied_to_control") is True
        closed = metadata.get("status") == "closed" or metadata.get("ended_at") is not None
        if not finalized or not closed:
            continue
        issues.append(
            {
                "type": "stale_worktree",
                "path": _relative(root, worktree),
                "run_id": str(metadata.get("run_id") or metadata_path.parent.name),
                "task_id": str(metadata.get("task_id") or ""),
                "summary": "finalized task worktree still exists after close",
                "repair": "remove finalized worktree directory",
            }
        )
    return issues


def _interrupted_session_issues(root: Path, config: dict[str, Any]) -> list[dict[str, Any]]:
    runs_root = _path(root, config, "runs", "harness/runs")
    if not runs_root.exists():
        return []
    issues = []
    for session_path in sorted(runs_root.glob("*/session.yml")):
        try:
            session = load_data(session_path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        status = str(session.get("status") or "")
        failure = str(session.get("failure") or "")
        if not (status.endswith("_cancelled") or "cancelled" in failure.lower()):
            continue
        task_id = str(session.get("task_id") or "")
        issues.append(
            {
                "type": "interrupted_session",
                "path": _relative(root, session_path),
                "run_id": str(session.get("run_id") or session_path.parent.name),
                "task_id": task_id,
                "status": status,
                "summary": "provider session was interrupted before a successful resume/launch",
                "repair": "resume the provider session through the session adapter",
                "next_action": f"python -m attestflow session resume {task_id}" if task_id else "inspect session.yml",
            }
        )
    return issues


def _apply_issue(
    root: Path,
    config: dict[str, Any],
    issue: dict[str, Any],
    *,
    resume_interrupted: bool,
) -> dict[str, Any] | None:
    issue_type = issue.get("type")
    if issue_type == "orphan_autopilot_run":
        return _repair_orphan_autopilot_run(root, config, issue)
    if issue_type == "task_state_mismatch":
        return _move_task_to_recorded_state(root, config, issue)
    if issue_type == "stale_worktree":
        return _remove_stale_worktree(root, issue)
    if issue_type == "interrupted_session" and resume_interrupted:
        return _resume_interrupted_session(root, config, issue)
    return None


def _repair_orphan_autopilot_run(root: Path, config: dict[str, Any], issue: dict[str, Any]) -> dict[str, Any]:
    run_id = str(issue["run_id"])
    run_dir = _path(root, config, "autopilot_runs", "harness/autopilot-runs") / run_id
    metadata = {
        "schema_version": 1,
        "run_id": run_id,
        "status": "failed",
        "pause_reason": "recovered_missing_metadata",
        "steps": 0,
        "actions": [],
        "planned": [],
        "dispatched": [],
        "failed": [],
        "blocked": [],
        "cancelled": [],
        "skipped": [],
        "release_status": "unknown",
        "recovered_at": _now(),
        "recovery": {"source": "attestflow recover", "reason": "metadata.json was missing"},
    }
    dump_data(metadata, run_dir / "metadata.json")
    ledger_path = run_dir / "ledger.jsonl"
    with ledger_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"timestamp": _now(), "event": "autopilot_recovered", "data": metadata["recovery"]}) + "\n")
    return {
        "type": "repair_orphan_autopilot_run",
        "status": "applied",
        "path": _relative(root, run_dir / "metadata.json"),
    }


def _move_task_to_recorded_state(root: Path, config: dict[str, Any], issue: dict[str, Any]) -> dict[str, Any]:
    source = root / str(issue["path"])
    target = _path(root, config, "tasks", "harness/tasks") / str(issue["task_state"]) / source.name
    if target.exists():
        return {
            "type": "move_task_to_recorded_state",
            "status": "skipped",
            "path": _relative(root, source),
            "reason": f"target already exists: {_relative(root, target)}",
        }
    target.parent.mkdir(parents=True, exist_ok=True)
    source.replace(target)
    return {
        "type": "move_task_to_recorded_state",
        "status": "applied",
        "path": _relative(root, source),
        "target": _relative(root, target),
    }


def _remove_stale_worktree(root: Path, issue: dict[str, Any]) -> dict[str, Any]:
    worktree = (root / str(issue["path"])).resolve()
    if not _safe_to_remove(root, worktree):
        return {
            "type": "remove_stale_worktree",
            "status": "skipped",
            "path": _relative(root, worktree),
            "reason": "worktree path is not safe to remove",
        }
    if _looks_like_git_worktree(worktree):
        completed = subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree)],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode == 0:
            return {"type": "remove_stale_worktree", "status": "applied", "path": _relative(root, worktree)}
    shutil.rmtree(worktree)
    return {"type": "remove_stale_worktree", "status": "applied", "path": _relative(root, worktree)}


def _resume_interrupted_session(root: Path, config: dict[str, Any], issue: dict[str, Any]) -> dict[str, Any]:
    session_path = root / str(issue["path"])
    run_path = session_path.parent
    try:
        record = resume_agent_session(root, config, run_path)
    except Exception as exc:
        return {
            "type": "resume_interrupted_session",
            "status": "failed",
            "path": _relative(root, session_path),
            "reason": str(exc),
        }
    return {
        "type": "resume_interrupted_session",
        "status": record.status,
        "path": _relative(root, session_path),
        "run_id": str(issue.get("run_id") or run_path.name),
    }


def _write_ledger_snapshot(root: Path, config: dict[str, Any]) -> dict[str, Any]:
    snapshot_root = _path(root, config, "snapshots", "harness/snapshots")
    snapshot_root.mkdir(parents=True, exist_ok=True)
    path = snapshot_root / f"ledger-snapshot-{_timestamp_for_path()}.json"
    snapshot = {"schema_version": 1, "created_at": _now(), "ledgers": _ledger_index(root, config)}
    dump_data(snapshot, path)
    return {
        "type": "write_ledger_snapshot",
        "status": "applied",
        "path": _relative(root, path),
        "ledger_count": len(snapshot["ledgers"]),
    }


def _ledger_index(root: Path, config: dict[str, Any]) -> dict[str, Any]:
    ledgers: dict[str, Any] = {}
    for base in _ledger_roots(root, config):
        if not base.exists():
            continue
        for path in sorted(base.rglob("ledger.jsonl")):
            rel = _relative(root, path)
            lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            last_event = None
            last_hash = None
            if lines:
                try:
                    payload = json.loads(lines[-1])
                except json.JSONDecodeError:
                    payload = {}
                if isinstance(payload, dict):
                    last_event = payload.get("event")
                    last_hash = payload.get("hash")
            ledgers[rel] = {"line_count": len(lines), "last_event": last_event, "last_hash": last_hash}
    return ledgers


def _ledger_roots(root: Path, config: dict[str, Any]) -> list[Path]:
    defaults = {
        "runs": "harness/runs",
        "autopilot_runs": "harness/autopilot-runs",
        "capability_runs": "harness/capability-runs",
        "ci_runs": "harness/ci-runs",
        "pr_runs": "harness/pr-runs",
        "release_runs": "harness/release-runs",
    }
    return [_path(root, config, key, default) for key, default in defaults.items()]


def _safe_to_remove(root: Path, path: Path) -> bool:
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    if resolved_path == resolved_root:
        return False
    if not resolved_path.exists() or not resolved_path.is_dir():
        return False
    return True


def _looks_like_git_worktree(path: Path) -> bool:
    return (path / ".git").exists()


def _path(root: Path, config: dict[str, Any], key: str, default: str) -> Path:
    paths = config.get("paths", {}) if isinstance(config.get("paths"), dict) else {}
    value = Path(str(paths.get(key, default)))
    return value if value.is_absolute() else root / value


def _relative(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp_for_path() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S-%fZ")
