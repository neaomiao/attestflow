from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any

from .io import dump_data, load_data


TOKEN_CHARS = 4
TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cached_input_tokens",
    "reasoning_tokens",
)


def estimate_tokens(value: Any) -> int:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return max(1, (len(text) + TOKEN_CHARS - 1) // TOKEN_CHARS) if text else 0


def enforce_payload_budget(root: Path, config: dict[str, Any], scope: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not _token_economy_enabled(config):
        return payload
    budget = _input_budget(config, scope)
    if budget is None:
        return payload
    optimized = deepcopy(payload)
    before = estimate_tokens(optimized)
    if before <= budget:
        optimized["token_economy"] = {
            "scope": scope,
            "estimated_input_tokens": before,
            "estimated_input_tokens_before": before,
            "input_budget_tokens": budget,
            "budget_exceeded": False,
            "strategy": "full_context",
        }
        return optimized

    _summarize_repository_context(root, config, optimized)
    after = estimate_tokens(optimized)
    optimized["token_economy"] = {
        "scope": scope,
        "estimated_input_tokens": after,
        "estimated_input_tokens_before": before,
        "input_budget_tokens": budget,
        "budget_exceeded": True,
        "strategy": "context_cache_summary",
        "estimated_tokens_saved": max(0, before - after),
    }
    return optimized


def build_incremental_context(root: Path, config: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    if not _section_enabled(config, "incremental_context", default=False):
        return {"enabled": False}
    files = task.get("files", {}) if isinstance(task.get("files"), dict) else {}
    focus_files = _dedupe(
        [
            str(item)
            for key in ("read", "write")
            for item in (files.get(key, []) if isinstance(files.get(key), list) else [])
        ]
    )
    evidence = task.get("evidence", {}) if isinstance(task.get("evidence"), dict) else {}
    capabilities = evidence.get("capabilities", {}) if isinstance(evidence.get("capabilities"), dict) else {}
    previous = []
    summary_config = _forced_evidence_summary_config(config)
    for name, path in sorted(capabilities.items()):
        if not path:
            continue
        summary = summarize_evidence_reference(root, str(path), summary_config)
        output_summary = summary.get("output_summary", {}) if isinstance(summary.get("output_summary"), dict) else {}
        previous.append(
            {
                "capability": str(name),
                "path": str(path),
                "exists": bool(summary.get("exists")),
                "status": output_summary.get("status"),
                "summary": output_summary.get("summary"),
                "findings_count": output_summary.get("findings_count", 0),
                "artifacts_count": output_summary.get("artifacts_count", 0),
            }
        )
    return {
        "enabled": True,
        "task_id": task.get("id"),
        "focus_files": focus_files,
        "previous_capabilities": previous,
    }


def summarize_evidence_reference(root: Path, value: Any, config: dict[str, Any]) -> Any:
    if not isinstance(value, str):
        return value
    path = Path(value)
    absolute_path = path if path.is_absolute() else root / path
    item: dict[str, Any] = {"path": value, "exists": absolute_path.exists()}
    if not absolute_path.exists():
        return item
    if absolute_path.suffix not in {".json", ".yml", ".yaml"}:
        if _section_enabled(config, "evidence_summary", default=False):
            item["text_summary"] = _read_text_excerpt(absolute_path, _evidence_summary_limit(config))
        return item
    try:
        output = load_data(absolute_path)
    except (OSError, ValueError) as exc:
        raise ValueError(f"failed to load evidence {absolute_path}: {exc}") from exc
    if _section_enabled(config, "evidence_summary", default=False):
        item["output_summary"] = _summarize_mapping(output, _evidence_summary_limit(config))
    else:
        item["output"] = output
    return item


def provider_cache_enabled(config: dict[str, Any]) -> bool:
    return _section_enabled(config, "provider_cache", default=False)


def provider_cache_key(capability_name: str, command: str, payload: dict[str, Any]) -> str:
    stable = {
        "capability": capability_name,
        "command": command,
        "payload": _normalized_provider_cache_payload(payload),
    }
    encoded = json.dumps(stable, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_provider_cache(root: Path, config: dict[str, Any], capability_name: str, command: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    if not provider_cache_enabled(config):
        return None
    key = provider_cache_key(capability_name, command, payload)
    path = _provider_cache_root(root, config) / f"{key}.json"
    if not path.exists():
        return None
    record = load_data(path)
    output = record.get("output")
    return output if isinstance(output, dict) else None


def store_provider_cache(root: Path, config: dict[str, Any], capability_name: str, command: str, payload: dict[str, Any], output: dict[str, Any]) -> dict[str, Any] | None:
    if not provider_cache_enabled(config) or not _cacheable_output(output):
        return None
    key = provider_cache_key(capability_name, command, payload)
    path = _provider_cache_root(root, config) / f"{key}.json"
    record = {
        "schema_version": 1,
        "cache_key": key,
        "capability": capability_name,
        "output": output,
    }
    dump_data(record, path)
    return {"hit": False, "cache_key": key, "path": _display_path(root, path)}


def provider_cache_hit_metadata(root: Path, config: dict[str, Any], capability_name: str, command: str, payload: dict[str, Any]) -> dict[str, Any]:
    key = provider_cache_key(capability_name, command, payload)
    path = _provider_cache_root(root, config) / f"{key}.json"
    return {"hit": True, "cache_key": key, "path": _display_path(root, path)}


def _summarize_repository_context(root: Path, config: dict[str, Any], payload: dict[str, Any]) -> None:
    context = payload.get("repository_context")
    if not isinstance(context, dict):
        return
    cached: list[dict[str, Any]] = []
    for key in ("documents", "files"):
        items = context.get(key)
        if not isinstance(items, list):
            continue
        summarized = []
        for item in items:
            if isinstance(item, dict):
                summarized.append(_summarize_context_item(root, config, item, cached))
            else:
                summarized.append(item)
        context[key] = summarized
    context["context_cache"] = cached
    for key, limit in (("semantic_index", 20), ("symbols", 100), ("dependencies", 100), ("change_history", 5), ("test_map", 50)):
        value = context.get(key)
        if isinstance(value, list) and len(value) > limit:
            context[key] = value[:limit]
            context[f"{key}_truncated"] = True


def _summarize_context_item(root: Path, config: dict[str, Any], item: dict[str, Any], cached: list[dict[str, Any]]) -> dict[str, Any]:
    content = item.get("content")
    if not isinstance(content, str):
        return dict(item)
    encoded = content.encode("utf-8")
    content_hash = hashlib.sha256(encoded).hexdigest()
    cache_key = content_hash
    summary = _summarize_text(content, _context_summary_limit(config))
    if _section_enabled(config, "context_cache", default=True):
        dump_data(
            {
                "schema_version": 1,
                "path": item.get("path"),
                "content_hash": content_hash,
                "content_bytes": len(encoded),
                "summary": summary,
            },
            _context_cache_root(root, config) / f"{cache_key}.json",
        )
    replacement = {key: value for key, value in item.items() if key != "content"}
    replacement.update(
        {
            "summary": summary,
            "cache_key": cache_key,
            "content_hash": content_hash,
            "content_bytes": len(encoded),
            "retrieval": {"type": "file_slice", "path": item.get("path")},
        }
    )
    cached.append({"path": item.get("path"), "cache_key": cache_key, "content_bytes": len(encoded)})
    return replacement


def _token_economy_enabled(config: dict[str, Any]) -> bool:
    settings = config.get("token_economy")
    if settings is None:
        return False
    if not isinstance(settings, dict):
        return False
    return settings.get("enabled") is not False


def _input_budget(config: dict[str, Any], scope: str) -> int | None:
    settings = config.get("token_economy", {})
    budgets = settings.get("budgets", {}) if isinstance(settings, dict) else {}
    if not isinstance(budgets, dict):
        return None
    for key in (f"{scope}_input_tokens", "default_input_tokens"):
        value = budgets.get(key)
        if type(value) is int and value > 0:
            return value
    return None


def _section_enabled(config: dict[str, Any], name: str, *, default: bool) -> bool:
    settings = config.get("token_economy", {})
    if not isinstance(settings, dict) or settings.get("enabled") is False:
        return False
    section = settings.get(name, {})
    if not isinstance(section, dict):
        return default
    enabled = section.get("enabled")
    return default if enabled is None else bool(enabled)


def _context_cache_root(root: Path, config: dict[str, Any]) -> Path:
    return root / _section_path(config, "context_cache", "harness/context-cache")


def _provider_cache_root(root: Path, config: dict[str, Any]) -> Path:
    return root / _section_path(config, "provider_cache", "harness/provider-cache")


def _display_path(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _section_path(config: dict[str, Any], name: str, default: str) -> str:
    settings = config.get("token_economy", {})
    section = settings.get(name, {}) if isinstance(settings, dict) else {}
    value = section.get("path") if isinstance(section, dict) else None
    return str(value) if isinstance(value, str) and value else default


def _context_summary_limit(config: dict[str, Any]) -> int:
    settings = config.get("token_economy", {})
    section = settings.get("context_cache", {}) if isinstance(settings, dict) else {}
    value = section.get("max_summary_bytes") if isinstance(section, dict) else None
    return value if type(value) is int and value > 0 else 800


def _evidence_summary_limit(config: dict[str, Any]) -> int:
    settings = config.get("token_economy", {})
    section = settings.get("evidence_summary", {}) if isinstance(settings, dict) else {}
    value = section.get("max_output_bytes") if isinstance(section, dict) else None
    return value if type(value) is int and value > 0 else 2000


def _summarize_text(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    data = encoded[:max_bytes]
    return data.decode("utf-8", errors="ignore").rstrip() + "\n[truncated]"


def _read_text_excerpt(path: Path, max_bytes: int) -> str:
    data = path.read_bytes()[:max_bytes]
    return data.decode("utf-8", errors="replace")


def _summarize_mapping(data: dict[str, Any], max_bytes: int) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key in ("schema_version", "status", "summary", "provider", "external_id", "url", "conclusion", "branch", "head_sha"):
        value = data.get(key)
        if value is not None:
            summary[key] = _summarize_scalar(value, max_bytes)
    for key in ("checks", "findings", "artifacts", "tasks", "command_results"):
        value = data.get(key)
        if isinstance(value, list):
            summary[f"{key}_count"] = len(value)
    usage = data.get("usage")
    if isinstance(usage, dict):
        summary["usage"] = {key: usage[key] for key in TOKEN_FIELDS if key in usage}
        if "cost_usd" in usage:
            summary["usage"]["cost_usd"] = usage["cost_usd"]
    return summary


def _summarize_scalar(value: Any, max_bytes: int) -> Any:
    if isinstance(value, str):
        return _summarize_text(value, max_bytes)
    return value


def _forced_evidence_summary_config(config: dict[str, Any]) -> dict[str, Any]:
    settings = deepcopy(config.get("token_economy", {})) if isinstance(config.get("token_economy"), dict) else {}
    evidence = settings.get("evidence_summary", {}) if isinstance(settings.get("evidence_summary"), dict) else {}
    evidence = dict(evidence)
    evidence["enabled"] = True
    settings["evidence_summary"] = evidence
    return {"token_economy": settings}


def _cacheable_output(output: dict[str, Any]) -> bool:
    status = output.get("status")
    if status in {"failed", "blocked"}:
        return False
    return True


def _normalized_provider_cache_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(payload)
    task = normalized.get("task")
    if isinstance(task, dict):
        evidence = task.get("evidence")
        if isinstance(evidence, dict):
            evidence = dict(evidence)
            evidence.pop("capabilities", None)
            task["evidence"] = evidence
        for key in ("created_at", "updated_at"):
            task.pop(key, None)
    return normalized


def _dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
