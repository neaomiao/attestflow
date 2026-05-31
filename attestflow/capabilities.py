from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Any

from .contracts import raise_contract_errors, validate_planner_output, validate_typed_capability_output
from .context import collect_repository_context
from .evidence import workspace_root_for_run
from .io import dump_data, load_data
from .planner import import_planner_tasks
from .provider_commands import provider_timeout_seconds, run_provider_json_command
from .release import release_task_summaries
from .sessions import BUILTIN_SESSION_PROVIDERS
from .tasks import TaskRecord, block_task, iter_tasks


@dataclass(frozen=True)
class CapabilityRunResult:
    records: list[TaskRecord]
    run_path: Path


@dataclass(frozen=True)
class TaskCapabilityRunResult:
    capability: str
    task_id: str
    output: dict[str, Any]
    run_path: Path


@dataclass(frozen=True)
class ReleaseCapabilityRunResult:
    output: dict[str, Any]
    run_path: Path
    done_tasks: list[str]


BUILTIN_CAPABILITIES: list[dict[str, Any]] = [
    {
        "name": "intake",
        "specialist": "requirements partner",
        "phase": "think",
        "description": "Reframe vague intent into confirmed requirements, assumptions, and unresolved decisions.",
        "inputs": ["user goal", "project context", "existing docs"],
        "outputs": ["requirement brief", "open decision list"],
        "gates": ["unresolved business decisions are explicit", "manual work is not added as the default path"],
        "evidence": ["capability input", "requirement brief", "decision log"],
        "programming_agent_provider": "optional",
        "external_dependency": False,
    },
    {
        "name": "planner",
        "specialist": "spec planner",
        "phase": "plan",
        "description": "Turn an approved goal into planner JSON that Attestflow can validate and import.",
        "inputs": ["user goal", "harness config", "existing task index", "planner output contract"],
        "outputs": ["planner JSON"],
        "gates": ["planner JSON parses", "runtime tasks satisfy Definition of Ready", "task ids are assigned by Attestflow"],
        "evidence": ["input.json", "output.json", "stderr.log"],
        "programming_agent_provider": "optional",
        "external_dependency": False,
        "contract": "docs/contracts/planner-output-schema.md",
    },
    {
        "name": "bdd",
        "specialist": "behavior spec author",
        "phase": "plan",
        "description": "Convert task requirements into behavior scenarios before implementation.",
        "inputs": ["task JSON", "requirement brief"],
        "outputs": ["BDD scenarios", "acceptance examples"],
        "gates": ["observable behavior is described before unit tests", "edge cases are named"],
        "evidence": ["scenario diff", "task update"],
        "programming_agent_provider": "optional",
        "external_dependency": False,
    },
    {
        "name": "tdd",
        "specialist": "test engineer",
        "phase": "build",
        "description": "Drive implementation through failing tests, minimal code, and green verification.",
        "inputs": ["task JSON", "BDD scenarios", "write scope"],
        "outputs": ["failing test evidence", "passing test evidence"],
        "gates": ["red evidence exists before implementation", "green evidence references the current run"],
        "evidence": ["red log", "green log", "test diff"],
        "programming_agent_provider": "optional",
        "external_dependency": False,
    },
    {
        "name": "implementer",
        "specialist": "implementation worker",
        "phase": "build",
        "description": "Apply scoped code changes without crossing task ownership boundaries.",
        "inputs": ["task JSON", "prompt packet", "file locks"],
        "outputs": ["implementation diff"],
        "gates": ["writes stay inside files.write", "unrelated user changes are preserved"],
        "evidence": ["diff summary", "ledger events"],
        "programming_agent_provider": "optional",
        "external_dependency": False,
    },
    {
        "name": "reviewer",
        "specialist": "staff engineer reviewer",
        "phase": "review",
        "description": "Find correctness, completeness, regression, and test gaps before close.",
        "inputs": ["task JSON", "diff", "verification logs"],
        "outputs": ["review findings", "fix recommendations"],
        "gates": ["findings are severity ordered", "blocking issues prevent close"],
        "evidence": ["review report", "resolved finding log"],
        "programming_agent_provider": "optional",
        "external_dependency": False,
    },
    {
        "name": "verifier",
        "specialist": "verification lead",
        "phase": "test",
        "description": "Run configured commands and prove the current run satisfies completion gates.",
        "inputs": ["task JSON", "harness config", "run metadata"],
        "outputs": ["verification packet"],
        "gates": ["fresh command logs exist", "required evidence is linked to the task"],
        "evidence": ["command logs", "evidence.md", "ledger.jsonl"],
        "programming_agent_provider": "optional",
        "external_dependency": False,
    },
    {
        "name": "releaser",
        "specialist": "release engineer",
        "phase": "ship",
        "description": "Prepare merge, release notes, and post-merge verification without binding to one CI provider.",
        "inputs": ["done tasks", "verification packets", "release config"],
        "outputs": ["release checklist", "post-release verification plan"],
        "gates": ["CI provider is optional", "release evidence is auditable"],
        "evidence": ["release checklist", "CI or local verification logs"],
        "programming_agent_provider": "optional",
        "external_dependency": False,
    },
]


