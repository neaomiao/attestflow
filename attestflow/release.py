from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shlex
import sys
from typing import Any

from .contracts import RELEASE_STATUSES, raise_contract_errors, validate_release_output
from .evidence import utc_timestamp
from .io import dump_data, load_data
from .provider_commands import provider_timeout_seconds, run_provider_json_command, shell_command_exists
from .tasks import iter_tasks, validate_task
from .token_economy import summarize_evidence_reference


BUILTIN_RELEASE_PROVIDERS: dict[str, dict[str, str]] = {
    "github-release": {"command": "gh", "description": "GitHub releases via attestflow.release_adapters."},
    "gitlab-release": {"command": "glab", "description": "GitLab releases via attestflow.release_adapters."},
    "linear": {"command": "linear", "description": "Linear release tracking via attestflow.release_adapters."},
    "jira": {"command": "jira", "description": "Jira release tracking via attestflow.release_adapters."},
    "buildkite": {"command": "buildkite-agent", "description": "Buildkite delivery status via attestflow.release_adapters."},
    "circleci": {"command": "circleci", "description": "CircleCI delivery status via attestflow.release_adapters."},
    "self-hosted-release": {"command": "attestflow-release", "description": "Self-hosted release systems via attestflow.release_adapters."},
}


@dataclass(frozen=True)
class ReleaseStatusResult:
    status: str
    output: dict[str, Any]
    run_path: Path


def list_release_providers() -> list[dict[str, str]]:
    return [
        {"name": name, "command": item["command"], "description": item["description"]}
        for name, item in sorted(BUILTIN_RELEASE_PROVIDERS.items())
    ]


def run_release_status(
    root: Path,
    config: dict[str, Any],
    *,
    command: str | None = None,
    done_tasks: list[str] | None = None,
    release_handoff: str | None = None,
    release_handoff_tasks: list[str] | None = None,
) -> ReleaseStatusResult:
    provider_config = _release_provider_config(config)
    provider = str(provider_config.get("provider") or ("command" if command else ""))
    if not provider:
        raise ValueError("integrations.release_provider must be configured or passed with --command")
    release_command = command or _configured_command(provider, provider_config)
    if not release_command:
        raise ValueError(f"Release provider command must be configured for {provider}")
    if not shell_command_exists(release_command):
        raise ValueError(f"Release provider command not found for {provider}: {release_command}")

    release_tasks = done_tasks if done_tasks is not None else _completed_task_ids(root, config)
    payload = _release_input(
        root,
        config,
        provider,
        provider_config,
        release_tasks,
        release_handoff=release_handoff,
        release_handoff_tasks=release_handoff_tasks,
    )
    run_path = _new_release_run_path(root, config)
    output = run_provider_json_command(
        root,
        release_command,
        payload,
        run_path,
        "Release",
        timeout_seconds=provider_timeout_seconds(provider_config),
    )
    dump_data(output, run_path / "output.json")
    _validate_release_output(output, run_path / "output.json")
    return ReleaseStatusResult(status=str(output["status"]), output=output, run_path=run_path)


def _release_provider_config(config: dict[str, Any]) -> dict[str, Any]:
    integrations = config.get("integrations", {})
    release_provider = integrations.get("release_provider", {}) if isinstance(integrations, dict) else {}
    return release_provider if isinstance(release_provider, dict) else {}


def _configured_command(provider: str, provider_config: dict[str, Any]) -> str | None:
    command = provider_config.get("command")
    if command:
        return str(command)
    if provider in BUILTIN_RELEASE_PROVIDERS:
        return _builtin_release_adapter_command()
    return None


def _release_input(
    root: Path,
    config: dict[str, Any],
    provider: str,
    provider_config: dict[str, Any],
    done_tasks: list[str],
    *,
    release_handoff: str | None = None,
    release_handoff_tasks: list[str] | None = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": 1,
        "provider": provider,
        "provider_options": _provider_options(provider_config),
        "security": config.get("security", {}),
        "root": str(root),
        "project": config.get("project", {}),
        "done_tasks": done_tasks,
        "tasks": release_task_summaries(root, config, done_tasks),
    }
    if release_handoff:
        payload["release_handoff"] = _release_handoff_summary(root, release_handoff, release_handoff_tasks or [])
    return payload


