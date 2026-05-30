from __future__ import annotations

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
        "worktree": {"enabled": False, "path_template": None},
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
            "docs/contracts/planner-output-schema.md",
            "docs/contracts/session-adapter-schema.md",
            "docs/contracts/task-schema.md",
            "docs/design/universal-harness.md",
        ],
    },
}


def load_config(root: Path) -> dict[str, Any]:
    config_path = root / "harness.yml"
    if not config_path.exists():
        config = {**DEFAULT_CONFIG}
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
    capabilities = config.get("capabilities", {})
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