def list_capabilities() -> list[dict[str, Any]]:
    return deepcopy(BUILTIN_CAPABILITIES)


def get_capability(name: str) -> dict[str, Any]:
    for capability in BUILTIN_CAPABILITIES:
        if capability["name"] == name:
            return deepcopy(capability)
    raise ValueError(f"unknown capability: {name}")


def is_capability_configured(config: dict[str, Any], capability_name: str) -> bool:
    return _configured_command(config, capability_name) is not None


def run_release_capability(
    root: Path,
    config: dict[str, Any],
    done_tasks: list[str],
    *,
    command: str | None = None,
) -> ReleaseCapabilityRunResult:
    capability = get_capability("releaser")
    capability_command = command or _configured_command(config, "releaser")
    if not capability_command:
        raise ValueError("capabilities.releaser.command must be configured or passed with --command")

    run_path = _new_capability_run_path(root, config, "releaser")
    capability_input = build_release_capability_input(root, config, capability, done_tasks)
    output = _run_json_command(root, config, "releaser", capability_command, capability_input, run_path)
    _validate_task_capability_output(output, "releaser", run_path / "output.json")
    return ReleaseCapabilityRunResult(output=output, run_path=run_path, done_tasks=done_tasks)


def run_planner_capability(
    root: Path,
    config: dict[str, Any],
    goal: str,
    *,
    command: str | None = None,
) -> CapabilityRunResult:
    planner_command = command or _configured_command(config, "planner")
    if not planner_command:
        raise ValueError("capabilities.planner.command must be configured or passed with --command")

    run_path = _new_capability_run_path(root, config, "planner")
    capability_input = build_planner_input(root, config, goal)
    planner_output = _run_json_command(root, config, "planner", planner_command, capability_input, run_path)
    raise_contract_errors(
        "planner output",
        "planner-output",
        validate_planner_output(planner_output, label="planner output"),
        run_path / "output.json",
    )
    records = import_planner_tasks(root, config, planner_output)
    return CapabilityRunResult(records=records, run_path=run_path)


def run_task_capability(
    root: Path,
    config: dict[str, Any],
    capability_name: str,
    task_id: str,
    *,
    command: str | None = None,
) -> TaskCapabilityRunResult:
    if capability_name == "planner":
        raise ValueError("planner is goal-scoped; use attestflow plan")
    if capability_name == "releaser":
        raise ValueError("releaser is release-scoped; use autopilot release gate")
    capability = get_capability(capability_name)
    capability_command = command or _configured_command(config, capability_name)
    if not capability_command:
        raise ValueError(f"capabilities.{capability_name}.command must be configured or passed with --command")

    record = _find_task(root, config, task_id)
    workspace_root = _task_workspace_root(root, config, record.task)
    run_path = _new_capability_run_path(root, config, f"{capability_name}-{task_id}")
    capability_input = build_task_capability_input(root, config, capability, record, workspace_root=workspace_root)
    before_status = _git_status_paths(workspace_root)
    output = _run_json_command(workspace_root, config, capability_name, capability_command, capability_input, run_path)
    _validate_task_capability_output(
        output,
        capability_name,
        run_path / "output.json",
        task=record.task,
        workspace_root=workspace_root,
        config=config,
        before_status=before_status,
    )
    _record_task_capability_evidence(root, record, capability_name, run_path)
    if output.get("status") == "blocked":
        block_task(
            root,
            config,
            task_id,
            reason=str(output["summary"]),
            unblock_condition=f"Resolve blocker reported by capability {capability_name}, then unblock the task.",
            owner="user",
            blocker_type="capability",
            source=f"capability:{capability_name}",
        )
    return TaskCapabilityRunResult(
        capability=capability_name,
        task_id=task_id,
        output=output,
        run_path=run_path,
    )


