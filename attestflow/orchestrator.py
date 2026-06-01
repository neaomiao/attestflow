from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
from typing import Any

from .capabilities import (
    is_capability_configured,
    run_intake_capability,
    run_planner_capability,
    run_release_capability,
    run_task_capability,
)
from .ci import run_ci_status
from .evidence import append_ledger, update_run_workspace, utc_timestamp
from .git import run_git_publish
from .io import dump_data, load_data
from .locks import file_lock_path, locks_root, normalize_file_path, release_locks_for_task
from .pr import run_pr_ensure, run_pr_merge, run_pr_status
from .release import run_release_status
from .sessions import launch_agent_session
from .tasks import (
    TaskRecord,
    block_task,
    close_task,
    iter_tasks,
    start_task,
    task_root,
    transition_task,
    validate_task,
    verify_task,
)
from .worktrees import apply_task_worktree


COMPLETED_STATES = {"done", "archived"}
ACTIVE_STATES = {"in_progress", "review", "verified", "accepted"}
EXTERNAL_PENDING_STATUSES = {"running", "queued", "unknown"}
AUTOPILOT_METADATA_SCHEMA_VERSION = 1
AUTOPILOT_STATE_MACHINE_VERSION = 1
AUTOPILOT_TERMINAL_STATUSES = {"finished", "blocked", "failed", "cancelled"}


@dataclass(frozen=True)
class PlannedTask:
    task_id: str
    title: str
    priority: int
    path: Path
    write_files: list[str]
    dependencies: list[str]


@dataclass(frozen=True)
class ExecutionBatch:
    index: int
    tasks: list[PlannedTask]


@dataclass(frozen=True)
class SkippedTask:
    task_id: str
    state: str
    path: Path
    reasons: list[str]


@dataclass(frozen=True)
class ExecutionPlan:
    batches: list[ExecutionBatch]
    actions: list[TaskAction]
    skipped: list[SkippedTask]
    completed: list[str]
    limit: int | None


@dataclass(frozen=True)
class TaskAction:
    task_id: str
    state: str
    action: str
    path: Path
    capability: str | None = None
    target_state: str | None = None
    repair: bool = False


@dataclass(frozen=True)
class AutopilotRunResult:
    run_id: str
    path: Path
    status: str
    pause_reason: str | None
    dispatched: list[str]
    actions: list[str]
    failed: list[str]
    blocked: list[str]
    cancelled: list[str]
    planned: list[str]
    skipped: list[SkippedTask]
    steps: int
    limit: int | None
    planner: str | None = None
    release: str | None = None
    release_status: str | None = None
    release_repair_planner: str | None = None
    releaser: str | None = None
    releaser_tasks: list[str] | None = None
    intake: str | None = None
    intake_status: str | None = None
    batch_executions: list[dict[str, Any]] | None = None


def build_execution_plan(root: Path, config: dict[str, Any], *, limit: int | None = None) -> ExecutionPlan:
    if limit is not None and limit < 1:
        raise ValueError("limit must be at least 1")

    records = iter_tasks(root, config)
    actions = _task_actions(root, config, records)
    completed = {
        str(record.task.get("id"))
        for record in records
        if _is_valid_completed_task(record)
    }
    remaining = [] if actions else [record for record in records if record.task.get("state") == "ready"]
    skipped: list[SkippedTask] = [
        SkippedTask(
            task_id=str(record.task.get("id", record.path.stem)),
            state=str(record.task.get("state")),
            path=record.path,
            reasons=[f"state is {record.task.get('state')}, not ready"],
        )
        for record in records
        if record.task.get("state") not in {"ready", *COMPLETED_STATES, *ACTIVE_STATES}
    ]
    for record in records:
        if record.task.get("state") not in {*ACTIVE_STATES, *COMPLETED_STATES}:
            continue
        errors = _task_validation_errors(record)
        if errors:
            skipped.append(_skipped(record, errors))
    batches: list[ExecutionBatch] = []

    while remaining:
        candidates: list[TaskRecord] = []
        waiting: list[tuple[TaskRecord, list[str]]] = []
        still_remaining: list[TaskRecord] = []

        for record in remaining:
            reasons = _non_dependency_skip_reasons(root, config, record)
            if reasons:
                skipped.append(_skipped(record, reasons))
                continue
            missing = _missing_dependencies(record, completed)
            if missing:
                waiting.append((record, [f"waiting for dependencies: {', '.join(missing)}"]))
                still_remaining.append(record)
                continue
            candidates.append(record)
            still_remaining.append(record)

        if not candidates:
            for record, reasons in waiting:
                skipped.append(_skipped(record, reasons))
            break

        selected: list[TaskRecord] = []
        reserved_write_files: set[str] = set()
        batch_limit = _resource_item_limit(config, limit)
        test_cost_budget = _resource_test_cost_budget(config)
        selected_test_cost = 0
        for record in sorted(candidates, key=_task_sort_key):
            if batch_limit is not None and len(selected) >= batch_limit:
                continue
            write_files = _write_files(record)
            if any(file_name in reserved_write_files for file_name in write_files):
                continue
            task_cost = _task_test_cost(record.task)
            if (
                test_cost_budget is not None
                and selected
                and selected_test_cost + task_cost > test_cost_budget
            ):
                continue
            selected.append(record)
            reserved_write_files.update(write_files)
            selected_test_cost += task_cost

        if not selected:
            for record in still_remaining:
                skipped.append(_skipped(record, ["could not select task for this dry-run batch"]))
            break

        selected_ids = {str(record.task["id"]) for record in selected}
        batches.append(
            ExecutionBatch(
                index=len(batches) + 1,
                tasks=[_planned_task(record) for record in selected],
            )
        )
        completed.update(selected_ids)
        remaining = [record for record in still_remaining if str(record.task.get("id")) not in selected_ids]

    return ExecutionPlan(
        batches=batches,
        actions=actions,
        skipped=sorted(skipped, key=lambda task: task.task_id),
        completed=sorted(completed),
        limit=limit,
    )


