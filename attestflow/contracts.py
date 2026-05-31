from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .io import load_data


TASK_CAPABILITY_STATUSES = ("passed", "failed", "blocked")
SESSION_STATUSES = {"launch": {"launched", "blocked"}, "resume": {"resumed", "blocked"}}
CI_STATUSES = {"passed", "failed", "running", "queued", "cancelled", "skipped", "blocked", "unknown"}
PR_STATUSES = {"merged", "open", "draft", "blocked", "failed", "skipped", "unknown"}
RELEASE_STATUSES = {"released", "skipped", "running", "queued", "blocked", "failed", "unknown"}
REVIEW_FINDING_SEVERITIES = {"blocker", "major", "minor", "info"}


def validate_contract(contract_type: str, value: Any) -> list[str]:
    if not isinstance(value, dict):
        return [f"{contract_type} must be a JSON object"]
    validator = CONTRACT_TYPES.get(contract_type)
    if validator is None:
        return [f"unknown contract type: {contract_type}"]
    return validator(value)


def validate_contract_file(contract_type: str, path: Path) -> list[str]:
    value = load_data(path)
    return validate_contract(contract_type, value)


def contract_validation_hint(contract_type: str, path: str | Path | None = None) -> str:
    target = str(path) if path else "<provider-output.json>"
    return f"next: python -m attestflow contract validate {contract_type} {target}"


def raise_contract_errors(label: str, contract_type: str, errors: list[str], path: str | Path | None = None) -> None:
    if errors:
        raise ValueError("; ".join(errors) + "\n" + contract_validation_hint(contract_type, path))


def validate_planner_output(output: dict[str, Any], label: str = "planner-output") -> list[str]:
    errors: list[str] = []
    _require_schema_version(output, label, errors)
    tasks = output.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        errors.append(_field_error(label, "tasks", "must be a non-empty list"))
        return errors
    keys: set[str] = set()
    for index, task in enumerate(tasks):
        task_label = f"{label}.tasks[{index}]"
        if not isinstance(task, dict):
            errors.append(f"{task_label} must be an object")
            continue
        if not str(task.get("title", "")).strip():
            errors.append(_field_error(task_label, "title", "must be non-empty"))
        if not str(task.get("purpose", "")).strip():
            errors.append(_field_error(task_label, "purpose", "must be non-empty"))
        for key in ("scope", "out_of_scope", "bdd_scenarios", "unit_tests", "acceptance"):
            if not _non_empty_string_list(task.get(key)):
                errors.append(_field_error(task_label, key, "must be a non-empty list of strings"))
        requirements = task.get("requirements")
        if not isinstance(requirements, dict):
            errors.append(_field_error(task_label, "requirements", "must be a mapping"))
        else:
            for key in ("confirmed", "unresolved", "assumptions"):
                value = requirements.get(key, [])
                if not isinstance(value, list) or not all(str(item).strip() for item in value):
                    errors.append(_field_error(task_label, f"requirements.{key}", "must be a list of strings"))
            if task.get("type") != "spike" and requirements.get("unresolved"):
                errors.append(_field_error(task_label, "requirements.unresolved", "must be empty for ready non-spike tasks"))
        files = task.get("files")
        if not isinstance(files, dict):
            errors.append(_field_error(task_label, "files", "must be a mapping"))
        elif not _non_empty_string_list(files.get("write")):
            errors.append(_field_error(task_label, "files.write", "must be a non-empty list of strings"))
        key = str(task.get("key", "")).strip()
        if key:
            if key in keys:
                errors.append(_field_error(task_label, "key", f"duplicates planner task key: {key}"))
            keys.add(key)
        dependencies = task.get("dependencies", [])
        if dependencies is not None and not isinstance(dependencies, list):
            errors.append(_field_error(task_label, "dependencies", "must be a list"))
    return errors


def validate_capability_output(output: dict[str, Any], label: str = "capability-output") -> list[str]:
    errors: list[str] = []
    _require_schema_version(output, label, errors)
    _require_status(output, label, TASK_CAPABILITY_STATUSES, errors)
    _require_summary(output, label, errors)
    if not isinstance(output.get("findings", []), list):
        errors.append(_field_error(label, "findings", "must be a list"))
    if not isinstance(output.get("evidence", []), list):
        errors.append(_field_error(label, "evidence", "must be a list"))
    return errors