def build_planner_input(root: Path, config: dict[str, Any], goal: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "capability": get_capability("planner"),
        "agent_provider": _capability_agent_provider(config, "planner"),
        "provider_options": _provider_options(config, "planner"),
        "root": str(root),
        "goal": goal,
        "project": config.get("project", {}),
        "commands": config.get("commands", {}),
        "repository_context": collect_repository_context(root, config),
        "contracts": {
            "planner_output": "docs/contracts/planner-output-schema.md",
            "runtime_task": "docs/contracts/task-schema.md",
        },
        "existing_tasks": [
            {
                "id": record.task.get("id"),
                "state": record.task.get("state"),
                "title": record.task.get("title"),
                "priority": record.task.get("priority"),
            }
            for record in iter_tasks(root, config)
        ],
        "instructions": [
            "Return only planner JSON.",
            "Do not generate TASK-* ids; Attestflow assigns runtime task ids.",
            "Every ready task must include scope, BDD scenarios, unit tests, acceptance, and files.write.",
            "Ask for external credentials or business decisions through external_inputs instead of assuming them.",
        ],
    }


def build_task_capability_input(
    root: Path,
    config: dict[str, Any],
    capability: dict[str, Any],
    record: TaskRecord,
    *,
    workspace_root: Path | None = None,
) -> dict[str, Any]:
    workspace_root = workspace_root or _task_workspace_root(root, config, record.task)
    task = record.task
    files = task.get("files", {}) if isinstance(task.get("files"), dict) else {}
    focus_files = []
    for key in ("read", "write"):
        value = files.get(key)
        if isinstance(value, list):
            focus_files.extend(str(item) for item in value)
    return {
        "schema_version": 1,
        "capability": capability,
        "agent_provider": _capability_agent_provider(config, str(capability["name"])),
        "provider_options": _provider_options(config, str(capability["name"])),
        "root": str(workspace_root),
        "control_root": str(root),
        "workspace": _task_workspace(root, config, task),
        "project": config.get("project", {}),
        "commands": config.get("commands", {}),
        "task": task,
        "task_path": str(record.path.relative_to(root)),
        "repository_context": collect_repository_context(workspace_root, config, focus_files=focus_files),
        "instructions": [
            "Return only JSON.",
            "Do not edit task files directly; Attestflow records capability evidence.",
            "Report blocking external inputs instead of assuming credentials, services, or business decisions.",
            "Keep findings and evidence scoped to the provided task.",
        ],
    }


def build_release_capability_input(
    root: Path,
    config: dict[str, Any],
    capability: dict[str, Any],
    done_tasks: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "capability": capability,
        "agent_provider": _capability_agent_provider(config, "releaser"),
        "provider_options": _provider_options(config, "releaser"),
        "root": str(root),
        "project": config.get("project", {}),
        "commands": config.get("commands", {}),
        "done_tasks": done_tasks,
        "tasks": release_task_summaries(root, config, done_tasks),
        "repository_context": collect_repository_context(root, config),
        "instructions": [
            "Return only JSON.",
            "Prepare release handoff evidence from completed tasks and delivery evidence.",
            "Report blocking external inputs instead of assuming credentials, services, or business decisions.",
            "Do not perform irreversible release actions; release provider handles the external release boundary.",
        ],
    }


def _configured_command(config: dict[str, Any], capability_name: str) -> str | None:
    capability_config = _capability_config(config, capability_name)
    command = capability_config.get("command") if isinstance(capability_config, dict) else None
    if command:
        return str(command)
    agent_provider = _capability_agent_provider(config, capability_name)
    if agent_provider in BUILTIN_SESSION_PROVIDERS:
        return _builtin_capability_adapter_command()
    return None


def _capability_config(config: dict[str, Any], capability_name: str) -> dict[str, Any]:
    capabilities = config.get("capabilities", {})
    capability_config = capabilities.get(capability_name, {}) if isinstance(capabilities, dict) else {}
    return capability_config if isinstance(capability_config, dict) else {}