def run_autopilot(
    root: Path,
    config: dict[str, Any],
    *,
    limit: int | None = 1,
    max_steps: int = 1,
    actor_role: str = "orchestrator",
    resume_path: Path | None = None,
    goal: str | None = None,
) -> AutopilotRunResult:
    if limit is not None and limit < 1:
        raise ValueError("limit must be at least 1")
    if max_steps < 1:
        raise ValueError("max_steps must be at least 1")

    if resume_path:
        run_id, run_path, previous_metadata = _load_autopilot_run(root, resume_path)
    else:
        run_id, run_path = _create_autopilot_run(root, config)
        previous_metadata = {}
    dispatched: list[str] = list(previous_metadata.get("dispatched", [])) if isinstance(previous_metadata.get("dispatched"), list) else []
    actions: list[str] = list(previous_metadata.get("actions", [])) if isinstance(previous_metadata.get("actions"), list) else []
    planned: list[str] = list(previous_metadata.get("planned", [])) if isinstance(previous_metadata.get("planned"), list) else []
    failed: list[str] = []
    blocked: list[str] = []
    previous_cancelled = previous_metadata.get("cancelled", [])
    cancelled: list[str] = [str(item) for item in previous_cancelled] if isinstance(previous_cancelled, list) else []
    pause_reason: str | None = None
    run_goal = str(previous_metadata.get("goal") or goal or "").strip() or None
    planner = str(previous_metadata.get("planner")) if previous_metadata.get("planner") else None
    intake = str(previous_metadata.get("intake")) if previous_metadata.get("intake") else None
    intake_status = str(previous_metadata.get("intake_status")) if previous_metadata.get("intake_status") else None
    release = str(previous_metadata.get("release")) if previous_metadata.get("release") else None
    release_status = _previous_release_status(root, previous_metadata, release)
    release_repair_planner = (
        str(previous_metadata.get("release_repair_planner"))
        if previous_metadata.get("release_repair_planner")
        else None
    )
    releaser = str(previous_metadata.get("releaser")) if previous_metadata.get("releaser") else None
    previous_releaser_tasks = previous_metadata.get("releaser_tasks", [])
    releaser_tasks = [str(task_id) for task_id in previous_releaser_tasks] if isinstance(previous_releaser_tasks, list) else []
    previous_skipped = previous_metadata.get("skipped", [])
    skipped_payload = previous_skipped if isinstance(previous_skipped, list) else []
    previous_batch_executions = previous_metadata.get("batch_executions", [])
    batch_executions = previous_batch_executions if isinstance(previous_batch_executions, list) else []
    latest_skipped: list[SkippedTask] = []
    steps_executed = 0
    started_at = str(previous_metadata.get("started_at") or datetime.now(timezone.utc).isoformat())
    previous_loop_cycles = (
        int(previous_metadata.get("loop_cycles", 0))
        if isinstance(previous_metadata.get("loop_cycles", 0), int)
        else 0
    )
    resume_count = (
        int(previous_metadata.get("resume_count", 0))
        if isinstance(previous_metadata.get("resume_count", 0), int)
        else 0
    )
    if resume_path:
        resume_count += 1
    _write_autopilot_metadata(
        run_path,
        {
            "schema_version": 1,
            "state_machine": _state_machine_payload("in_progress"),
            "run_id": run_id,
            "started_at": started_at,
            "ended_at": None,
            "status": "in_progress",
            "pause_reason": None,
            "parameters": {"limit": limit, "max_steps": max_steps, "actor_role": actor_role},
            "goal": run_goal,
            "steps": int(previous_metadata.get("steps", 0)) if isinstance(previous_metadata.get("steps", 0), int) else 0,
            "actions": actions,
            "intake": intake,
            "intake_status": intake_status,
            "planned": planned,
            "planner": planner,
            "dispatched": dispatched,
            "failed": [],
            "blocked": [],
            "cancelled": cancelled,
            "cancellation": _autopilot_cancel_payload(run_path),
            "skipped": skipped_payload,
            "batch_executions": batch_executions,
            "release": release,
            "release_status": release_status,
            "release_repair_planner": release_repair_planner,
            "releaser": releaser,
            "releaser_tasks": releaser_tasks,
            "loop_cycles": previous_loop_cycles,
            "resume_count": resume_count,
        },
    )
    _append_autopilot_event(
        run_path,
        "autopilot_resumed" if resume_path else "autopilot_started",
        data={"limit": limit, "max_steps": max_steps, "actor_role": actor_role},
    )
    _recover_autopilot_state(root, config, run_path)

    for step in range(1, max_steps + 1):
        if _autopilot_cancel_requested(run_path):
            if "autopilot" not in cancelled:
                cancelled.append("autopilot")
            _append_autopilot_event(run_path, "autopilot_cancelled", data={"step": step})
            break
        if run_goal and not planned and not planner:
            if not intake and is_capability_configured(config, "intake"):
                steps_executed += 1
                actions.append("autopilot:intake")
                _append_autopilot_event(run_path, "intake_started", data={"step": step, "goal": run_goal})
                status, intake_path = _run_intake_action(root, config, run_path, run_goal, step)
                intake = intake_path
                intake_status = status
                if status == "failed":
                    failed.append("intake")
                    break
                if status == "blocked":
                    blocked.append("intake")
                    break
                continue
            steps_executed += 1
            actions.append("autopilot:plan")
            _append_autopilot_event(run_path, "planner_started", data={"step": step, "goal": run_goal})
            status, planner_path, planned_ids = _run_planner_action(root, config, run_path, run_goal, step)
            planner = planner_path
            planned.extend(planned_ids)
            if status == "failed":
                failed.append("planner")
                break
            continue

        active_actions = _select_task_actions(root, config, limit=limit)
        if active_actions:
            steps_executed += 1
            _append_autopilot_event(
                run_path,
                "active_actions_planned",
                data={
                    "step": step,
                    "tasks": [action.task_id for action in active_actions],
                    "actions": [_action_label(action) for action in active_actions],
                },
            )
            stop_after_action = False
            for action in active_actions:
                action_label = _action_label(action)
                actions.append(action_label)
                _append_autopilot_event(
                    run_path,
                    "task_action_planned",
                    task_id=action.task_id,
                    data={
                        "step": step,
                        "state": action.state,
                        "action": action.action,
                        "capability": action.capability,
                        "target_state": action.target_state,
                    },
                )
                status = _execute_task_action(root, config, run_path, action, step)
                if status == "failed":
                    if _request_repair(root, config, run_path, action, step, reason="action failed"):
                        continue
                    failed.append(action.task_id)
                    stop_after_action = True
                    break
                if status == "blocked":
                    blocked.append(action.task_id)
                    stop_after_action = True
                    break
                if status == "pending":
                    pause_reason = "external_status_pending"
                    stop_after_action = True
                    break
            if stop_after_action or pause_reason:
                break
            continue

        plan = build_execution_plan(root, config, limit=limit)
        latest_skipped = plan.skipped
        skipped_payload = [_skipped_json(task) for task in latest_skipped]
        if not plan.batches:
            if _release_configured(config) and not _release_complete(release_status) and _all_tasks_completed(root, config):
                done_task_ids = _completed_task_ids(root, config)
                if _releaser_needed(root, config, releaser, releaser_tasks, done_task_ids):
                    steps_executed += 1
                    actions.append("autopilot:releaser")
                    _append_autopilot_event(run_path, "releaser_started", data={"step": step, "done_tasks": done_task_ids})
                    status, releaser_path = _run_releaser_action(root, config, run_path, done_task_ids, step)
                    releaser = releaser_path
                    releaser_tasks = done_task_ids if releaser_path else releaser_tasks
                    if status == "failed":
                        failed.append("releaser")
                        break
                    if status == "blocked":
                        blocked.append("releaser")
                        break
                    continue
                steps_executed += 1
                actions.append("autopilot:release_status")
                _append_autopilot_event(run_path, "release_started", data={"step": step})
                status, release_path, provider_status = _run_release_action(
                    root,
                    config,
                    run_path,
                    step,
                    release_handoff=releaser,
                    release_handoff_tasks=releaser_tasks,
                )
                release = release_path
                release_status = provider_status
                if status == "failed":
                    repair_status, repair_planner_path, repair_task_ids = _run_release_repair_planner_action(
                        root,
                        config,
                        run_path,
                        release_path,
                        provider_status,
                        step,
                        release_handoff=releaser,
                    )
                    if repair_status == "passed":
                        actions.append("autopilot:release_repair_plan")
                        release_repair_planner = repair_planner_path
                        planned.extend(task_id for task_id in repair_task_ids if task_id not in planned)
                        continue
                    failed.append("release")
                    break
                if status == "blocked":
                    blocked.append("release")
                    break
                if status == "pending":
                    pause_reason = "external_status_pending"
                    break
                continue
            for task_id in _blocked_skipped_task_ids(plan):
                if task_id not in blocked:
                    blocked.append(task_id)
            _append_autopilot_event(
                run_path,
                "no_executable_batch",
                data={"step": step, "skipped": [_skipped_json(task) for task in plan.skipped]},
            )
            break

        batch = plan.batches[0]
        steps_executed += 1
        _append_autopilot_event(
            run_path,
            "batch_planned",
            data={
                "step": step,
                "batch_index": batch.index,
                "tasks": [task.task_id for task in batch.tasks],
            },
        )

        batch_result = _dispatch_task_batch(root, config, run_path, batch, step, actor_role)
        batch_executions.append(batch_result)
        for task_result in batch_result["tasks"]:
            task_id = str(task_result["task_id"])
            status = str(task_result["status"])
            if status == "dispatched":
                if task_id not in dispatched:
                    dispatched.append(task_id)
                _append_autopilot_event(
                    run_path,
                    "task_dispatched",
                    task_id=task_id,
                    task_run_id=task_result.get("run_id"),
                    data={
                        "step": step,
                        "session_id": task_result.get("session_id"),
                        "session_status": task_result.get("session_status"),
                        "batch_index": batch.index,
                    },
                )
            elif status == "blocked":
                if task_id not in blocked:
                    blocked.append(task_id)
                _append_autopilot_event(
                    run_path,
                    "task_dispatch_blocked",
                    task_id=task_id,
                    task_run_id=task_result.get("run_id"),
                    data={
                        "step": step,
                        "session_status": task_result.get("session_status"),
                        "summary": task_result.get("summary"),
                        "batch_index": batch.index,
                    },
                )
            elif status == "failed":
                if task_id not in failed:
                    failed.append(task_id)
                _append_autopilot_event(
                    run_path,
                    "task_dispatch_failed",
                    task_id=task_id,
                    task_run_id=task_result.get("run_id"),
                    data={
                        "step": step,
                        "session_status": task_result.get("session_status"),
                        "error": task_result.get("error"),
                        "batch_index": batch.index,
                    },
                )
            elif status == "cancelled":
                if task_id not in cancelled:
                    cancelled.append(task_id)
                _append_autopilot_event(
                    run_path,
                    "task_dispatch_cancelled",
                    task_id=task_id,
                    task_run_id=task_result.get("run_id"),
                    data={
                        "step": step,
                        "session_status": task_result.get("session_status"),
                        "summary": task_result.get("summary"),
                        "batch_index": batch.index,
                    },
                )
        if pause_reason:
            break

    final_status, pause_reason = _final_autopilot_status(
        root,
        config,
        limit=limit,
        max_steps=max_steps,
        steps_executed=steps_executed,
        failed=failed,
        blocked=blocked,
        cancelled=cancelled,
        pause_reason=pause_reason,
        release_status=release_status,
        run_goal=run_goal,
        planned=planned,
        planner=planner,
        intake_status=intake_status,
    )
    _write_autopilot_metadata(
        run_path,
        {
            "schema_version": 1,
            "state_machine": _state_machine_payload(final_status),
            "run_id": run_id,
            "started_at": started_at,
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "status": final_status,
            "pause_reason": pause_reason,
            "parameters": {"limit": limit, "max_steps": max_steps, "actor_role": actor_role},
            "goal": run_goal,
            "steps": (int(previous_metadata.get("steps", 0)) if isinstance(previous_metadata.get("steps", 0), int) else 0)
            + steps_executed,
            "actions": actions,
            "intake": intake,
            "intake_status": intake_status,
            "planned": planned,
            "planner": planner,
            "dispatched": dispatched,
            "failed": failed,
            "blocked": blocked,
            "cancelled": cancelled,
            "cancellation": _autopilot_cancel_payload(run_path),
            "skipped": skipped_payload,
            "batch_executions": batch_executions,
            "release": release,
            "release_status": release_status,
            "release_repair_planner": release_repair_planner,
            "releaser": releaser,
            "releaser_tasks": releaser_tasks,
            "loop_cycles": previous_loop_cycles,
            "resume_count": resume_count,
        },
    )
    _append_autopilot_event(
        run_path,
        "autopilot_finished",
        data={
            "steps": steps_executed,
            "status": final_status,
            "actions": actions,
            "planned": planned,
            "dispatched": dispatched,
            "failed": failed,
            "blocked": blocked,
            "cancelled": cancelled,
            "pause_reason": pause_reason,
        },
    )
    return AutopilotRunResult(
        run_id=run_id,
        path=run_path,
        status=final_status,
        pause_reason=pause_reason,
        dispatched=dispatched,
        actions=actions,
        failed=failed,
        blocked=blocked,
        cancelled=cancelled,
        planned=planned,
        skipped=latest_skipped,
        steps=(int(previous_metadata.get("steps", 0)) if isinstance(previous_metadata.get("steps", 0), int) else 0)
        + steps_executed,
        limit=limit,
        planner=planner,
        release=release,
        release_status=release_status,
        release_repair_planner=release_repair_planner,
        releaser=releaser,
        releaser_tasks=releaser_tasks,
        intake=intake,
        intake_status=intake_status,
        batch_executions=batch_executions,
    )


