from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .config import validate_config
from .io import dump_data, load_data


def list_policy_packs(root: Path, config: dict[str, Any]) -> dict[str, Any]:
    packs: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for directory in _policy_directories(root, config):
        for path in _policy_paths(directory):
            try:
                pack = load_data(path)
            except (OSError, ValueError) as exc:
                errors.append({"path": _relative(root, path), "error": str(exc)})
                continue
            pack_errors = _policy_errors(pack)
            if pack_errors:
                errors.append({"path": _relative(root, path), "error": "; ".join(pack_errors)})
                continue
            packs.append(_summary(root, path, pack))
    packs.sort(key=lambda item: (item["name"], item["version"], item["path"]))
    return {"schema_version": 1, "packs": packs, "errors": errors}


def validate_policy_pack(root: Path, config: dict[str, Any], name: str) -> dict[str, Any]:
    pack, path = _find_policy_pack(root, config, name)
    errors = _policy_errors(pack)
    merged = _merge_dicts(config, pack.get("config", {}) if isinstance(pack.get("config"), dict) else {})
    errors.extend(validate_config(merged))
    return {
        "schema_version": 1,
        "name": name,
        "path": _relative(root, path),
        "status": "passed" if not errors else "failed",
        "errors": errors,
    }


def apply_policy_pack(
    root: Path,
    config: dict[str, Any],
    name: str,
    *,
    output_path: Path | None = None,
) -> dict[str, Any]:
    validation = validate_policy_pack(root, config, name)
    if validation["status"] != "passed":
        raise ValueError("; ".join(validation["errors"]))
    pack, _ = _find_policy_pack(root, config, name)
    merged = _merge_dicts(config, pack.get("config", {}) if isinstance(pack.get("config"), dict) else {})
    merged.pop("root", None)
    if output_path is not None:
        dump_data(merged, output_path)
    return {
        "schema_version": 1,
        "name": name,
        "status": "passed",
        "output_path": str(output_path) if output_path else None,
        "config": merged,
    }


def _find_policy_pack(root: Path, config: dict[str, Any], name: str) -> tuple[dict[str, Any], Path]:
    for directory in _policy_directories(root, config):
        for path in _policy_paths(directory):
            pack = load_data(path)
            if str(pack.get("name")) == name:
                return pack, path
    raise ValueError(f"policy pack not found: {name}")


def _policy_directories(root: Path, config: dict[str, Any]) -> list[Path]:
    policy_packs = config.get("policy_packs", {})
    directories = policy_packs.get("directories", ["harness/policies"]) if isinstance(policy_packs, dict) else ["harness/policies"]
    if not isinstance(directories, list):
        directories = ["harness/policies"]
    result: list[Path] = []
    for item in directories:
        path = Path(str(item))
        result.append(path if path.is_absolute() else root / path)
    return result


def _policy_paths(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted([*directory.glob("*.json"), *directory.glob("*.yml"), *directory.glob("*.yaml")])


def _policy_errors(pack: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if pack.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if not str(pack.get("name", "")).strip():
        errors.append("name must be non-empty")
    if not str(pack.get("version", "")).strip():
        errors.append("version must be non-empty")
    config = pack.get("config")
    if not isinstance(config, dict):
        errors.append("config must be a mapping")
    return errors


def _summary(root: Path, path: Path, pack: dict[str, Any]) -> dict[str, Any]:
    config = pack.get("config", {}) if isinstance(pack.get("config"), dict) else {}
    return {
        "schema_version": 1,
        "name": str(pack["name"]),
        "version": str(pack["version"]),
        "description": str(pack.get("description", "")),
        "path": _relative(root, path),
        "sections": sorted(str(key) for key in config),
    }


def _merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_dicts(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _relative(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)
