from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
from typing import Any

from .io import dump_data, load_data
from .provider_commands import run_provider_json_command


def discover_plugins(root: Path, config: dict[str, Any]) -> dict[str, Any]:
    plugins: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for directory in _plugin_directories(root, config):
        for manifest_path in _manifest_paths(directory):
            try:
                manifest = load_data(manifest_path)
            except (OSError, ValueError) as exc:
                errors.append({"manifest": _relative(root, manifest_path), "error": str(exc)})
                continue
            manifest_errors = _manifest_errors(manifest)
            if manifest_errors:
                errors.append({"manifest": _relative(root, manifest_path), "error": "; ".join(manifest_errors)})
                continue
            plugins.append(_normalized_manifest(root, manifest_path, manifest))
    plugins.sort(key=lambda plugin: (plugin["name"], plugin["version"], plugin["manifest"]))
    return {"schema_version": 1, "plugins": plugins, "errors": errors}


def run_plugin_command(
    root: Path,
    config: dict[str, Any],
    plugin_name: str,
    command_name: str,
    input_payload: dict[str, Any],
) -> dict[str, Any]:
    report = discover_plugins(root, config)
    matches = [plugin for plugin in report["plugins"] if plugin["name"] == plugin_name]
    if not matches:
        raise ValueError(f"plugin not found: {plugin_name}")
    plugin = matches[0]
    commands = plugin.get("commands", {})
    if not isinstance(commands, dict) or command_name not in commands:
        raise ValueError(f"plugin command not found: {plugin_name}.{command_name}")
    run_path = _plugin_run_path(root, config, plugin_name, command_name)
    payload = {
        "schema_version": 1,
        "plugin": plugin,
        "command": command_name,
        "root": str(root),
        "security": config.get("security", {}),
        "input": input_payload,
    }
    output = run_provider_json_command(root, str(commands[command_name]), payload, run_path, f"plugin {plugin_name}.{command_name}")
    dump_data(output, run_path / "output.json")
    return {
        "schema_version": 1,
        "status": str(output.get("status", "passed")),
        "plugin": plugin_name,
        "command": command_name,
        "run_path": _relative(root, run_path),
        "output": output,
    }


def _plugin_directories(root: Path, config: dict[str, Any]) -> list[Path]:
    plugins = config.get("plugins", {})
    directories = plugins.get("directories", ["harness/plugins"]) if isinstance(plugins, dict) else ["harness/plugins"]
    if not isinstance(directories, list):
        directories = ["harness/plugins"]
    result = []
    for item in directories:
        path = Path(str(item))
        result.append(path if path.is_absolute() else root / path)
    return result


def _manifest_paths(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    paths: list[Path] = []
    direct = directory / "plugin.json"
    if direct.exists():
        paths.append(direct)
    paths.extend(path for path in sorted(directory.glob("*/plugin.json")) if path != direct)
    return paths


def _manifest_errors(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if manifest.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    for key in ("name", "version"):
        if not str(manifest.get(key, "")).strip():
            errors.append(f"{key} must be non-empty")
    for key in ("capabilities", "adapters"):
        value = manifest.get(key, [])
        if not isinstance(value, list) or not all(str(item).strip() for item in value):
            errors.append(f"{key} must be a list of strings")
    providers = manifest.get("providers", {})
    if providers is not None and not isinstance(providers, dict):
        errors.append("providers must be a mapping")
    elif isinstance(providers, dict):
        for provider_type, names in providers.items():
            if not isinstance(names, list) or not all(str(item).strip() for item in names):
                errors.append(f"providers.{provider_type} must be a list of strings")
    commands = manifest.get("commands", {})
    if commands is not None:
        if not isinstance(commands, dict):
            errors.append("commands must be a mapping")
        else:
            for name, command in commands.items():
                if not str(name).strip() or not isinstance(command, str) or not command.strip():
                    errors.append("commands entries must map non-empty names to command strings")
    return errors


def _normalized_manifest(root: Path, manifest_path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    providers = manifest.get("providers", {})
    return {
        "schema_version": 1,
        "name": str(manifest["name"]),
        "version": str(manifest["version"]),
        "manifest": _relative(root, manifest_path),
        "capabilities": [str(item) for item in manifest.get("capabilities", [])],
        "providers": providers if isinstance(providers, dict) else {},
        "adapters": [str(item) for item in manifest.get("adapters", [])],
        "commands": dict(manifest.get("commands", {})) if isinstance(manifest.get("commands", {}), dict) else {},
    }


def _plugin_run_path(root: Path, config: dict[str, Any], plugin_name: str, command_name: str) -> Path:
    paths = config.get("paths", {}) if isinstance(config.get("paths"), dict) else {}
    run_root = root / str(paths.get("plugin_runs", "harness/plugin-runs"))
    safe_plugin = _safe_segment(plugin_name)
    safe_command = _safe_segment(command_name)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = run_root / f"{safe_plugin}-{safe_command}-{timestamp}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_segment(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value).strip("-") or "plugin"


def _relative(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)