def _capability_agent_provider(config: dict[str, Any], capability_name: str) -> str:
    capability_config = _capability_config(config, capability_name)
    if capability_config.get("agent_provider"):
        return str(capability_config["agent_provider"])
    sessions = config.get("sessions", {})
    if isinstance(sessions, dict) and sessions.get("agent_provider"):
        return str(sessions["agent_provider"])
    return "command"


def _provider_options(config: dict[str, Any], capability_name: str) -> dict[str, Any]:
    options: dict[str, Any] = {}
    sessions = config.get("sessions", {})
    session_options = sessions.get("provider_options", {}) if isinstance(sessions, dict) else {}
    if isinstance(session_options, dict):
        options.update(session_options)
    capability_options = _capability_config(config, capability_name).get("provider_options", {})
    if isinstance(capability_options, dict):
        options.update(capability_options)
    return options


def _builtin_capability_adapter_command() -> str:
    adapter_path = Path(__file__).resolve().parent / "capability_adapters.py"
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(adapter_path))}"


def _find_task(root: Path, config: dict[str, Any], task_id: str) -> TaskRecord:
    for record in iter_tasks(root, config):
        if record.task.get("id") == task_id:
            return record
    raise FileNotFoundError(f"task not found: {task_id}")


def _task_workspace_root(root: Path, config: dict[str, Any], task: dict[str, Any]) -> Path:
    run_path = _task_run_path(root, config, task)
    return workspace_root_for_run(run_path, root) if run_path else root


