from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shlex
import sys
from typing import Any

from .contracts import raise_contract_errors, validate_git_output
from .evidence import utc_timestamp
from .io import dump_data
from .provider_commands import provider_timeout_seconds, run_provider_json_command, shell_command_exists
from .tasks import iter_tasks


BUILTIN_GIT_PROVIDERS: dict[str, dict[str, str]] = {
    "git": {"command": "git", "description": "Local git commit and push via attestflow.git_adapters."},
}


@dataclass(frozen=True)
class GitPublishResult:
    status: str
    output: dict[str, Any]
    run_path: Path


def list_git_providers() -> list[dict[str, str]]:
    return [
        {"name": name, "command": item["command"], "description": item["description"]}
        for name, item in sorted(BUILTIN_GIT_PROVIDERS.items())
    ]


def run_git_publish(
    root: Path,
    config: dict[str, Any],
    *,
    task_id: str | None = None,
    command: str | None = None,
) -> GitPublishResult:
    provider_config = _git_provider_config(config)
    provider = str(provider_config.get("provider") or ("command" if command else ""))
    if not provider:
        raise ValueError("integrations.git_provider must be configured or passed with --command")
    git_command = command or _configured_command(provider, provider_config)
    if not git_command:
        raise ValueError(f"Git provider command must be configured for {provider}")
    if not shell_command_exists(git_command):
        raise ValueError(f"Git provider command not found for {provider}: {git_command}")

    run_path = _new_git_run_path(root, config)
    payload = _git_input(root, config, provider, provider_config, task_id=task_id)
    output = run_provider_json_command(
        root,
        git_command,
        payload,
        run_path,
        "Git",
        timeout_seconds=provider_timeout_seconds(provider_config),
    )
    dump_data(output, run_path / "output.json")
    _validate_git_output(output, run_path / "output.json")
    return GitPublishResult(status=str(output["status"]), output=output, run_path=run_path)


def _git_provider_config(config: dict[str, Any]) -> dict[str, Any]:
    integrations = config.get("integrations", {})
    git_provider = integrations.get("git_provider", {}) if isinstance(integrations, dict) else {}
    return git_provider if isinstance(git_provider, dict) else {}


def _configured_command(provider: str, provider_config: dict[str, Any]) -> str | None:
    command = provider_config.get("command")
    if command:
        return str(command)
    if provider in BUILTIN_GIT_PROVIDERS:
        return _builtin_git_adapter_command()
    return None


def _git_input(
    root: Path,
    config: dict[str, Any],
    provider: str,
    provider_config: dict[str, Any],
    *,
    task_id: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "action": "publish",
        "provider": provider,
        "provider_options": _provider_options(provider_config),
        "security": config.get("security", {}),
        "root": str(root),
        "project": config.get("project", {}),
        "task_id": task_id,
        "task": _task_summary(root, config, task_id) if task_id else None,
    }


def _task_summary(root: Path, config: dict[str, Any], task_id: str | None) -> dict[str, Any] | None:
    if not task_id:
        return None
    for record in iter_tasks(root, config):
        if record.task.get("id") == task_id:
            task = record.task
            return {
                "id": task.get("id"),
                "title": task.get("title"),
                "state": task.get("state"),
                "files": task.get("files", {}),
                "evidence": task.get("evidence", {}),
            }
    raise FileNotFoundError(f"task not found: {task_id}")


def _provider_options(provider_config: dict[str, Any]) -> dict[str, Any]:
    options = provider_config.get("provider_options", {})
    merged = dict(options) if isinstance(options, dict) else {}
    for key in (
        "command",
        "remote",
        "branch",
        "default_branch",
        "commit_message",
        "push",
        "stage",
        "stage_paths",
        "allow_default_branch",
        "timeout_seconds",
    ):
        if key in provider_config and key not in merged:
            merged[key] = provider_config[key]
    return merged


def _validate_git_output(output: dict[str, Any], path: Path | None = None) -> None:
    raise_contract_errors("Git output", "git-output", validate_git_output(output, label="Git output"), path)


def _new_git_run_path(root: Path, config: dict[str, Any]) -> Path:
    run_root = root / str(config.get("paths", {}).get("git_runs", "harness/git-runs"))
    run_root.mkdir(parents=True, exist_ok=True)
    path = run_root / f"git-{utc_timestamp()}"
    suffix = 1
    while path.exists():
        suffix += 1
        path = run_root / f"git-{utc_timestamp()}-{suffix}"
    path.mkdir(parents=True)
    return path


def _builtin_git_adapter_command() -> str:
    adapter_path = Path(__file__).resolve().parent / "git_adapters.py"
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(adapter_path))}"
