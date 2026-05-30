from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
from typing import Any

from .context import collect_repository_context
from .io import dump_data
from .planner import import_planner_tasks
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
    planner_output = _run_json_command(root, planner_command, capability_input, run_path, "planner")
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
    capability = get_capability(capability_name)
    capability_command = command or _configured_command(config, capability_name)
    if not capability_command:
        raise ValueError(f"capabilities.{capability_name}.command must be configured or passed with --command")

    record = _find_task(root, config, task_id)
    run_path = _new_capability_run_path(root, config, f"{capability_name}-{task_id}")
    capability_input = build_task_capability_input(root, config, capability, record)
    output = _run_json_command(root, capability_command, capability_input, run_path, capability_name)
    _validate_task_capability_output(output, capability_name)
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
) -> dict[str, Any]:
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
        "project": config.get("project", {}),
        "commands": config.get("commands", {}),
        "task": task,
        "task_path": str(record.path.relative_to(root)),
        "repository_context": collect_repository_context(root, config, focus_files=focus_files),
        "instructions": [
            "Return only JSON.",
            "Do not edit task files directly; Attestflow records capability evidence.",
            "Report blocking external inputs instead of assuming credentials, services, or business decisions.",
            "Keep findings and evidence scoped to the provided task.",
        ],
    }


def _configured_command(config: dict[str, Any], capability_name: str) -> str | None:
    capabilities = config.get("capabilities", {})
    capability_config = capabilities.get(capability_name, {}) if isinstance(capabilities, dict) else {}
    command = capability_config.get("command") if isinstance(capability_config, dict) else None
    return str(command) if command else None


def _find_task(root: Path, config: dict[str, Any], task_id: str) -> TaskRecord:
    for record in iter_tasks(root, config):
        if record.task.get("id") == task_id:
            return record
    raise FileNotFoundError(f"task not found: {task_id}")


def _run_json_command(
    root: Path,
    command: str,
    payload: dict[str, Any],
    run_path: Path,
    capability_name: str,
) -> dict[str, Any]:
    dump_data(payload, run_path / "input.json")
    completed = subprocess.run(
        command,
        cwd=root,
        shell=True,
        text=True,
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        check=False,
    )
    (run_path / "stderr.log").write_text(completed.stderr or "", encoding="utf-8")
    (run_path / "stdout.log").write_text(completed.stdout or "", encoding="utf-8")
    if completed.returncode != 0:
        raise ValueError(f"{capability_name} command failed with exit code {completed.returncode}")
    try:
        output = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{capability_name} command did not return valid JSON: {exc}") from exc
    if not isinstance(output, dict):
        raise ValueError(f"{capability_name} command must return a JSON object")
    dump_data(output, run_path / "output.json")
    return output


def _validate_task_capability_output(output: dict[str, Any], capability_name: str) -> None:
    if output.get("schema_version") != 1:
        raise ValueError(f"{capability_name} output schema_version must be 1")
    if output.get("status") not in {"passed", "failed", "blocked"}:
        raise ValueError(f"{capability_name} output status must be one of: passed, failed, blocked")
    if not str(output.get("summary", "")).strip():
        raise ValueError(f"{capability_name} output summary must be non-empty")
    findings = output.get("findings", [])
    if not isinstance(findings, list):
        raise ValueError(f"{capability_name} output findings must be a list")
    evidence = output.get("evidence", [])
    if not isinstance(evidence, list):
        raise ValueError(f"{capability_name} output evidence must be a list")


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
