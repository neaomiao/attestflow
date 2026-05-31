from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any

from .ci import BUILTIN_CI_PROVIDERS
from .config import validate_config
from .provider_commands import shell_command_exists
from .sessions import list_session_providers
from .tasks import TASK_STATES, iter_tasks, validate_task


def autonomy_doctor(root: Path, config: dict[str, Any]) -> dict[str, Any]:
    checks = [
        _config_check(config),
        _runtime_layout_check(root, config),
        _provider_command_check(config),
        _git_repository_check(root),
        _git_remote_check(root, config),
        _workspace_clean_check(root),
        _permission_boundary_check(root, config),
    ]
    status = "blocked" if any(check["status"] == "blocked" for check in checks) else "passed"
    return {"schema_version": 1, "status": status, "checks": checks}


def _check(name: str, status: str, summary: str) -> dict[str, str]:
    return {"name": name, "status": status, "summary": summary}


def _config_check(config: dict[str, Any]) -> dict[str, str]:
    errors = validate_config(config)
    if errors:
        return _check("config", "blocked", "; ".join(errors))
    return _check("config", "passed", "harness configuration is valid")


def _runtime_layout_check(root: Path, config: dict[str, Any]) -> dict[str, str]:
    paths = config.get("paths", {}) if isinstance(config.get("paths"), dict) else {}
    task_root = root / str(paths.get("tasks", "harness/tasks"))
    missing: list[str] = []
    for state in TASK_STATES:
        if not (task_root / state).is_dir():
            missing.append(str(task_root / state))
    for key, default in (
        ("runs", "harness/runs"),
        ("locks", "harness/locks"),
        ("capability_runs", "harness/capability-runs"),
        ("autopilot_runs", "harness/autopilot-runs"),
        ("ci_runs", "harness/ci-runs"),
        ("pr_runs", "harness/pr-runs"),
        ("release_runs", "harness/release-runs"),
    ):
        if not (root / str(paths.get(key, default))).is_dir():
            missing.append(str(root / str(paths.get(key, default))))
    task_errors: list[str] = []
    if task_root.exists():
        for record in iter_tasks(root, config):
            task_errors.extend(f"{record.path}: {error}" for error in validate_task(record.task, directory_state=record.path.parent.name))
    if missing or task_errors:
        details = [*(f"missing {path}" for path in missing), *task_errors]
        return _check("runtime_layout", "blocked", "; ".join(details))
    return _check("runtime_layout", "passed", "runtime directories and task schema are valid")


def _provider_command_check(config: dict[str, Any]) -> dict[str, str]:
    missing: list[str] = []
    sessions = config.get("sessions", {}) if isinstance(config.get("sessions"), dict) else {}
    session_provider = str(sessions.get("agent_provider", "command"))
    session_commands = {provider["name"]: provider["command"] for provider in list_session_providers()}
    if session_provider in session_commands:
        options = sessions.get("provider_options", {}) if isinstance(sessions.get("provider_options"), dict) else {}
        command = str(options.get("command") or session_commands[session_provider])
        if not shell_command_exists(command):
            missing.append(f"session provider {session_provider}: {command}")
    integrations = config.get("integrations", {}) if isinstance(config.get("integrations"), dict) else {}
    ci_provider = integrations.get("ci_provider", {}) if isinstance(integrations.get("ci_provider"), dict) else {}
    if ci_provider:
        provider = str(ci_provider.get("provider", "command"))
        command = ci_provider.get("command")
        if not command and provider in BUILTIN_CI_PROVIDERS:
            command = BUILTIN_CI_PROVIDERS[provider]["command"]
        if not command or not shell_command_exists(str(command)):
            missing.append(f"CI provider {provider}: {command or '<missing command>'}")
    for key, label in (("pr_provider", "PR"), ("release_provider", "Release")):
        provider_config = integrations.get(key, {}) if isinstance(integrations.get(key), dict) else {}
        if not provider_config:
            continue
        provider = str(provider_config.get("provider", "command"))
        command = provider_config.get("command")
        if not command or not shell_command_exists(str(command)):
            missing.append(f"{label} provider {provider}: {command or '<missing command>'}")
    if missing:
        return _check("provider_commands", "blocked", "; ".join(missing))
    return _check("provider_commands", "passed", "configured provider commands are executable")


def _git_repository_check(root: Path) -> dict[str, str]:
    completed = _git(root, "rev-parse", "--show-toplevel")
    if completed.returncode != 0:
        return _check("git_repository", "blocked", "project root is not a git repository")
    return _check("git_repository", "passed", "project root is a git repository")


def _git_remote_check(root: Path, config: dict[str, Any]) -> dict[str, str]:
    if not _delivery_provider_configured(config):
        return _check("git_remote", "skipped", "no CI, PR, or release provider requires a git remote")
    if _git(root, "rev-parse", "--show-toplevel").returncode != 0:
        return _check("git_remote", "blocked", "delivery providers are configured but project is not a git repository")
    completed = _git(root, "remote")
    remotes = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if not remotes:
        return _check("git_remote", "blocked", "PR provider, CI provider, or release provider is configured but git remote is missing")
    return _check("git_remote", "passed", "git remote is configured: " + ", ".join(remotes))


def _workspace_clean_check(root: Path) -> dict[str, str]:
    if _git(root, "rev-parse", "--show-toplevel").returncode != 0:
        return _check("workspace_clean", "skipped", "not a git repository")
    completed = _git(root, "status", "--porcelain")
    if completed.returncode != 0:
        return _check("workspace_clean", "blocked", "could not read git workspace status")
    if completed.stdout.strip():
        return _check("workspace_clean", "blocked", "workspace has uncommitted changes")
    return _check("workspace_clean", "passed", "workspace is clean")


def _permission_boundary_check(root: Path, config: dict[str, Any]) -> dict[str, str]:
    paths = config.get("paths", {}) if isinstance(config.get("paths"), dict) else {}
    writable_paths = [
        root / str(paths.get(key, default))
        for key, default in (
            ("runs", "harness/runs"),
            ("locks", "harness/locks"),
            ("capability_runs", "harness/capability-runs"),
            ("autopilot_runs", "harness/autopilot-runs"),
        )
    ]
    missing_parent = [str(path.parent) for path in writable_paths if not path.exists() and not path.parent.exists()]
    if missing_parent:
        return _check("permission_boundaries", "blocked", "runtime parent directories are missing: " + ", ".join(missing_parent))
    return _check("permission_boundaries", "passed", "runtime paths stay under configured harness directories")


def _delivery_provider_configured(config: dict[str, Any]) -> bool:
    integrations = config.get("integrations", {}) if isinstance(config.get("integrations"), dict) else {}
    for key in ("ci_provider", "pr_provider", "release_provider"):
        provider_config = integrations.get(key)
        if isinstance(provider_config, dict) and bool(provider_config.get("provider") or provider_config.get("command")):
            return True
    return False


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=False)