def _recover_autopilot_state(root: Path, config: dict[str, Any], run_path: Path) -> None:
    _recover_orphan_task_runs(root, config, run_path)
    _recover_missing_worktrees(root, config, run_path)
    _recover_stale_locks(root, config, run_path)


def _recover_orphan_task_runs(root: Path, config: dict[str, Any], run_path: Path) -> None:
    for record in list(iter_tasks(root, config)):
        if record.task.get("state") not in ACTIVE_STATES:
            continue
        evidence = record.task.get("evidence", {})
        run_id = evidence.get("run_id") if isinstance(evidence, dict) else None
        if run_id and (root / str(config.get("paths", {}).get("runs", "harness/runs")) / str(run_id)).exists():
            continue
        task_id = str(record.task.get("id", record.path.stem))
        if not _task_has_locks(root, config, task_id):
            continue
        release_locks_for_task(root, config, task_id)
        updated = dict(record.task)
        updated["state"] = "ready"
        updated_evidence = dict(evidence) if isinstance(evidence, dict) else {}
        for key in ("run_id", "session", "packet", "red", "green", "verify", "worktree"):
            if key in updated_evidence:
                updated_evidence[key] = None
        updated_evidence.pop("capabilities", None)
        updated_evidence.pop("autopilot", None)
        updated["evidence"] = updated_evidence
        target = task_root(root, config) / "ready" / f"{task_id}.json"
        dump_data(updated, target)
        if record.path != target and record.path.exists():
            record.path.unlink()
        _append_autopilot_event(
            run_path,
            "recovery_orphan_run_requeued",
            task_id=task_id,
            data={"missing_run_id": run_id, "from_state": record.task.get("state")},
        )


def _task_has_locks(root: Path, config: dict[str, Any], task_id: str) -> bool:
    lock_root = locks_root(root, config)
    task_lock = lock_root / "tasks" / f"{task_id}.lock"
    if task_lock.exists():
        return True
    files_dir = lock_root / "files"
    if not files_dir.exists():
        return False
    for path in files_dir.glob("*.lock"):
        if path.read_text(encoding="utf-8").strip() == task_id:
            return True
    return False


def _recover_missing_worktrees(root: Path, config: dict[str, Any], run_path: Path) -> None:
    for record in list(iter_tasks(root, config)):
        if record.task.get("state") not in ACTIVE_STATES:
            continue
        task_run_path = _task_run_path(root, config, record.task)
        if not task_run_path:
            continue
        metadata_path = task_run_path / "metadata.yml"
        if not metadata_path.exists():
            continue
        metadata = load_data(metadata_path)
        workspace = metadata.get("workspace", {})
        worktree = workspace.get("worktree") if isinstance(workspace, dict) else None
        if not worktree or Path(str(worktree)).exists():
            continue
        workspace = dict(workspace)
        workspace["root"] = str(root)
        workspace["worktree"] = None
        workspace["worktree_recovered_at"] = datetime.now(timezone.utc).isoformat()
        metadata["workspace"] = workspace
        dump_data(metadata, metadata_path)
        updated = dict(record.task)
        evidence = dict(updated.get("evidence", {})) if isinstance(updated.get("evidence"), dict) else {}
        if evidence.get("worktree"):
            evidence["worktree"] = None
            updated["evidence"] = evidence
            dump_data(updated, record.path)
        _append_autopilot_event(
            run_path,
            "recovery_orphan_worktree_reset",
            task_id=str(record.task.get("id", record.path.stem)),
            data={"worktree": str(worktree), "run_path": str(task_run_path.relative_to(root))},
        )


def _recover_stale_locks(root: Path, config: dict[str, Any], run_path: Path) -> None:
    active_records = {
        str(record.task.get("id")): record
        for record in iter_tasks(root, config)
        if record.task.get("state") in ACTIVE_STATES
    }
    active_run_ids = {
        str(record.task.get("evidence", {}).get("run_id"))
        for record in active_records.values()
        if isinstance(record.task.get("evidence"), dict) and record.task.get("evidence", {}).get("run_id")
    }
    lock_root = locks_root(root, config)
    task_locks = lock_root / "tasks"
    if task_locks.exists():
        for path in task_locks.glob("*.lock"):
            task_id = path.stem
            lock_run_id = path.read_text(encoding="utf-8").strip()
            if task_id in active_records and lock_run_id in active_run_ids:
                continue
            path.unlink()
            _append_autopilot_event(
                run_path,
                "recovery_stale_lock_released",
                task_id=task_id,
                data={"lock": str(path.relative_to(root)), "owner": lock_run_id},
            )
    file_locks = lock_root / "files"
    if file_locks.exists():
        for path in file_locks.glob("*.lock"):
            owner_task_id = path.read_text(encoding="utf-8").strip()
            if owner_task_id in active_records:
                continue
            path.unlink()
            _append_autopilot_event(
                run_path,
                "recovery_stale_lock_released",
                task_id=owner_task_id or None,
                data={"lock": str(path.relative_to(root)), "owner": owner_task_id},
            )


def _final_autopilot_status(
    root: Path,
    config: dict[str, Any],
    *,
    limit: int | None,
    max_steps: int,
    steps_executed: int,
    failed: list[str],
    blocked: list[str],
    cancelled: list[str],
    pause_reason: str | None,
    release_status: str | None,
    run_goal: str | None = None,
    planned: list[str] | None = None,
    planner: str | None = None,
    intake_status: str | None = None,
) -> tuple[str, str | None]:
    if cancelled:
        return "cancelled", None
    if failed:
        return "failed", None
    if blocked:
        return "blocked", None
    if pause_reason:
        return "paused", pause_reason
    if steps_executed >= max_steps and (
        _has_more_goal_planning(run_goal, planned or [], planner, intake_status)
        or _has_more_autopilot_work(root, config, limit=limit, release_status=release_status)
    ):
        return "paused", "max_steps_reached"
    return "finished", None


def _has_more_goal_planning(
    run_goal: str | None,
    planned: list[str],
    planner: str | None,
    intake_status: str | None,
) -> bool:
    if not run_goal or planned or planner:
        return False
    return intake_status in {None, "passed"}


def _dispatch_task_batch(
    root: Path,
    config: dict[str, Any],
    run_path: Path,
    batch: ExecutionBatch,
    step: int,
    actor_role: str,
) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc).isoformat()
    log_path = run_path / f"batch-{step}-{batch.index}-dispatch.jsonl"
    cancel_path = run_path / "cancel.json"
    log_lock = threading.Lock()
    prepared_runs: dict[str, Path] = {}
    prepared_failures: dict[str, dict[str, Any]] = {}
    for task in batch.tasks:
        _append_autopilot_event(
            run_path,
            "task_started",
            task_id=task.task_id,
            data={"step": step, "batch_index": batch.index},
        )
        _append_batch_log(log_path, log_lock, "task_queued", task.task_id, {"step": step, "batch_index": batch.index})
        try:
            task_run = start_task(root, config, task.task_id, actor_role=actor_role, launch_session=False)
            prepared_runs[task.task_id] = task_run.path
            _append_batch_log(
                log_path,
                log_lock,
                "task_prepared",
                task.task_id,
                {"step": step, "batch_index": batch.index, "run_id": task_run.run_id},
            )
        except Exception as exc:
            prepared_failures[task.task_id] = {
                "task_id": task.task_id,
                "status": "failed",
                "error": str(exc),
                "run_id": None,
                "session_id": None,
                "session_status": None,
                "summary": None,
                "started_at": started_at,
                "ended_at": datetime.now(timezone.utc).isoformat(),
            }
            _append_batch_log(log_path, log_lock, "task_result", task.task_id, prepared_failures[task.task_id])

    max_workers = max(1, len(prepared_runs))
    results_by_task: dict[str, dict[str, Any]] = dict(prepared_failures)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _dispatch_one_task,
                root,
                config,
                task,
                prepared_runs[task.task_id],
                cancel_path,
                actor_role,
                log_path,
                log_lock,
                step,
                batch.index,
            ): task
            for task in batch.tasks
            if task.task_id in prepared_runs
        }
        for future in as_completed(futures):
            task = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # pragma: no cover - defensive for unexpected worker failures.
                result = {
                    "task_id": task.task_id,
                    "status": "failed",
                    "error": str(exc),
                    "run_id": None,
                    "session_id": None,
                    "session_status": None,
                    "summary": None,
                    "started_at": None,
                    "ended_at": datetime.now(timezone.utc).isoformat(),
                }
            results_by_task[task.task_id] = result

    ordered_results = [results_by_task[task.task_id] for task in batch.tasks if task.task_id in results_by_task]
    statuses = {str(item["status"]) for item in ordered_results}
    if "cancelled" in statuses:
        status = "cancelled"
    elif "failed" in statuses:
        status = "failed"
    elif "blocked" in statuses:
        status = "blocked"
    else:
        status = "passed"
    ended_at = datetime.now(timezone.utc).isoformat()
    merge_queue = [
        str(item["task_id"])
        for item in ordered_results
        if item.get("status") == "dispatched" and item.get("run_id")
    ]
    return {
        "schema_version": 1,
        "mode": "concurrent",
        "batch_index": batch.index,
        "step": step,
        "status": status,
        "started_at": started_at,
        "ended_at": ended_at,
        "log": str(log_path.relative_to(run_path)),
        "heartbeat": str(log_path.relative_to(run_path)),
        "resource_budget": {"max_workers": max_workers, "task_count": len(batch.tasks)},
        "merge_queue": merge_queue,
        "tasks": ordered_results,
    }