def _task_workspace(root: Path, config: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    run_path = _task_run_path(root, config, task)
    if not run_path:
        return {"root": str(root), "worktree": None}
    metadata_path = run_path / "metadata.yml"
    if not metadata_path.exists():
        return {"root": str(root), "worktree": None}
    metadata = load_data(metadata_path)
    workspace = metadata.get("workspace", {})
    if not isinstance(workspace, dict):
        return {"root": str(root), "worktree": None}
    normalized = dict(workspace)
    for key in ("root", "worktree"):
        if normalized.get(key):
            normalized[key] = str(Path(str(normalized[key])).resolve())
    return normalized


def _task_run_path(root: Path, config: dict[str, Any], task: dict[str, Any]) -> Path | None:
    evidence = task.get("evidence", {})
    run_id = evidence.get("run_id") if isinstance(evidence, dict) else None
    if not run_id:
        return None
    return root / str(config.get("paths", {}).get("runs", "harness/runs")) / str(run_id)


def _run_json_command(
    cwd: Path,
    config: dict[str, Any],
    capability_name: str,
    command: str,
    payload: dict[str, Any],
    run_path: Path,
) -> dict[str, Any]:
    output = run_provider_json_command(
        cwd,
        command,
        payload,
        run_path,
        capability_name,
        timeout_seconds=_capability_timeout_seconds(config, capability_name),
    )
    dump_data(output, run_path / "output.json")
    return output


def _capability_timeout_seconds(config: dict[str, Any], capability_name: str) -> float | None:
    capability_config = _capability_config(config, capability_name)
    return provider_timeout_seconds(
        {
            "timeout_seconds": capability_config.get("timeout_seconds"),
            "provider_options": _provider_options(config, capability_name),
        }
    )


def _validate_task_capability_output(
    output: dict[str, Any],
    capability_name: str,
    path: Path | None = None,
    *,
    task: dict[str, Any] | None = None,
    workspace_root: Path | None = None,
    config: dict[str, Any] | None = None,
    before_status: set[str] | None = None,
) -> None:
    errors = validate_typed_capability_output(output, capability_name, label=f"{capability_name} output")
    if task is not None:
        errors.extend(_capability_write_scope_errors(output, capability_name, task, label=f"{capability_name} output"))
    if (
        output.get("status") == "passed"
        and task is not None
        and workspace_root is not None
        and before_status is not None
    ):
        errors.extend(
            _actual_write_scope_errors(
                workspace_root,
                config or {},
                task,
                before_status,
                label=f"{capability_name} output",
            )
        )
    raise_contract_errors(
        f"{capability_name} output",
        "capability-output",
        errors,
        path,
    )


def _capability_write_scope_errors(
    output: dict[str, Any],
    capability_name: str,
    task: dict[str, Any],
    *,
    label: str,
) -> list[str]:
    if output.get("status") == "blocked":
        return []
    artifacts = output.get("artifacts")
    if not isinstance(artifacts, dict):
        return []
    field_by_capability = {
        "bdd": "updated_files",
        "tdd": "test_files",
        "implementer": "written_files",
    }
    field = field_by_capability.get(capability_name)
    if field is None:
        return []
    files = artifacts.get(field, [])
    if not isinstance(files, list):
        return []
    return _files_write_scope_errors(files, task, label=f"{label}.artifacts.{field}")


def _actual_write_scope_errors(
    workspace_root: Path,
    config: dict[str, Any],
    task: dict[str, Any],
    before_status: set[str],
    *,
    label: str,
) -> list[str]:
    after_status = _git_status_paths(workspace_root)
    if after_status is None:
        return []
    runtime_paths = _runtime_status_paths(config)
    changed = sorted(
        path
        for path in (after_status - before_status)
        if path and not _path_matches_any(path, runtime_paths)
    )
    return _files_write_scope_errors(changed, task, label=f"{label}.actual_writes", prefix="wrote outside files.write")


def _files_write_scope_errors(
    files: list[Any],
    task: dict[str, Any],
    *,
    label: str,
    prefix: str = "must stay within files.write",
) -> list[str]:
    write_scope = _task_write_scope(task)
    outside = []
    for item in files:
        normalized = _normalize_repo_path(str(item))
        if not normalized or not _path_matches_any(normalized, write_scope):
            outside.append(str(item))
    if not outside:
        return []
    return [f"{label} {prefix}: {', '.join(outside)}"]


def _task_write_scope(task: dict[str, Any]) -> list[str]:
    files = task.get("files", {}) if isinstance(task.get("files"), dict) else {}
    write = files.get("write", []) if isinstance(files, dict) else []
    if not isinstance(write, list):
        return []
    return [path for path in (_normalize_repo_path(str(item)) for item in write) if path]


def _runtime_status_paths(config: dict[str, Any]) -> list[str]:
    paths = config.get("paths", {}) if isinstance(config.get("paths"), dict) else {}
    defaults = (
        "tasks",
        "runs",
        "locks",
        "capability_runs",
        "autopilot_runs",
        "ci_runs",
        "pr_runs",
        "release_runs",
    )
    values = []
    for key in defaults:
        value = paths.get(key)
        if value:
            normalized = _normalize_repo_path(str(value))
            if normalized:
                values.append(normalized)
    if not values:
        values.append("harness")
    return values


def _normalize_repo_path(value: str) -> str | None:
    path = value.strip().replace("\\", "/")
    if not path:
        return None
    if path.startswith('"') and path.endswith('"'):
        path = path[1:-1]
    if path.startswith("/") or path.startswith("../") or "/../" in path or path == "..":
        return None
    while path.startswith("./"):
        path = path[2:]
    return path.rstrip("/")


def _path_matches_any(path: str, scopes: list[str]) -> bool:
    normalized = _normalize_repo_path(path)
    if not normalized:
        return False
    for scope in scopes:
        if normalized == scope or normalized.startswith(scope.rstrip("/") + "/"):
            return True
    return False


def _git_status_paths(root: Path) -> set[str] | None:
    try:
        inside = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return None
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all", "--", "."],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if status.returncode != 0:
        return None
    paths: set[str] = set()
    for line in status.stdout.splitlines():
        if len(line) < 4:
            continue
        raw_path = line[3:].strip()
        if " -> " in raw_path:
            raw_path = raw_path.split(" -> ", 1)[1].strip()
        normalized = _normalize_repo_path(raw_path)
        if normalized:
            paths.add(normalized)
    return paths


def _record_task_capability_evidence(
    root: Path,
    record: TaskRecord,
    capability_name: str,
    run_path: Path,
) -> None:
    updated = dict(record.task)
    evidence = dict(updated.get("evidence", {}))
    capabilities = dict(evidence.get("capabilities", {})) if isinstance(evidence.get("capabilities"), dict) else {}
    capabilities[capability_name] = str((run_path / "output.json").relative_to(root))
    evidence["capabilities"] = capabilities
    updated["evidence"] = evidence
    dump_data(updated, record.path)


def _new_capability_run_path(root: Path, config: dict[str, Any], capability_name: str) -> Path:
    run_root = root / str(config.get("paths", {}).get("capability_runs", "harness/capability-runs"))
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = run_root / f"{capability_name}-{timestamp}"
    path.mkdir(parents=True, exist_ok=False)
    return path