def validate_typed_capability_output(
    output: dict[str, Any],
    capability_name: str,
    *,
    label: str = "capability-output",
) -> list[str]:
    errors = validate_capability_output(output, label=label)
    if capability_name == "reviewer":
        _validate_review_findings(output, label, errors)
        return errors
    if output.get("status") == "blocked":
        return errors
    artifacts = output.get("artifacts")
    if capability_name == "bdd":
        if not isinstance(artifacts, dict):
            errors.append(_field_error(label, "artifacts", "must be a mapping"))
            return errors
        if not _non_empty_scenario_list(artifacts.get("scenarios")):
            errors.append(_field_error(label, "artifacts.scenarios", "must be a non-empty list of scenario objects"))
        if not _string_list(artifacts.get("updated_files")):
            errors.append(_field_error(label, "artifacts.updated_files", "must be a list of strings"))
        if not _mapping_list(artifacts.get("requirements_mapping")):
            errors.append(_field_error(label, "artifacts.requirements_mapping", "must be a list of mappings"))
        if not _string_list(artifacts.get("uncovered_behaviors")):
            errors.append(_field_error(label, "artifacts.uncovered_behaviors", "must be a list of strings"))
        return errors
    if capability_name == "tdd":
        if not isinstance(artifacts, dict):
            errors.append(_field_error(label, "artifacts", "must be a mapping"))
            return errors
        if not str(artifacts.get("red_log", "")).strip():
            errors.append(_field_error(label, "artifacts.red_log", "must be non-empty"))
        if not str(artifacts.get("green_log", "")).strip():
            errors.append(_field_error(label, "artifacts.green_log", "must be non-empty"))
        if not _non_empty_string_list(artifacts.get("test_files")):
            errors.append(_field_error(label, "artifacts.test_files", "must be a non-empty list of strings"))
        if not _string_list(artifacts.get("failing_tests")):
            errors.append(_field_error(label, "artifacts.failing_tests", "must be a list of strings"))
        if not isinstance(artifacts.get("coverage"), dict):
            errors.append(_field_error(label, "artifacts.coverage", "must be a mapping"))
        return errors
    if capability_name == "implementer":
        if not isinstance(artifacts, dict):
            errors.append(_field_error(label, "artifacts", "must be a mapping"))
            return errors
        if not str(artifacts.get("diff_summary", "")).strip():
            errors.append(_field_error(label, "artifacts.diff_summary", "must be non-empty"))
        if not _non_empty_string_list(artifacts.get("written_files")):
            errors.append(_field_error(label, "artifacts.written_files", "must be a non-empty list of strings"))
        if not _string_list(artifacts.get("incomplete")):
            errors.append(_field_error(label, "artifacts.incomplete", "must be a list of strings"))
        if not _string_list(artifacts.get("risks")):
            errors.append(_field_error(label, "artifacts.risks", "must be a list of strings"))
        if not isinstance(artifacts.get("command_results"), list):
            errors.append(_field_error(label, "artifacts.command_results", "must be a list"))
        return errors
    if capability_name == "verifier":
        if not isinstance(artifacts, dict):
            errors.append(_field_error(label, "artifacts", "must be a mapping"))
            return errors
        if not _non_empty_command_result_list(artifacts.get("commands")):
            errors.append(_field_error(label, "artifacts.commands", "must be a non-empty list of command result objects"))
        if not isinstance(artifacts.get("environment"), dict):
            errors.append(_field_error(label, "artifacts.environment", "must be a mapping"))
        duration = artifacts.get("duration_seconds")
        if not isinstance(duration, (int, float)) or duration < 0:
            errors.append(_field_error(label, "artifacts.duration_seconds", "must be a non-negative number"))
        flake = artifacts.get("flake")
        if not isinstance(flake, dict) or not isinstance(flake.get("detected"), bool):
            errors.append(_field_error(label, "artifacts.flake.detected", "must be a boolean"))
        if not isinstance(artifacts.get("evidence"), list):
            errors.append(_field_error(label, "artifacts.evidence", "must be a list"))
    return errors


def validate_session_launch_output(output: dict[str, Any], label: str = "session-launch-output") -> list[str]:
    return validate_session_output(output, "launch", label=label)


def validate_session_resume_output(output: dict[str, Any], label: str = "session-resume-output") -> list[str]:
    return validate_session_output(output, "resume", label=label)


def validate_session_output(output: dict[str, Any], action: str, label: str = "adapter output") -> list[str]:
    errors: list[str] = []
    _require_schema_version(output, label, errors)
    allowed = SESSION_STATUSES.get(action, set())
    _require_status(output, label, allowed, errors)
    _require_summary(output, label, errors)
    return errors