def _dispatch_one_task(
    root: Path,
    config: dict[str, Any],
    task: PlannedTask,
    task_run_path: Path,
    cancel_path: Path,
    actor_role: str,
    log_path: Path,
    log_lock: threading.Lock,
    step: int,
    batch_index: int,
) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc).isoformat()
    _append_batch_log(log_path, log_lock, "task_heartbeat", task.task_id, {"step": step, "batch_index": batch_index, "status": "starting"})
    try:
        if cancel_path.exists():
            raise _TaskDispatchCancelled("cancel requested before session launch")
        launched = launch_agent_session(root, config, task_run_path, cancel_path=cancel_path)
        session = load_data(launched.path)
    except _TaskDispatchCancelled as exc:
        ended_at = datetime.now(timezone.utc).isoformat()
        result = {
            "task_id": task.task_id,
            "status": "cancelled",
            "error": str(exc),
            "run_id": task_run_path.name,
            "session_id": None,
            "session_status": "cancelled",
            "summary": str(exc),
            "started_at": started_at,
            "ended_at": ended_at,
        }
        _append_batch_log(log_path, log_lock, "task_result", task.task_id, result)
        return result
    except Exception as exc:
        ended_at = datetime.now(timezone.utc).isoformat()
        result = {
            "task_id": task.task_id,
            "status": "failed",
            "error": str(exc),
            "run_id": None,
            "session_id": None,
            "session_status": None,
            "summary": None,
            "started_at": started_at,
            "ended_at": ended_at,
        }
        _append_batch_log(log_path, log_lock, "task_result", task.task_id, result)
        return result

    session_status = str(session.get("status"))
    if session_status in {"prepared", "launched"}:
        status = "dispatched"
        error = None
    elif session_status.endswith("_cancelled"):
        status = "cancelled"
        error = str(session.get("failure") or "cancelled")
    elif session_status == "blocked":
        status = "blocked"
        error = None
        block_task(
            root,
            config,
            task.task_id,
            reason=str(session.get("summary") or session.get("failure") or "agent session blocked"),
            unblock_condition="Resolve the agent session prerequisite, then unblock and dispatch the task again.",
            owner="user",
            blocker_type="agent_session",
            source="session:launch",
        )
    else:
        status = "failed"
        error = str(session.get("failure") or session.get("summary") or session_status)
    ended_at = datetime.now(timezone.utc).isoformat()
    result = {
        "task_id": task.task_id,
        "status": status,
        "error": error,
        "run_id": str(session.get("run_id")),
        "session_id": session.get("session_id"),
        "session_status": session_status,
        "summary": session.get("summary") or session.get("failure"),
        "started_at": started_at,
        "ended_at": ended_at,
    }
    _append_batch_log(log_path, log_lock, "task_result", task.task_id, result)
    return result


class _TaskDispatchCancelled(Exception):
    pass


def _append_batch_log(path: Path, lock: threading.Lock, event: str, task_id: str, data: dict[str, Any]) -> None:
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "task_id": task_id,
        "data": data,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with lock:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _has_more_autopilot_work(
    root: Path,
    config: dict[str, Any],
    *,
    limit: int | None,
    release_status: str | None,
) -> bool:
    if _select_task_action(root, config):
        return True
    plan = build_execution_plan(root, config, limit=limit)
    if plan.batches:
        return True
    return _release_configured(config) and not _release_complete(release_status) and _all_tasks_completed(root, config)


def _blocked_skipped_task_ids(plan: ExecutionPlan) -> list[str]:
    return [task.task_id for task in plan.skipped]


def _non_dependency_skip_reasons(root: Path, config: dict[str, Any], record: TaskRecord) -> list[str]:
    reasons = _task_validation_errors(record)
    locked = [file_name for file_name in _write_files(record) if file_lock_path(root, config, file_name).exists()]
    if locked:
        reasons.append(f"write scope locked: {', '.join(locked)}")
    return reasons


def _task_validation_errors(record: TaskRecord) -> list[str]:
    return validate_task(record.task, directory_state=record.path.parent.name)


def _is_valid_completed_task(record: TaskRecord) -> bool:
    return record.task.get("state") in COMPLETED_STATES and not _task_validation_errors(record)


def _missing_dependencies(record: TaskRecord, completed: set[str]) -> list[str]:
    dependencies = record.task.get("dependencies", [])
    if not isinstance(dependencies, list):
        return []
    return [str(dep) for dep in dependencies if str(dep) not in completed]


def _write_files(record: TaskRecord) -> list[str]:
    files = record.task.get("files", {})
    write_files = files.get("write", []) if isinstance(files, dict) else []
    return [normalize_file_path(str(file_name)) for file_name in write_files]


def _task_sort_key(record: TaskRecord) -> tuple[int, str]:
    return (int(record.task.get("priority", 999)), str(record.task["id"]))


def _resource_item_limit(config: dict[str, Any], limit: int | None) -> int | None:
    values = [limit] if limit is not None else []
    model_concurrency = _resource_positive_int(config, "model_concurrency")
    if model_concurrency is not None:
        values.append(model_concurrency)
    return min(values) if values else None


def _resource_test_cost_budget(config: dict[str, Any]) -> int | None:
    return _resource_positive_int(config, "max_test_cost")


def _resource_ci_queue_limit(config: dict[str, Any]) -> int | None:
    return _resource_positive_int(config, "ci_queue")


def _resource_positive_int(config: dict[str, Any], key: str) -> int | None:
    autopilot = config.get("autopilot", {})
    resources: Any = {}
    if isinstance(autopilot, dict):
        resources = autopilot.get("resources", autopilot.get("resource_budget", {}))
    if not isinstance(resources, dict):
        return None
    value = resources.get(key)
    return int(value) if isinstance(value, int) and value > 0 else None


def _task_test_cost(task: dict[str, Any]) -> int:
    estimate = task.get("estimate", {})
    if isinstance(estimate, dict):
        value = estimate.get("test_cost")
        if isinstance(value, int) and value > 0:
            return value
    return 1


def _planned_task(record: TaskRecord) -> PlannedTask:
    return PlannedTask(
        task_id=str(record.task["id"]),
        title=str(record.task.get("title", "")),
        priority=int(record.task.get("priority", 999)),
        path=record.path,
        write_files=_write_files(record),
        dependencies=[str(dep) for dep in record.task.get("dependencies", []) if str(dep)],
    )


def _skipped(record: TaskRecord, reasons: list[str]) -> SkippedTask:
    return SkippedTask(
        task_id=str(record.task.get("id", record.path.stem)),
        state=str(record.task.get("state")),
        path=record.path,
        reasons=reasons,
    )


def _select_task_action(root: Path, config: dict[str, Any]) -> TaskAction | None:
    actions = _select_task_actions(root, config, limit=1)
    return actions[0] if actions else None


def _select_task_actions(root: Path, config: dict[str, Any], *, limit: int | None) -> list[TaskAction]:
    selected: list[TaskAction] = []
    reserved_write_files: set[str] = set()
    action_limit = _resource_item_limit(config, limit)
    ci_queue_limit = _resource_ci_queue_limit(config)
    ci_actions = 0
    test_cost_budget = _resource_test_cost_budget(config)
    selected_test_cost = 0
    for action in _task_actions(root, config, iter_tasks(root, config)):
        if action_limit is not None and len(selected) >= action_limit:
            break
        if action.action == "ci_status" and ci_queue_limit is not None and ci_actions >= ci_queue_limit:
            continue
        write_files = _task_action_write_files(action)
        if any(file_name in reserved_write_files for file_name in write_files):
            continue
        task = _load_action_task(action)
        action_cost = _task_test_cost(task) if task else 1
        if (
            test_cost_budget is not None
            and selected
            and selected_test_cost + action_cost > test_cost_budget
        ):
            continue
        selected.append(action)
        reserved_write_files.update(write_files)
        selected_test_cost += action_cost
        if action.action == "ci_status":
            ci_actions += 1
    return selected


