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
        "provider": "command",
        "role": "worker_agent",
        "launch_command": None,
        "resume_command": None,
        "worktree": {"enabled": False, "path_template": None},
    },
    "capabilities": {
        "planner": {
            "provider": "command",
            "command": None,
        },
        "bdd": {"provider": "command", "command": None},
        "tdd": {"provider": "command", "command": None},
        "implementer": {"provider": "command", "command": None},
        "reviewer": {"provider": "command", "command": None},
        "verifier": {"provider": "command", "command": None},
        "releaser": {"provider": "command", "command": None},
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
    launch_command = config.get("sessions", {}).get("launch_command")
    if launch_command is not None and not isinstance(launch_command, str):
        errors.append("sessions.launch_command must be a string or null")
    capabilities = config.get("capabilities", {})
    if isinstance(capabilities, dict):
        for name, capability in capabilities.items():
            if not isinstance(capability, dict):
                errors.append(f"capabilities.{name} must be a mapping")
                continue
            command = capability.get("command")
            if command is not None and not isinstance(command, str):
                errors.append(f"capabilities.{name}.command must be a string or null")
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
