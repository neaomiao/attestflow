from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .io import load_data


DEFAULT_CONFIG: dict[str, Any] = {
    "schema_version": 1,
    "project": {"name": "harness", "default_branch": "main"},
    "paths": {
        "tasks": "harness/tasks",
        "runs": "harness/runs",
        "gates": "harness/gates",
        "locks": "harness/locks",
        "capability_runs": "harness/capability-runs",
        "autopilot_runs": "harness/autopilot-runs",
        "ci_runs": "harness/ci-runs",
        "pr_runs": "harness/pr-runs",
        "release_runs": "harness/release-runs",
        "docs": "docs",
    },
    "commands": {
        "bdd": "python -m unittest discover -s tests/bdd",
        "unit": "python -m unittest discover -s tests/unit",
        "lint": None,
        "typecheck": None,
        "secret_scan": "python -m attestflow secret-scan",
        "project_verify": None,
    },
    "policies": {
        "require_bdd_before_unit": True,
        "require_unit_before_implementation": True,
        "require_fresh_verify_for_done": True,
        "require_agent_session_for_task": True,
        "require_disjoint_agent_write_scopes": True,
        "require_issue_triage_for_linked_issues": True,
        "docker_required": False,
    },
    "sessions": {
        "agent_provider": "command",
        "role": "worker_agent",
        "launch_command": None,
        "resume_command": None,
        "provider_options": {},
        "worktree": {
            "enabled": False,
            "path_template": "../.attestflow-worktrees/{project}/{task_id}-{run_id}",
        },
    },
    "autopilot": {
        "max_repair_attempts": 1,
        "default_limit": 1,
        "max_steps": 1,
        "max_loop_cycles": 1,
        "loop_interval_seconds": 0,
    },
    "capabilities": {
        "planner": {
            "agent_provider": "command",
            "command": None,
        },
        "bdd": {"agent_provider": "command", "command": None},
        "tdd": {"agent_provider": "command", "command": None},
        "implementer": {"agent_provider": "command", "command": None},
        "reviewer": {"agent_provider": "command", "command": None},
        "verifier": {"agent_provider": "command", "command": None},
        "releaser": {"agent_provider": "command", "command": None},
    },
    "context": {
        "enabled": True,
        "max_tree_entries": 200,
        "max_file_bytes": 4000,
        "documents": [
            "README.md",
            "AGENTS.md",
            "harness.yml",
            "pyproject.toml",
            "package.json",
            "docs/contracts/capability-schema.md",
            "docs/contracts/ci-provider-schema.md",
            "docs/contracts/planner-output-schema.md",
            "docs/contracts/pr-provider-schema.md",
            "docs/contracts/release-provider-schema.md",
            "docs/contracts/session-adapter-schema.md",
            "docs/contracts/task-schema.md",
            "docs/design/universal-harness.md",
        ],
    },
    "integrations": {
        "git_provider": "optional",
        "ci_provider": "optional",
        "pr_provider": "optional",
        "release_provider": "optional",
    },
}


def load_config(root: Path) -> dict[str, Any]:
    config_path = root / "harness.yml"
    if not config_path.exists():
        config = deepcopy(DEFAULT_CONFIG)
        config["root"] = root
        return config
    config = load_data(config_path)
    merged = _merge_dicts(DEFAULT_CONFIG, config)
    merged["root"] = root
    return merged