def _task_action_write_files(action: TaskAction) -> list[str]:
    task = _load_action_task(action)
    if not task:
        return []
    files = task.get("files", {}) if isinstance(task, dict) else {}
    write_files = files.get("write", []) if isinstance(files, dict) else []
    return [normalize_file_path(str(file_name)) for file_name in write_files]


def _load_action_task(action: TaskAction) -> dict[str, Any] | None:
    try:
        task = load_data(action.path)
    except ValueError:
        return None
    return task if isinstance(task, dict) else None


def _task_actions(root: Path, config: dict[str, Any], records: list[TaskRecord]) -> list[TaskAction]:
    actions: list[TaskAction] = []
    active = [
        record
        for record in records
        if record.task.get("state") in ACTIVE_STATES
    ]
    for record in sorted(active, key=_task_sort_key):
        errors = _task_validation_errors(record)
        if errors:
            continue
        action = _next_task_action(root, config, record)
        if action:
            actions.append(action)
    return actions


def _next_task_action(root: Path, config: dict[str, Any], record: TaskRecord) -> TaskAction | None:
    task_id = str(record.task["id"])
    state = str(record.task.get("state"))
    repair = _pending_repair(record.task)
    if repair and state in {"in_progress", "review"}:
        target_capability = str(repair.get("target_capability") or "implementer")
        return TaskAction(
            task_id=task_id,
            state=state,
            action="run_capability",
            path=record.path,
            capability=target_capability,
            repair=True,
        )

    capabilities = _passed_capabilities(root, record.task)
    if state == "in_progress":
        for capability in ("bdd", "tdd", "implementer"):
            if capability not in capabilities:
                return TaskAction(
                    task_id=task_id,
                    state=state,
                    action="run_capability",
                    path=record.path,
                    capability=capability,
                )
        return TaskAction(task_id=task_id, state=state, action="transition", path=record.path, target_state="review")
    if state == "review":
        if "reviewer" not in capabilities:
            return TaskAction(
                task_id=task_id,
                state=state,
                action="run_capability",
                path=record.path,
                capability="reviewer",
            )
        if is_capability_configured(config, "verifier") and "verifier" not in capabilities:
            return TaskAction(
                task_id=task_id,
                state=state,
                action="run_capability",
                path=record.path,
                capability="verifier",
            )
        return TaskAction(task_id=task_id, state=state, action="verify_task", path=record.path, target_state="verified")
    if state == "verified":
        return TaskAction(task_id=task_id, state=state, action="transition", path=record.path, target_state="accepted")
    if state == "accepted":
        if _worktree_needs_apply(root, config, record.task):
            return TaskAction(task_id=task_id, state=state, action="apply_worktree", path=record.path)
        if _git_configured(config) and not _passed_git_evidence(root, record.task):
            return TaskAction(task_id=task_id, state=state, action="publish_changes", path=record.path)
        if _pr_configured(config) and not _passed_pr_request_evidence(root, record.task):
            return TaskAction(task_id=task_id, state=state, action="pr_ensure", path=record.path)
        if _ci_configured(config) and not _passed_ci_evidence(root, record.task):
            return TaskAction(task_id=task_id, state=state, action="ci_status", path=record.path)
        if (
            _pr_auto_merge_enabled(config)
            and not _passed_pr_merge_evidence(root, record.task)
            and _pr_request_ready_for_merge(root, record.task)
        ):
            return TaskAction(task_id=task_id, state=state, action="pr_merge", path=record.path)
        if _pr_configured(config) and not _passed_pr_evidence(root, record.task):
            return TaskAction(task_id=task_id, state=state, action="pr_status", path=record.path)
        return TaskAction(task_id=task_id, state=state, action="close_task", path=record.path, target_state="done")
    return None


def _capability_evidence(task: dict[str, Any]) -> dict[str, Any]:
    evidence = task.get("evidence", {})
    if not isinstance(evidence, dict):
        return {}
    capabilities = evidence.get("capabilities", {})
    return capabilities if isinstance(capabilities, dict) else {}


def _passed_capabilities(root: Path, task: dict[str, Any]) -> set[str]:
    passed: set[str] = set()
    for capability, path_value in _capability_evidence(task).items():
        output_path = root / str(path_value)
        if not output_path.exists():
            continue
        try:
            output = load_data(output_path)
        except ValueError:
            continue
        if output.get("status") == "passed":
            passed.add(str(capability))
    return passed


def _pending_repair(task: dict[str, Any]) -> dict[str, Any] | None:
    evidence = task.get("evidence", {})
    if not isinstance(evidence, dict):
        return None
    autopilot = evidence.get("autopilot", {})
    if not isinstance(autopilot, dict):
        return None
    repair = autopilot.get("repair", {})
    if isinstance(repair, dict) and repair.get("status") == "pending":
        return repair
    return None


def _ci_configured(config: dict[str, Any]) -> bool:
    integrations = config.get("integrations", {})
    ci_provider = integrations.get("ci_provider") if isinstance(integrations, dict) else None
    return isinstance(ci_provider, dict) and bool(ci_provider.get("provider") or ci_provider.get("command"))


def _git_configured(config: dict[str, Any]) -> bool:
    integrations = config.get("integrations", {})
    git_provider = integrations.get("git_provider") if isinstance(integrations, dict) else None
    return isinstance(git_provider, dict) and bool(git_provider.get("provider") or git_provider.get("command"))


def _passed_git_evidence(root: Path, task: dict[str, Any]) -> bool:
    evidence = task.get("evidence", {})
    if not isinstance(evidence, dict) or not evidence.get("git"):
        return False
    output_path = root / str(evidence["git"])
    if not output_path.exists():
        return False
    try:
        output = load_data(output_path)
    except ValueError:
        return False
    return output.get("status") in {"published", "skipped"}


def _passed_ci_evidence(root: Path, task: dict[str, Any]) -> bool:
    evidence = task.get("evidence", {})
    if not isinstance(evidence, dict) or not evidence.get("ci"):
        return False
    output_path = root / str(evidence["ci"])
    if not output_path.exists():
        return False
    try:
        output = load_data(output_path)
    except ValueError:
        return False
    return output.get("status") in {"passed", "skipped"}


def _pr_configured(config: dict[str, Any]) -> bool:
    integrations = config.get("integrations", {})
    pr_provider = integrations.get("pr_provider") if isinstance(integrations, dict) else None
    return isinstance(pr_provider, dict) and bool(pr_provider.get("provider") or pr_provider.get("command"))


def _pr_auto_merge_enabled(config: dict[str, Any]) -> bool:
    integrations = config.get("integrations", {})
    pr_provider = integrations.get("pr_provider") if isinstance(integrations, dict) else None
    if not isinstance(pr_provider, dict):
        return False
    options = pr_provider.get("provider_options", {})
    option_enabled = isinstance(options, dict) and options.get("auto_merge") is True
    return pr_provider.get("auto_merge") is True or option_enabled


def _passed_pr_evidence(root: Path, task: dict[str, Any]) -> bool:
    evidence = task.get("evidence", {})
    if not isinstance(evidence, dict) or not evidence.get("pr"):
        return False
    output_path = root / str(evidence["pr"])
    if not output_path.exists():
        return False
    try:
        output = load_data(output_path)
    except ValueError:
        return False
    return output.get("status") in {"merged", "skipped"}


def _passed_pr_merge_evidence(root: Path, task: dict[str, Any]) -> bool:
    evidence = task.get("evidence", {})
    if not isinstance(evidence, dict) or not evidence.get("pr_merge"):
        return False
    output_path = root / str(evidence["pr_merge"])
    if not output_path.exists():
        return False
    try:
        output = load_data(output_path)
    except ValueError:
        return False
    return output.get("status") in {"merged", "skipped"}


def _pr_request_ready_for_merge(root: Path, task: dict[str, Any]) -> bool:
    evidence = task.get("evidence", {})
    if not isinstance(evidence, dict) or not evidence.get("pr_request"):
        return False
    output_path = root / str(evidence["pr_request"])
    if not output_path.exists():
        return False
    try:
        output = load_data(output_path)
    except ValueError:
        return False
    return output.get("status") == "open"


def _passed_pr_request_evidence(root: Path, task: dict[str, Any]) -> bool:
    evidence = task.get("evidence", {})
    if not isinstance(evidence, dict) or not evidence.get("pr_request"):
        return False
    output_path = root / str(evidence["pr_request"])
    if not output_path.exists():
        return False
    try:
        output = load_data(output_path)
    except ValueError:
        return False
    return output.get("status") in {"open", "draft", "merged", "skipped"}


def _release_configured(config: dict[str, Any]) -> bool:
    integrations = config.get("integrations", {})
    release_provider = integrations.get("release_provider") if isinstance(integrations, dict) else None
    return isinstance(release_provider, dict) and bool(release_provider.get("provider") or release_provider.get("command"))


def _release_complete(release_status: str | None) -> bool:
    return release_status in {"released", "skipped"}


