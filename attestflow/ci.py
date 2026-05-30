from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import shlex
import shutil
import subprocess
import sys
from typing import Any

from .evidence import utc_timestamp
from .io import dump_data


BUILTIN_CI_PROVIDERS: dict[str, dict[str, str]] = {
    "github-actions": {"command": "gh", "description": "GitHub Actions via attestflow.ci_adapters."},
}

CI_STATUSES = {"passed", "failed", "running", "queued", "cancelled", "skipped", "blocked", "unknown"}


@dataclass(frozen=True)
class CIStatusResult:
    status: str
    output: dict[str, Any]
    run_path: Path


def list_ci_providers() -> list[dict[str, str]]:
    return [
        {"name": name, "command": item["command"], "description": item["description"]}
        for name, item in sorted(BUILTIN_CI_PROVIDERS.items())
    ]


def run_ci_status(root: Path, config: dict[str, Any], *, command: str | None = None) -> CIStatusResult:
    provider_config = _ci_provider_config(config)
    provider = str(provider_config.get("provider") or ("command" if command else ""))
    if not provider:
        raise ValueError("integrations.ci_provider must be configured or passed with --command")
    ci_command = command or _configured_command(provider, provider_config)
    if not ci_command:
        raise ValueError(f"CI provider command must be configured for {provider}")
    if not _shell_command_exists(ci_command):
        raise ValueError(f"CI provider command not found for {provider}: {ci_command}")

    run_path = _new_ci_run_path(root, config)
    payload = _ci_input(root, config, provider, provider_config)
    output = _run_json_command(root, ci_command, payload, run_path)
    _validate_ci_output(output)
    dump_data(output, run_path / "output.json")
    return CIStatusResult(status=str(output["status"]), output=output, run_path=run_path)


def _ci_provider_config(config: dict[str, Any]) -> dict[str, Any]:
    integrations = config.get("integrations", {})
    ci_provider = integrations.get("ci_provider", {}) if isinstance(integrations, dict) else {}
    return ci_provider if isinstance(ci_provider, dict) else {}


def _configured_command(provider: str, provider_config: dict[str, Any]) -> str | None:
    command = provider_config.get("command")
    if command:
        return str(command)
    if provider in BUILTIN_CI_PROVIDERS:
        return _builtin_ci_adapter_command()
    return None


def _ci_input(root: Path, config: dict[str, Any], provider: str, provider_config: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "provider": provider,
        "provider_options": _provider_options(provider_config),
        "root": str(root),
        "project": config.get("project", {}),
    }


def _provider_options(provider_config: dict[str, Any]) -> dict[str, Any]:
    options = provider_config.get("provider_options", {})
    merged = dict(options) if isinstance(options, dict) else {}
    for key in ("command", "repository", "status_args", "timeout_seconds"):
        if key in provider_config and key not in merged:
            merged[key] = provider_config[key]
    return merged


def _run_json_command(root: Path, command: str, payload: dict[str, Any], run_path: Path) -> dict[str, Any]:
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
    (run_path / "stdout.log").write_text(completed.stdout or "", encoding="utf-8")
    (run_path / "stderr.log").write_text(completed.stderr or "", encoding="utf-8")
    if completed.returncode != 0:
        raise ValueError(f"CI provider command failed with exit code {completed.returncode}")
    try:
        output = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"CI provider command did not return valid JSON: {exc}") from exc
    if not isinstance(output, dict):
        raise ValueError("CI provider command must return a JSON object")
    return output


def _validate_ci_output(output: dict[str, Any]) -> None:
    if output.get("schema_version") != 1:
        raise ValueError("CI output schema_version must be 1")
    if output.get("status") not in CI_STATUSES:
        raise ValueError("CI output status must be one of: " + ", ".join(sorted(CI_STATUSES)))
    if not str(output.get("summary", "")).strip():
        raise ValueError("CI output summary must be non-empty")
    checks = output.get("checks", [])
    if not isinstance(checks, list):
        raise ValueError("CI output checks must be a list")


def _new_ci_run_path(root: Path, config: dict[str, Any]) -> Path:
    run_root = root / str(config.get("paths", {}).get("ci_runs", "harness/ci-runs"))
    run_root.mkdir(parents=True, exist_ok=True)
    path = run_root / f"ci-{utc_timestamp()}"
    suffix = 1
    while path.exists():
        suffix += 1
        path = run_root / f"ci-{utc_timestamp()}-{suffix}"
    path.mkdir(parents=True)
    return path


def _builtin_ci_adapter_command() -> str:
    adapter_path = Path(__file__).resolve().parent / "ci_adapters.py"
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(adapter_path))}"


def _shell_command_exists(command: str) -> bool:
    try:
        executable = shlex.split(command)[0]
    except (ValueError, IndexError):
        return False
    return bool(shutil.which(executable) or Path(executable).exists())
