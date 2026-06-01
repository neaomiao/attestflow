from __future__ import annotations

from pathlib import Path
from typing import Any

from .io import load_data
from .token_economy import TOKEN_FIELDS


DEFAULT_RUNTIME_PATHS = {
    "capability_runs": "harness/capability-runs",
    "runs": "harness/runs",
    "ci_runs": "harness/ci-runs",
    "git_runs": "harness/git-runs",
    "pr_runs": "harness/pr-runs",
    "release_runs": "harness/release-runs",
}


def build_usage_report(root: Path, config: dict[str, Any]) -> dict[str, Any]:
    records = []
    for path in _usage_files(root, config):
        try:
            usage = load_data(path)
        except (OSError, ValueError):
            continue
        record = _usage_record(root, path, usage)
        if record:
            records.append(record)
    totals = _empty_totals()
    by_provider_model: dict[str, dict[str, Any]] = {}
    for record in records:
        _add_usage(totals, record["usage"])
        key = f"{record['provider']}/{record['model']}"
        if key not in by_provider_model:
            by_provider_model[key] = _empty_totals()
        _add_usage(by_provider_model[key], record["usage"])
    return {
        "schema_version": 1,
        "records": records,
        "totals": totals,
        "by_provider_model": by_provider_model,
    }


def _usage_files(root: Path, config: dict[str, Any]) -> list[Path]:
    paths = config.get("paths", {}) if isinstance(config.get("paths"), dict) else {}
    seen: set[Path] = set()
    files: list[Path] = []
    for key, default in DEFAULT_RUNTIME_PATHS.items():
        run_root = root / str(paths.get(key, default))
        if not run_root.exists():
            continue
        for pattern in ("**/usage.json", "**/session-*-usage.json"):
            for path in sorted(run_root.glob(pattern)):
                resolved = path.resolve()
                if resolved in seen or not path.is_file():
                    continue
                seen.add(resolved)
                files.append(path)
    return files


def _usage_record(root: Path, path: Path, usage: dict[str, Any]) -> dict[str, Any] | None:
    if not any(field in usage for field in TOKEN_FIELDS) and "cost_usd" not in usage:
        return None
    try:
        rel = str(path.relative_to(root))
    except ValueError:
        rel = str(path)
    provider = str(usage.get("provider") or "unknown")
    model = str(usage.get("model") or "unknown")
    return {"path": rel, "provider": provider, "model": model, "usage": usage}


def _empty_totals() -> dict[str, Any]:
    totals = {field: 0 for field in TOKEN_FIELDS}
    totals["cost_usd"] = 0.0
    return totals


def _add_usage(totals: dict[str, Any], usage: dict[str, Any]) -> None:
    for field in TOKEN_FIELDS:
        value = usage.get(field)
        if isinstance(value, int) and value >= 0:
            totals[field] += value
    cost = usage.get("cost_usd")
    if type(cost) in {int, float} and cost >= 0:
        totals["cost_usd"] += float(cost)