def _releaser_needed(
    root: Path,
    config: dict[str, Any],
    releaser: str | None,
    releaser_tasks: list[str],
    done_task_ids: list[str],
) -> bool:
    if not is_capability_configured(config, "releaser"):
        return False
    if releaser_tasks != done_task_ids:
        return True
    if not releaser:
        return True
    return not (root / releaser).exists()


def _previous_release_status(root: Path, previous_metadata: dict[str, Any], release: str | None) -> str | None:
    status = previous_metadata.get("release_status")
    if status:
        return str(status)
    if not release:
        return None
    output_path = root / release
    if not output_path.exists():
        return None
    try:
        output = load_data(output_path)
    except ValueError:
        return None
    return str(output.get("status")) if output.get("status") else None


def _all_tasks_completed(root: Path, config: dict[str, Any]) -> bool:
    records = iter_tasks(root, config)
    return bool(records) and all(_is_valid_completed_task(record) for record in records)


def _completed_task_ids(root: Path, config: dict[str, Any]) -> list[str]:
    task_ids: list[str] = []
    for record in iter_tasks(root, config):
        if record.task.get("state") not in COMPLETED_STATES:
            continue
        errors = _task_validation_errors(record)
        if errors:
            raise ValueError(f"invalid completed task {record.path}: {'; '.join(errors)}")
        task_ids.append(str(record.task.get("id")))
    return sorted(task_ids)


def _execute_task_action(root: Path, config: dict[str, Any], run_path: Path, action: TaskAction, step: int) -> str:
    try:
        if action.action == "run_capability" and action.capability:
            _append_autopilot_event(
                run_path,
                "capability_started",
                task_id=action.task_id,
                data={"step": step, "capability": action.capability},
            )
            result = run_task_capability(root, config, action.capability, action.task_id)
            status = str(result.output.get("status"))
            _append_autopilot_event(
                run_path,
                "capability_finished",
                task_id=action.task_id,
                data={
                    "step": step,
                    "capability": action.capability,
                    "status": status,
                    "run_path": str(result.run_path.relative_to(root)),
                },
            )
            if status == "passed" and action.repair:
                _finish_repair(root, config, run_path, action, step)
            return "passed" if status == "passed" else status
        if action.action == "transition" and action.target_state:
            transition_task(root, config, action.task_id, action.target_state)
            _append_autopilot_event(
                run_path,
                "task_transitioned",
                task_id=action.task_id,
                data={"step": step, "target_state": action.target_state},
            )
            return "passed"
        if action.action == "verify_task":
            result = verify_task(root, config, action.task_id)
            _append_autopilot_event(
                run_path,
                "task_verified",
                task_id=action.task_id,
                data={"step": step, "failed": result.failed},
            )
            if result.failed:
                return "failed"
            transition_task(root, config, action.task_id, "verified")
            _append_autopilot_event(
                run_path,
                "task_transitioned",
                task_id=action.task_id,
                data={"step": step, "target_state": "verified"},
            )
            return "passed"
        if action.action == "ci_status":
            result = run_ci_status(root, config)
            record = _find_task_record(root, config, action.task_id)
            updated = dict(record.task)
            evidence = dict(updated.get("evidence", {}))
            evidence["ci"] = str((result.run_path / "output.json").relative_to(root))
            updated["evidence"] = evidence
            dump_data(updated, record.path)
            _append_autopilot_event(
                run_path,
                "ci_status_finished",
                task_id=action.task_id,
                data={"step": step, "status": result.status, "run_path": str(result.run_path.relative_to(root))},
            )
            if result.status in {"passed", "skipped"}:
                return "passed"
            if result.status in EXTERNAL_PENDING_STATUSES:
                return "pending"
            if result.status == "blocked":
                return "blocked"
            return "failed"
        if action.action == "apply_worktree":
            result = _apply_worktree_action(root, config, action.task_id)
            _append_autopilot_event(
                run_path,
                "worktree_apply_finished",
                task_id=action.task_id,
                data={
                    "step": step,
                    "run_path": str(result["run_path"].relative_to(root)),
                    "commit_before": result["commit_before"],
                    "commit_after": result["commit_after"],
                    "applied_to_control": result["applied_to_control"],
                },
            )
            return "passed"
        if action.action == "publish_changes":
            result = run_git_publish(root, config, task_id=action.task_id)
            record = _find_task_record(root, config, action.task_id)
            updated = dict(record.task)
            evidence = dict(updated.get("evidence", {}))
            evidence["git"] = str((result.run_path / "output.json").relative_to(root))
            updated["evidence"] = evidence
            dump_data(updated, record.path)
            _append_autopilot_event(
                run_path,
                "git_publish_finished",
                task_id=action.task_id,
                data={"step": step, "status": result.status, "run_path": str(result.run_path.relative_to(root))},
            )
            if result.status == "blocked":
                return "blocked"
            return "passed" if result.status in {"published", "skipped"} else "failed"
        if action.action == "pr_status":
            result = run_pr_status(root, config, task_id=action.task_id)
            record = _find_task_record(root, config, action.task_id)
            updated = dict(record.task)
            evidence = dict(updated.get("evidence", {}))
            evidence["pr"] = str((result.run_path / "output.json").relative_to(root))
            updated["evidence"] = evidence
            dump_data(updated, record.path)
            _append_autopilot_event(
                run_path,
                "pr_status_finished",
                task_id=action.task_id,
                data={"step": step, "status": result.status, "run_path": str(result.run_path.relative_to(root))},
            )
            if result.status in EXTERNAL_PENDING_STATUSES:
                return "pending"
            if result.status in {"open", "draft", "blocked"}:
                return "blocked"
            return "passed" if result.status in {"merged", "skipped"} else "failed"
        if action.action == "pr_merge":
            result = run_pr_merge(root, config, task_id=action.task_id)
            record = _find_task_record(root, config, action.task_id)
            updated = dict(record.task)
            evidence = dict(updated.get("evidence", {}))
            evidence["pr_merge"] = str((result.run_path / "output.json").relative_to(root))
            updated["evidence"] = evidence
            dump_data(updated, record.path)
            _append_autopilot_event(
                run_path,
                "pr_merge_finished",
                task_id=action.task_id,
                data={"step": step, "status": result.status, "run_path": str(result.run_path.relative_to(root))},
            )
            if result.status in EXTERNAL_PENDING_STATUSES:
                return "pending"
            if result.status in {"open", "draft", "blocked"}:
                return "blocked"
            return "passed" if result.status in {"merged", "skipped"} else "failed"
        if action.action == "pr_ensure":
            result = run_pr_ensure(root, config, task_id=action.task_id)
            record = _find_task_record(root, config, action.task_id)
            updated = dict(record.task)
            evidence = dict(updated.get("evidence", {}))
            evidence["pr_request"] = str((result.run_path / "output.json").relative_to(root))
            updated["evidence"] = evidence
            dump_data(updated, record.path)
            _append_autopilot_event(
                run_path,
                "pr_ensure_finished",
                task_id=action.task_id,
                data={"step": step, "status": result.status, "run_path": str(result.run_path.relative_to(root))},
            )
            if result.status == "blocked":
                return "blocked"
            return "passed" if result.status in {"open", "draft", "merged", "skipped"} else "failed"
        if action.action == "close_task":
            close_task(root, config, action.task_id)
            _append_autopilot_event(
                run_path,
                "task_closed",
                task_id=action.task_id,
                data={"step": step},
            )
            return "passed"
    except Exception as exc:
        _append_autopilot_event(
            run_path,
            "task_action_failed",
            task_id=action.task_id,
            data={
                "step": step,
                "action": action.action,
                "capability": action.capability,
                "target_state": action.target_state,
                "error": str(exc),
            },
        )
        return "failed"
    _append_autopilot_event(
        run_path,
        "task_action_failed",
        task_id=action.task_id,
        data={"step": step, "action": action.action, "error": "unknown task action"},
    )
    return "failed"


def _action_label(action: TaskAction) -> str:
    if action.repair and action.capability:
        return f"{action.task_id}:repair:{action.capability}"
    if action.capability:
        return f"{action.task_id}:{action.action}:{action.capability}"
    if action.target_state:
        return f"{action.task_id}:{action.action}:{action.target_state}"
    return f"{action.task_id}:{action.action}"


def _worktree_needs_apply(root: Path, config: dict[str, Any], task: dict[str, Any]) -> bool:
    run_path = _task_run_path(root, config, task)
    if not run_path:
        return False
    metadata_path = run_path / "metadata.yml"
    if not metadata_path.exists():
        return False
    metadata = load_data(metadata_path)
    workspace = metadata.get("workspace", {})
    if not isinstance(workspace, dict) or not workspace.get("worktree"):
        return False
    return workspace.get("worktree_finalized") is not True


def _task_run_path(root: Path, config: dict[str, Any], task: dict[str, Any]) -> Path | None:
    evidence = task.get("evidence", {})
    run_id = evidence.get("run_id") if isinstance(evidence, dict) else None
    if not run_id:
        return None
    return root / str(config.get("paths", {}).get("runs", "harness/runs")) / str(run_id)