def validate_ci_output(output: dict[str, Any], label: str = "ci-output") -> list[str]:
    errors: list[str] = []
    _require_schema_version(output, label, errors)
    _require_status(output, label, CI_STATUSES, errors)
    _require_summary(output, label, errors)
    if not isinstance(output.get("checks", []), list):
        errors.append(_field_error(label, "checks", "must be a list"))
    return errors


def validate_pr_output(output: dict[str, Any], label: str = "pr-output") -> list[str]:
    errors: list[str] = []
    _require_schema_version(output, label, errors)
    _require_status(output, label, PR_STATUSES, errors)
    _require_summary(output, label, errors)
    if not isinstance(output.get("checks", []), list):
        errors.append(_field_error(label, "checks", "must be a list"))
    return errors


def validate_release_output(output: dict[str, Any], label: str = "release-output") -> list[str]:
    errors: list[str] = []
    _require_schema_version(output, label, errors)
    _require_status(output, label, RELEASE_STATUSES, errors)
    _require_summary(output, label, errors)
    if not isinstance(output.get("artifacts", []), list):
        errors.append(_field_error(label, "artifacts", "must be a list"))
    return errors


def validate_task_contract(output: dict[str, Any], label: str = "task") -> list[str]:
    from .tasks import validate_task

    return [f"{label}.{error}" for error in validate_task(output)]


def _require_schema_version(output: dict[str, Any], label: str, errors: list[str]) -> None:
    if output.get("schema_version") != 1:
        errors.append(_field_error(label, "schema_version", "must be 1"))


def _require_status(output: dict[str, Any], label: str, allowed: Any, errors: list[str]) -> None:
    if output.get("status") not in allowed:
        ordered = sorted(allowed) if isinstance(allowed, set) else list(allowed)
        errors.append(_field_error(label, "status", "must be one of: " + ", ".join(ordered)))


def _require_summary(output: dict[str, Any], label: str, errors: list[str]) -> None:
    if not str(output.get("summary", "")).strip():
        errors.append(_field_error(label, "summary", "must be non-empty"))


def _field_error(label: str, field: str, message: str) -> str:
    separator = " " if " " in label else "."
    return f"{label}{separator}{field} {message}"


def _non_empty_string_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(str(item).strip() for item in value)


def _string_list(value: Any) -> bool:
    return isinstance(value, list) and all(str(item).strip() for item in value)


def _mapping_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, dict) for item in value)


def _non_empty_scenario_list(value: Any) -> bool:
    if not isinstance(value, list) or not value:
        return False
    for item in value:
        if not isinstance(item, dict):
            return False
        if not str(item.get("name", "")).strip():
            return False
        if not str(item.get("given", "")).strip():
            return False
        if not str(item.get("when", "")).strip():
            return False
        if not str(item.get("then", "")).strip():
            return False
    return True


def _non_empty_command_result_list(value: Any) -> bool:
    if not isinstance(value, list) or not value:
        return False
    for item in value:
        if not isinstance(item, dict):
            return False
        if not str(item.get("name", "")).strip():
            return False
        if not str(item.get("command", "")).strip():
            return False
        if item.get("status") not in TASK_CAPABILITY_STATUSES:
            return False
    return True


def _validate_review_findings(output: dict[str, Any], label: str, errors: list[str]) -> None:
    findings = output.get("findings", [])
    if not isinstance(findings, list):
        return
    for index, finding in enumerate(findings):
        finding_label = f"{label}.findings[{index}]" if " " not in label else f"{label} findings[{index}]"
        if not isinstance(finding, dict):
            errors.append(f"{finding_label} must be an object")
            continue
        if finding.get("severity") not in REVIEW_FINDING_SEVERITIES:
            errors.append(_field_error(finding_label, "severity", "must be one of: blocker, major, minor, info"))
        if not isinstance(finding.get("blocking"), bool):
            errors.append(_field_error(finding_label, "blocking", "must be a boolean"))
        if not str(finding.get("summary", "")).strip():
            errors.append(_field_error(finding_label, "summary", "must be non-empty"))


CONTRACT_TYPES: dict[str, Callable[[dict[str, Any]], list[str]]] = {
    "planner-output": validate_planner_output,
    "capability-output": validate_capability_output,
    "session-launch-output": validate_session_launch_output,
    "session-resume-output": validate_session_resume_output,
    "ci-output": validate_ci_output,
    "pr-output": validate_pr_output,
    "release-output": validate_release_output,
    "task": validate_task_contract,
}