def validate_config(config: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in ("schema_version", "project", "paths", "commands", "policies"):
        if key not in config:
            errors.append(f"missing required config section: {key}")
    if config.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    for key in ("tasks", "runs"):
        if not isinstance(config.get("paths", {}).get(key), str):
            errors.append(f"paths.{key} must be a string")
    ci_runs = config.get("paths", {}).get("ci_runs")
    if ci_runs is not None and not isinstance(ci_runs, str):
        errors.append("paths.ci_runs must be a string")
    autopilot_runs = config.get("paths", {}).get("autopilot_runs")
    if autopilot_runs is not None and not isinstance(autopilot_runs, str):
        errors.append("paths.autopilot_runs must be a string")
    pr_runs = config.get("paths", {}).get("pr_runs")
    if pr_runs is not None and not isinstance(pr_runs, str):
        errors.append("paths.pr_runs must be a string")
    release_runs = config.get("paths", {}).get("release_runs")
    if release_runs is not None and not isinstance(release_runs, str):
        errors.append("paths.release_runs must be a string")
    sessions = config.get("sessions", {})
    if sessions is not None and not isinstance(sessions, dict):
        errors.append("sessions must be a mapping")
        sessions = {}
    launch_command = sessions.get("launch_command") if isinstance(sessions, dict) else None
    if launch_command is not None and not isinstance(launch_command, str):
        errors.append("sessions.launch_command must be a string or null")
    resume_command = sessions.get("resume_command") if isinstance(sessions, dict) else None
    if resume_command is not None and not isinstance(resume_command, str):
        errors.append("sessions.resume_command must be a string or null")
    agent_provider = sessions.get("agent_provider") if isinstance(sessions, dict) else None
    if agent_provider is not None and not isinstance(agent_provider, str):
        errors.append("sessions.agent_provider must be a string")
    role = sessions.get("role") if isinstance(sessions, dict) else None
    if role is not None and not isinstance(role, str):
        errors.append("sessions.role must be a string")
    provider_options = sessions.get("provider_options") if isinstance(sessions, dict) else None
    if provider_options is not None and not isinstance(provider_options, dict):
        errors.append("sessions.provider_options must be a mapping")
    if isinstance(provider_options, dict):
        _validate_timeout_seconds(
            errors,
            "sessions.provider_options.timeout_seconds",
            provider_options.get("timeout_seconds"),
        )
    worktree = sessions.get("worktree") if isinstance(sessions, dict) else None
    if worktree is not None:
        if not isinstance(worktree, dict):
            errors.append("sessions.worktree must be a mapping")
        else:
            enabled = worktree.get("enabled")
            if enabled is not None and not isinstance(enabled, bool):
                errors.append("sessions.worktree.enabled must be a boolean")
            path_template = worktree.get("path_template")
            if path_template is not None and not isinstance(path_template, str):
                errors.append("sessions.worktree.path_template must be a string or null")
    capabilities = config.get("capabilities", {})
    autopilot = config.get("autopilot", {})
    if autopilot is not None and not isinstance(autopilot, dict):
        errors.append("autopilot must be a mapping")
    elif isinstance(autopilot, dict):
        max_repair_attempts = autopilot.get("max_repair_attempts")
        if max_repair_attempts is not None and (type(max_repair_attempts) is not int or max_repair_attempts <= 0):
            errors.append("autopilot.max_repair_attempts must be a positive integer")
        default_limit = autopilot.get("default_limit")
        if default_limit is not None and (type(default_limit) is not int or default_limit <= 0):
            errors.append("autopilot.default_limit must be a positive integer or null")
        max_steps = autopilot.get("max_steps")
        if max_steps is not None and (type(max_steps) is not int or max_steps <= 0):
            errors.append("autopilot.max_steps must be a positive integer")
        max_loop_cycles = autopilot.get("max_loop_cycles")
        if max_loop_cycles is not None and (type(max_loop_cycles) is not int or max_loop_cycles <= 0):
            errors.append("autopilot.max_loop_cycles must be a positive integer")
        loop_interval_seconds = autopilot.get("loop_interval_seconds")
        if loop_interval_seconds is not None and (
            not isinstance(loop_interval_seconds, (int, float))
            or isinstance(loop_interval_seconds, bool)
            or loop_interval_seconds < 0
        ):
            errors.append("autopilot.loop_interval_seconds must be a non-negative number")
        resources = autopilot.get("resources", autopilot.get("resource_budget"))
        if resources is not None:
            if not isinstance(resources, dict):
                errors.append("autopilot.resources must be a mapping")
            else:
                for key in ("model_concurrency", "max_test_cost", "ci_queue"):
                    value = resources.get(key)
                    if value is not None and (type(value) is not int or value <= 0):
                        errors.append(f"autopilot.resources.{key} must be a positive integer")
    if isinstance(capabilities, dict):
        for name, capability in capabilities.items():
            if not isinstance(capability, dict):
                errors.append(f"capabilities.{name} must be a mapping")
                continue
            command = capability.get("command")
            if command is not None and not isinstance(command, str):
                errors.append(f"capabilities.{name}.command must be a string or null")
            agent_provider = capability.get("agent_provider")
            if agent_provider is not None and not isinstance(agent_provider, str):
                errors.append(f"capabilities.{name}.agent_provider must be a string")
            provider_options = capability.get("provider_options")
            if provider_options is not None and not isinstance(provider_options, dict):
                errors.append(f"capabilities.{name}.provider_options must be a mapping")
            _validate_timeout_seconds(errors, f"capabilities.{name}.timeout_seconds", capability.get("timeout_seconds"))
            if isinstance(provider_options, dict):
                _validate_timeout_seconds(
                    errors,
                    f"capabilities.{name}.provider_options.timeout_seconds",
                    provider_options.get("timeout_seconds"),
                )
    context = config.get("context", {})
    if context is not None and not isinstance(context, dict):
        errors.append("context must be a mapping")
    elif isinstance(context, dict):
        enabled = context.get("enabled")
        if enabled is not None and not isinstance(enabled, bool):
            errors.append("context.enabled must be a boolean")
        for key in ("max_tree_entries", "max_file_bytes"):
            value = context.get(key)
            if value is not None and (type(value) is not int or value <= 0):
                errors.append(f"context.{key} must be a positive integer")
        for key in ("documents", "focus_files"):
            value = context.get(key)
            if value is not None and not _is_string_or_string_list(value):
                errors.append(f"context.{key} must be a string or list of strings")
    integrations = config.get("integrations", {})
    if integrations is not None and not isinstance(integrations, dict):
        errors.append("integrations must be a mapping")
    elif isinstance(integrations, dict):
        _validate_provider_config(errors, integrations, "ci_provider")
        _validate_provider_config(errors, integrations, "pr_provider")
        _validate_provider_config(errors, integrations, "release_provider")
    return errors


def _merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in base.items():
        if isinstance(value, dict):
            result[key] = _merge_dicts(value, override.get(key, {}))
        else:
            result[key] = override.get(key, value)
    for key, value in override.items():
        if key not in result:
            result[key] = value
    return result


def _is_string_or_string_list(value: Any) -> bool:
    return isinstance(value, str) or (isinstance(value, list) and all(isinstance(item, str) for item in value))


def _validate_provider_config(errors: list[str], integrations: dict[str, Any], key: str) -> None:
    provider_config = integrations.get(key)
    if provider_config is None or provider_config == "optional":
        return
    if not isinstance(provider_config, dict):
        errors.append(f"integrations.{key} must be a mapping or optional")
        return
    provider = provider_config.get("provider")
    if provider is not None and not isinstance(provider, str):
        errors.append(f"integrations.{key}.provider must be a string")
    command = provider_config.get("command")
    if command is not None and not isinstance(command, str):
        errors.append(f"integrations.{key}.command must be a string or null")
    provider_options = provider_config.get("provider_options")
    if provider_options is not None and not isinstance(provider_options, dict):
        errors.append(f"integrations.{key}.provider_options must be a mapping")
    _validate_timeout_seconds(errors, f"integrations.{key}.timeout_seconds", provider_config.get("timeout_seconds"))
    if isinstance(provider_options, dict):
        _validate_timeout_seconds(
            errors,
            f"integrations.{key}.provider_options.timeout_seconds",
            provider_options.get("timeout_seconds"),
        )


def _validate_timeout_seconds(errors: list[str], field: str, value: Any) -> None:
    if value is None:
        return
    if type(value) not in {int, float} or value <= 0:
        errors.append(f"{field} must be a positive number")