def _completed_task_ids(root: Path, config: dict[str, Any]) -> list[str]:
    return [str(task.get("id")) for task in _completed_tasks(root, config)]


def _task_summaries(root: Path, config: dict[str, Any], done_tasks: list[str]) -> list[dict[str, Any]]:
    return release_task_summaries(root, config, done_tasks)


def release_task_summaries(root: Path, config: dict[str, Any], done_tasks: list[str]) -> list[dict[str, Any]]:
    summaries = {
        str(task.get("id")): _task_summary(root, config, task)
        for task in _completed_tasks(root, config)
    }
    if done_tasks:
        missing = [task_id for task_id in done_tasks if task_id not in summaries]
        if missing:
            raise ValueError(f"release task not found: {', '.join(missing)}")
        return [summaries[task_id] for task_id in done_tasks]
    return [summaries[task_id] for task_id in sorted(summaries)]


def _release_handoff_summary(root: Path, release_handoff: str, done_tasks: list[str]) -> dict[str, Any]:
    path = Path(release_handoff)
    absolute_path = path if path.is_absolute() else root / path
    summary: dict[str, Any] = {
        "path": release_handoff,
        "exists": absolute_path.exists(),
        "tasks": done_tasks,
    }
    if absolute_path.exists():
        try:
            summary["output"] = load_data(absolute_path)
        except ValueError:
            summary["output"] = None
    return summary


def _completed_tasks(root: Path, config: dict[str, Any]) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    try:
        records = iter_tasks(root, config)
    except (OSError, ValueError) as exc:
        raise ValueError(f"failed to load completed tasks: {exc}") from exc
    for record in records:
        task = record.task
        if task.get("state") in {"done", "archived"} or record.path.parent.name in {"done", "archived"}:
            errors = validate_task(task, directory_state=record.path.parent.name)
            if errors:
                raise ValueError(f"invalid completed task {record.path}: {'; '.join(errors)}")
        if task.get("state") in {"done", "archived"}:
            tasks.append(task)
    return tasks


def _task_summary(root: Path, config: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": task.get("id"),
        "title": task.get("title"),
        "state": task.get("state"),
        "type": task.get("type"),
        "purpose": task.get("purpose"),
        "scope": task.get("scope", []),
        "acceptance": task.get("acceptance", []),
        "links": task.get("links", {}),
        "evidence": _evidence_summary(root, config, task.get("evidence", {})),
    }


def _evidence_summary(root: Path, config: dict[str, Any], evidence: Any) -> dict[str, Any]:
    if not isinstance(evidence, dict):
        return {}
    summary: dict[str, Any] = {}
    for key, value in evidence.items():
        if value is None:
            continue
        if key == "run_id":
            summary[key] = str(value)
            continue
        summary[str(key)] = _evidence_value(root, config, value)
    return summary


def _evidence_value(root: Path, config: dict[str, Any], value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _evidence_value(root, config, item) for key, item in value.items() if item is not None}
    if not isinstance(value, str):
        return value
    return summarize_evidence_reference(root, value, config)


def _provider_options(provider_config: dict[str, Any]) -> dict[str, Any]:
    options = provider_config.get("provider_options", {})
    merged = dict(options) if isinstance(options, dict) else {}
    for key in ("command", "repository", "release_args", "timeout_seconds"):
        if key in provider_config and key not in merged:
            merged[key] = provider_config[key]
    return merged


def _validate_release_output(output: dict[str, Any], path: Path | None = None) -> None:
    raise_contract_errors(
        "Release output",
        "release-output",
        validate_release_output(output, label="Release output"),
        path,
    )


def _new_release_run_path(root: Path, config: dict[str, Any]) -> Path:
    run_root = root / str(config.get("paths", {}).get("release_runs", "harness/release-runs"))
    run_root.mkdir(parents=True, exist_ok=True)
    path = run_root / f"release-{utc_timestamp()}"
    suffix = 1
    while path.exists():
        suffix += 1
        path = run_root / f"release-{utc_timestamp()}-{suffix}"
    path.mkdir(parents=True)
    return path


def _builtin_release_adapter_command() -> str:
    adapter_path = Path(__file__).resolve().parent / "release_adapters.py"
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(adapter_path))}"