def _apply_worktree_action(root: Path, config: dict[str, Any], task_id: str) -> dict[str, Any]:
    record = _find_task_record(root, config, task_id)
    run_path = _task_run_path(root, config, record.task)
    if not run_path:
        raise ValueError("task requires evidence.run_id before applying worktree")
    result = apply_task_worktree(root, run_path, task_id)
    if result is None:
        return {
            "run_path": run_path,
            "commit_before": None,
            "commit_after": None,
            "applied_to_control": False,
        }
    update_run_workspace(
        run_path,
        {
            "root": str(result.path),
            "worktree": str(result.path),
            "commit_before": result.commit_before,
            "commit_after": result.commit_after,
            "applied_to_control": result.applied_to_control,
            "worktree_finalized": True,
        },
    )
    append_ledger(
        run_path,
        "worktree_applied",
        task_id,
        str(record.task.get("evidence", {}).get("run_id")),
        str(record.task.get("agents", {}).get("owner", "orchestrator")),
        {
            "path": str(result.path),
            "commit_before": result.commit_before,
            "commit_after": result.commit_after,
            "applied_to_control": result.applied_to_control,
        },
    )
    return {
        "run_path": run_path,
        "commit_before": result.commit_before,
        "commit_after": result.commit_after,
        "applied_to_control": result.applied_to_control,
    }


def _run_planner_action(
    root: Path,
    config: dict[str, Any],
    run_path: Path,
    goal: str,
    step: int,
) -> tuple[str, str | None, list[str]]:
    try:
        result = run_planner_capability(root, config, goal)
    except Exception as exc:
        _append_autopilot_event(
            run_path,
            "planner_failed",
            data={"step": step, "error": str(exc)},
        )
        return "failed", None, []
    planner_ref = str(result.run_path.relative_to(root))
    task_ids = [str(record.task["id"]) for record in result.records]
    _append_autopilot_event(
        run_path,
        "planner_finished",
        data={"step": step, "run_path": planner_ref, "tasks": task_ids, "attempts": result.attempts or []},
    )
    return "passed", planner_ref, task_ids


def _run_intake_action(
    root: Path,
    config: dict[str, Any],
    run_path: Path,
    goal: str,
    step: int,
) -> tuple[str, str | None]:
    try:
        result = run_intake_capability(root, config, goal)
    except Exception as exc:
        _append_autopilot_event(
            run_path,
            "intake_failed",
            data={"step": step, "error": str(exc)},
        )
        return "failed", None
    intake_ref = str(result.run_path.relative_to(root))
    status = str(result.output.get("status"))
    _append_autopilot_event(
        run_path,
        "intake_finished",
        data={
            "step": step,
            "status": status,
            "run_path": intake_ref,
            "summary": result.output.get("summary"),
            "artifacts": result.output.get("artifacts", {}),
        },
    )
    if status == "blocked":
        return "blocked", intake_ref
    return ("passed" if status == "passed" else "failed"), intake_ref


def _run_releaser_action(
    root: Path,
    config: dict[str, Any],
    run_path: Path,
    done_tasks: list[str],
    step: int,
) -> tuple[str, str | None]:
    try:
        result = run_release_capability(root, config, done_tasks=done_tasks)
    except Exception as exc:
        _append_autopilot_event(
            run_path,
            "releaser_failed",
            data={"step": step, "done_tasks": done_tasks, "error": str(exc)},
        )
        return "failed", None
    releaser_ref = str((result.run_path / "output.json").relative_to(root))
    status = str(result.output.get("status"))
    _append_autopilot_event(
        run_path,
        "releaser_finished",
        data={"step": step, "status": status, "run_path": str(result.run_path.relative_to(root))},
    )
    if status == "blocked":
        return "blocked", releaser_ref
    return ("passed" if status == "passed" else "failed"), releaser_ref


def _run_release_action(
    root: Path,
    config: dict[str, Any],
    run_path: Path,
    step: int,
    *,
    release_handoff: str | None,
    release_handoff_tasks: list[str],
) -> tuple[str, str | None, str | None]:
    try:
        result = run_release_status(
            root,
            config,
            done_tasks=_completed_task_ids(root, config),
            release_handoff=release_handoff,
            release_handoff_tasks=release_handoff_tasks,
        )
    except Exception as exc:
        _append_autopilot_event(
            run_path,
            "release_failed",
            data={"step": step, "error": str(exc)},
        )
        return "failed", None, "failed"
    release_ref = str((result.run_path / "output.json").relative_to(root))
    _append_autopilot_event(
        run_path,
        "release_finished",
        data={"step": step, "status": result.status, "run_path": str(result.run_path.relative_to(root))},
    )
    if result.status == "blocked":
        return "blocked", release_ref, result.status
    if result.status in {"released", "skipped"}:
        return "passed", release_ref, result.status
    if result.status in EXTERNAL_PENDING_STATUSES:
        return "pending", release_ref, result.status
    return "failed", release_ref, result.status


def _run_release_repair_planner_action(
    root: Path,
    config: dict[str, Any],
    run_path: Path,
    release_ref: str | None,
    release_status: str | None,
    step: int,
    *,
    release_handoff: str | None = None,
) -> tuple[str, str | None, list[str]]:
    goal = _release_repair_goal(root, release_ref, release_status, release_handoff=release_handoff)
    _append_autopilot_event(
        run_path,
        "release_repair_planner_started",
        data={
            "step": step,
            "release": release_ref,
            "release_status": release_status,
            "release_handoff": release_handoff,
        },
    )
    try:
        result = run_planner_capability(root, config, goal)
    except Exception as exc:
        _append_autopilot_event(
            run_path,
            "release_repair_planner_failed",
            data={
                "step": step,
                "release": release_ref,
                "release_status": release_status,
                "release_handoff": release_handoff,
                "error": str(exc),
            },
        )
        return "failed", None, []
    planner_ref = str(result.run_path.relative_to(root))
    task_ids = [str(record.task["id"]) for record in result.records]
    _append_autopilot_event(
        run_path,
        "release_repair_planner_finished",
        data={"step": step, "release": release_ref, "run_path": planner_ref, "tasks": task_ids, "attempts": result.attempts or []},
    )
    return "passed", planner_ref, task_ids


def _release_repair_goal(
    root: Path,
    release_ref: str | None,
    release_status: str | None,
    *,
    release_handoff: str | None = None,
) -> str:
    release_output: dict[str, Any] = {}
    if release_ref:
        release_path = root / release_ref
        if release_path.exists():
            try:
                loaded = load_data(release_path)
                release_output = loaded if isinstance(loaded, dict) else {}
            except ValueError:
                release_output = {}
    status = str(release_output.get("status") or release_status or "failed")
    summary = str(release_output.get("summary") or "Release provider failed without a summary.").strip()
    evidence = release_ref or "release evidence unavailable"
    lines = [
        f"Release provider reported {status}.",
        f"Summary: {summary}",
        f"Release evidence: {evidence}",
    ]
    if release_handoff:
        handoff_output: dict[str, Any] = {}
        handoff_path = root / release_handoff
        if handoff_path.exists():
            try:
                loaded = load_data(handoff_path)
                handoff_output = loaded if isinstance(loaded, dict) else {}
            except ValueError:
                handoff_output = {}
        handoff_status = str(handoff_output.get("status") or "unknown").strip()
        handoff_summary = str(
            handoff_output.get("summary") or "Release handoff did not include a summary."
        ).strip()
        lines.extend(
            [
                f"Release handoff: {release_handoff}",
                f"Release handoff status: {handoff_status}",
                f"Release handoff summary: {handoff_summary}",
            ]
        )
    lines.extend(
        [
            "Generate the smallest ready repair task or tasks required to make the release pass on the next retry.",
            "Do not create manual checklist tasks unless an external credential, service, or business decision is truly required.",
        ]
    )
    return "\n".join(lines)


