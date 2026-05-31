from __future__ import annotations

from pathlib import Path
from typing import Any

from .io import load_data


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
    }


def _relative(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)
