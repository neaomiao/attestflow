from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shlex
import sys
from typing import Any

from .contracts import PR_STATUSES, raise_contract_errors, validate_pr_output
from .evidence import utc_timestamp
from .io import dump_data
from .provider_commands import provider_timeout_seconds, run_provider_json_command, shell_command_exists


BUILTIN_PR_PROVIDERS: dict[str, dict[str, str]] = {
    "github": {"command": "gh", "description": "GitHub pull requests via attestflow.pr_adapters."},
    "gitlab": {"command": "glab", "description": "GitLab merge requests via attestflow.pr_adapters."},
}


@dataclass(frozen=True)
class PRStatusResult:
    status: str
    output: dict[str, Any]
    run_path: Path


def list_pr_providers() -> list[dict[str, str]]:
    return [
        {"name": name, "command": item["command"], "description": item["description"]}
        for name, item in sorted(BUILTIN_PR_PROVIDERS.items())
    ]


def run_pr_status(
    root: Path,
    config: dict[str, Any],
    *,
    task_id: str | None = None,
    command: str | None = None,
) -> PRStatusResult:
    return _run_pr_action(root, config, action="status", task_id=task_id, command=command)


def run_pr_ensure(
    root: Path,
    config: dict[str, Any],
    *,
    task_id: str | None = None,
    command: str | None = None,
) -> PRStatusResult:
    return _run_pr_action(root, config, action="ensure", task_id=task_id, command=command)


def _run_pr_action(
    root: Path,
    config: dict[str, Any],
    *,
    action: str,
    task_id: str | None = None,
    command: str | None = None,
) -> PRStatusResult:
    provider_config = _pr_provider_config(config)
    provider = str(provider_config.get("provider") or ("command" if command else ""))
    if not provider:
        raise ValueError("integrations.pr_provider must be configured or passed with --command")
    pr_command = command or _configured_command(provider, provider_config)
    if not pr_command:
        raise ValueError(f"PR provider command must be configured for {provider}")
    if not shell_command_exists(pr_command):
        raise ValueError(f"PR provider command not found for {provider}: {pr_command}")

    run_path = _new_pr_run_path(root, config)
    payload = _pr_input(root, config, provider, provider_config, action=action, task_id=task_id)
    output = run_provider_json_command(
        root,
        pr_command,
        payload,
        run_path,
        "PR",
        timeout_seconds=provider_timeout_seconds(provider_config),
    )
    dump_data(output, run_path / "output.json")
    _validate_pr_output(output, run_path / "output.json")
    return PRStatusResult(status=str(output["status"]), output=output, run_path=run_path)


def _pr_provider_config(config: dict[str, Any]) -> dict[str, Any]:
    integrations = config.get("integrations", {})
    pr_provider = integrations.get("pr_provider", {}) if isinstance(integrations, dict) else {}
    return pr_provider if isinstance(pr_provider, dict) else {}


def _configured_command(provider: str, provider_config: dict[str, Any]) -> str | None:
    command = provider_config.get("command")
    if command:
        return str(command)
    if provider in BUILTIN_PR_PROVIDERS:
        return _builtin_pr_adapter_command()
    return None


def _pr_input(
    root: Path,
    config: dict[str, Any],
    provider: str,
    provider_config: dict[str, Any],
    *,
    action: str,
    task_id: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "action": action,
        "provider": provider,
        "provider_options": _provider_options(provider_config),
        "security": config.get("security", {}),
        "root": str(root),
        "project": config.get("project", {}),
        "task_id": task_id,
    }


def _provider_options(provider_config: dict[str, Any]) -> dict[str, Any]:
    options = provider_config.get("provider_options", {})
    merged = dict(options) if isinstance(options, dict) else {}
    for key in ("command", "repository", "ensure_args", "status_args", "timeout_seconds"):
        if key in provider_config and key not in merged:
            merged[key] = provider_config[key]
    return merged


def _validate_pr_output(output: dict[str, Any], path: Path | None = None) -> None:
    raise_contract_errors("PR output", "pr-output", validate_pr_output(output, label="PR output"), path)


def _new_pr_run_path(root: Path, config: dict[str, Any]) -> Path:
    run_root = root / str(config.get("paths", {}).get("pr_runs", "harness/pr-runs"))
    run_root.mkdir(parents=True, exist_ok=True)
    path = run_root / f"pr-{utc_timestamp()}"
    suffix = 1
    while path.exists():
        suffix += 1
        path = run_root / f"pr-{utc_timestamp()}-{suffix}"
    path.mkdir(parents=True)
    return path


def _builtin_pr_adapter_command() -> str:
    adapter_path = Path(__file__).resolve().parent / "pr_adapters.py"
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(adapter_path))}"