def _request_repair(
    root: Path,
    config: dict[str, Any],
    run_path: Path,
    action: TaskAction,
    step: int,
    *,
    reason: str,
) -> bool:
    if not _is_repairable_failure(action):
        _append_autopilot_event(
            run_path,
            "repair_unavailable",
            task_id=action.task_id,
            data={"step": step, "action": action.action, "capability": action.capability, "reason": reason},
        )
        return False

    record = _find_task_record(root, config, action.task_id)
    target_capability = _repair_target_capability(root, config, record, action)
    if record.task.get("state") in {"review", "accepted"}:
        record = transition_task(root, config, action.task_id, "in_progress")
    if record.task.get("state") != "in_progress":
        _append_autopilot_event(
            run_path,
            "repair_unavailable",
            task_id=action.task_id,
            data={"step": step, "state": record.task.get("state"), "reason": "state cannot be repaired"},
        )
        return False

    evidence = dict(record.task.get("evidence", {}))
    autopilot = dict(evidence.get("autopilot", {})) if isinstance(evidence.get("autopilot"), dict) else {}
    attempts = int(autopilot.get("repair_attempts", 0))
    max_attempts = _max_repair_attempts(config)
    if attempts >= max_attempts:
        _append_autopilot_event(
            run_path,
            "repair_limit_reached",
            task_id=action.task_id,
            data={"step": step, "attempts": attempts, "max_attempts": max_attempts},
        )
        return False

    updated = dict(record.task)
    capabilities = dict(evidence.get("capabilities", {})) if isinstance(evidence.get("capabilities"), dict) else {}
    for capability in _repair_capabilities_to_clear(target_capability):
        capabilities.pop(capability, None)
    evidence["capabilities"] = capabilities
    evidence["verify"] = None
    autopilot["repair_attempts"] = attempts + 1
    autopilot["repair"] = {
        "status": "pending",
        "source_action": action.action,
        "source_capability": action.capability,
        "source_target_state": action.target_state,
        "target_capability": target_capability,
        "reason": reason,
        "attempt": attempts + 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    evidence["autopilot"] = autopilot
    updated["evidence"] = evidence
    dump_data(updated, record.path)
    _append_autopilot_event(
        run_path,
        "repair_requested",
        task_id=action.task_id,
        data={
            "step": step,
            "attempt": attempts + 1,
            "source_action": action.action,
            "source_capability": action.capability,
            "source_target_state": action.target_state,
            "target_capability": target_capability,
            "reason": reason,
        },
    )
    return True


def _finish_repair(root: Path, config: dict[str, Any], run_path: Path, action: TaskAction, step: int) -> None:
    record = _find_task_record(root, config, action.task_id)
    updated = dict(record.task)
    evidence = dict(updated.get("evidence", {}))
    autopilot = dict(evidence.get("autopilot", {})) if isinstance(evidence.get("autopilot"), dict) else {}
    repair = dict(autopilot.get("repair", {})) if isinstance(autopilot.get("repair"), dict) else {}
    repair["status"] = "resolved"
    repair["resolved_at"] = datetime.now(timezone.utc).isoformat()
    autopilot["repair"] = repair
    evidence["autopilot"] = autopilot
    updated["evidence"] = evidence
    dump_data(updated, record.path)
    _append_autopilot_event(
        run_path,
        "repair_finished",
        task_id=action.task_id,
        data={"step": step, "capability": action.capability},
    )


def _is_repairable_failure(action: TaskAction) -> bool:
    if action.action == "verify_task":
        return True
    if action.action == "ci_status":
        return True
    if action.action == "pr_status":
        return True
    if action.action == "pr_merge":
        return True
    return action.action == "run_capability" and action.capability in {
        "bdd",
        "tdd",
        "implementer",
        "reviewer",
        "verifier",
    }


def _repair_target_capability(
    root: Path,
    config: dict[str, Any],
    record: TaskRecord,
    action: TaskAction,
) -> str:
    if action.action == "run_capability" and action.capability:
        if action.capability in {"bdd", "tdd", "implementer", "verifier"}:
            return action.capability
        if action.capability == "reviewer":
            return "implementer"
    if action.action == "verify_task":
        failed_commands = _failed_verify_commands(root, config, record.task)
        if any(name == "bdd" for name in failed_commands):
            return "bdd"
        if any(name == "unit" for name in failed_commands):
            return "tdd"
        return "implementer"
    if action.action == "pr_status":
        return "reviewer"
    if action.action == "pr_merge":
        return "reviewer"
    return "implementer"


def _failed_verify_commands(root: Path, config: dict[str, Any], task: dict[str, Any]) -> list[str]:
    evidence = task.get("evidence", {})
    verify_ref = evidence.get("verify") if isinstance(evidence, dict) else None
    if not verify_ref:
        return []
    verify_path = root / str(verify_ref)
    if not verify_path.exists():
        return []
    try:
        metadata = load_data(verify_path)
    except ValueError:
        return []
    commands = metadata.get("commands", {})
    if not isinstance(commands, dict):
        return []
    failed: list[str] = []
    for name, command in commands.items():
        if isinstance(command, dict) and command.get("exit_code") not in {None, 0}:
            failed.append(str(name))
    return failed


def _repair_capabilities_to_clear(target_capability: str) -> list[str]:
    order = ["bdd", "tdd", "implementer", "reviewer", "verifier"]
    if target_capability not in order:
        return ["reviewer", "verifier"]
    return order[order.index(target_capability):]


def _max_repair_attempts(config: dict[str, Any]) -> int:
    autopilot = config.get("autopilot", {})
    value = autopilot.get("max_repair_attempts", 1) if isinstance(autopilot, dict) else 1
    return int(value) if isinstance(value, int) and value > 0 else 1


def _find_task_record(root: Path, config: dict[str, Any], task_id: str) -> TaskRecord:
    for record in iter_tasks(root, config):
        if record.task.get("id") == task_id:
            return record
    raise FileNotFoundError(f"task not found: {task_id}")


def _autopilot_run_root(root: Path, config: dict[str, Any]) -> Path:
    return root / config.get("paths", {}).get("autopilot_runs", "harness/autopilot-runs")


def request_autopilot_cancel(run_path: Path, *, reason: str | None = None, requested_by: str = "cli") -> dict[str, Any]:
    if not run_path.exists():
        raise FileNotFoundError(f"autopilot run does not exist: {run_path}")
    payload = {
        "schema_version": 1,
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "requested_by": requested_by,
        "reason": reason,
    }
    dump_data(payload, run_path / "cancel.json")
    metadata_path = run_path / "metadata.json"
    if metadata_path.exists():
        metadata = _migrate_autopilot_metadata(load_data(metadata_path))
        metadata["cancellation"] = payload
        dump_data(metadata, metadata_path)
    _append_autopilot_event(run_path, "autopilot_cancel_requested", data=payload)
    return payload


def read_autopilot_log_lines(run_path: Path) -> list[str]:
    metadata_path = run_path / "metadata.json"
    if not metadata_path.exists():
        raise FileNotFoundError(f"autopilot metadata does not exist: {metadata_path}")
    metadata = load_data(metadata_path)
    batch_executions = metadata.get("batch_executions", [])
    if not isinstance(batch_executions, list):
        return []
    lines: list[str] = []
    for batch in batch_executions:
        if not isinstance(batch, dict):
            continue
        log_ref = batch.get("log") or batch.get("heartbeat")
        if not log_ref:
            continue
        log_path = run_path / str(log_ref)
        if not log_path.exists():
            continue
        lines.extend(log_path.read_text(encoding="utf-8").splitlines())
    return lines


def _create_autopilot_run(root: Path, config: dict[str, Any]) -> tuple[str, Path]:
    base_run_id = f"{utc_timestamp()}-autopilot"
    run_root = _autopilot_run_root(root, config)
    run_path = run_root / base_run_id
    run_id = base_run_id
    counter = 2
    while run_path.exists():
        run_id = f"{base_run_id}-{counter}"
        run_path = run_root / run_id
        counter += 1
    run_path.mkdir(parents=True)
    return run_id, run_path


def _load_autopilot_run(root: Path, run_path: Path) -> tuple[str, Path, dict[str, Any]]:
    resolved = run_path if run_path.is_absolute() else root / run_path
    metadata_path = resolved / "metadata.json"
    if not metadata_path.exists():
        raise FileNotFoundError(f"autopilot metadata does not exist: {metadata_path}")
    metadata = _migrate_autopilot_metadata(load_data(metadata_path))
    run_id = str(metadata.get("run_id") or resolved.name)
    return run_id, resolved, metadata


def _migrate_autopilot_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    migrated = dict(metadata)
    migrated["schema_version"] = AUTOPILOT_METADATA_SCHEMA_VERSION
    for key in ("actions", "planned", "dispatched", "failed", "blocked", "cancelled", "skipped", "releaser_tasks"):
        if not isinstance(migrated.get(key), list):
            migrated[key] = []
    if not isinstance(migrated.get("parameters"), dict):
        migrated["parameters"] = {}
    if not isinstance(migrated.get("loop_cycles"), int):
        migrated["loop_cycles"] = 0
    if not isinstance(migrated.get("resume_count"), int):
        migrated["resume_count"] = 0
    status = str(migrated.get("status") or "paused")
    if not isinstance(migrated.get("state_machine"), dict):
        migrated["state_machine"] = _state_machine_payload(status)
    else:
        state_machine = dict(migrated["state_machine"])
        state_machine.setdefault("version", AUTOPILOT_STATE_MACHINE_VERSION)
        state_machine.setdefault("current", status)
        state_machine.setdefault("terminal", sorted(AUTOPILOT_TERMINAL_STATUSES))
        state_machine.setdefault("paused", ["paused"])
        migrated["state_machine"] = state_machine
    return migrated


def _autopilot_cancel_requested(run_path: Path) -> bool:
    return (run_path / "cancel.json").exists()


def _autopilot_cancel_payload(run_path: Path) -> dict[str, Any] | None:
    cancel_path = run_path / "cancel.json"
    if not cancel_path.exists():
        return None
    payload = load_data(cancel_path)
    return payload if isinstance(payload, dict) else None


def _state_machine_payload(status: str) -> dict[str, Any]:
    return {
        "version": AUTOPILOT_STATE_MACHINE_VERSION,
        "current": status,
        "terminal": sorted(AUTOPILOT_TERMINAL_STATUSES),
        "paused": ["paused"],
    }


def _append_autopilot_event(
    run_path: Path,
    event: str,
    *,
    task_id: str | None = None,
    task_run_id: str | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    line = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "task_id": task_id,
        "task_run_id": task_run_id,
        "data": data or {},
    }
    with (run_path / "ledger.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line, ensure_ascii=False) + "\n")


def _write_autopilot_metadata(run_path: Path, metadata: dict[str, Any]) -> None:
    dump_data(metadata, run_path / "metadata.json")


def _skipped_json(task: SkippedTask) -> dict[str, Any]:
    return {
        "id": task.task_id,
        "state": task.state,
        "path": str(task.path),
        "reasons": task.reasons,
    }
