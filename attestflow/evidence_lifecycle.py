from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
from typing import Any

from .provider_failures import redact_text


def maintain_evidence(
    root: Path,
    config: dict[str, Any],
    *,
    retention_days: int | None = None,
    apply: bool = False,
    redact: bool = False,
    compact: bool = False,
    max_file_bytes: int = 131072,
) -> dict[str, Any]:
    if retention_days is not None and retention_days <= 0:
        raise ValueError("retention_days must be positive")
    if max_file_bytes <= 0:
        raise ValueError("max_file_bytes must be positive")

    actions: list[dict[str, Any]] = []
    gc_dirs: set[Path] = set()
    if retention_days is not None:
        threshold = datetime.now(timezone.utc) - timedelta(days=retention_days)
        for run_dir in _run_directories(root, config):
            if _mtime(run_dir) < threshold:
                action = _action("gc_run", root, run_dir, apply=apply)
                actions.append(action)
                gc_dirs.add(run_dir.resolve())
                if apply:
                    shutil.rmtree(run_dir)

    for path in _evidence_files(root, config):
        if any(_is_relative_to(path.resolve(), directory) for directory in gc_dirs):
            continue
        if redact:
            redaction = _redaction_action(root, path, apply=apply)
            if redaction:
                actions.append(redaction)
        if compact and path.exists() and path.stat().st_size > max_file_bytes:
            actions.append(_compact_action(root, path, max_file_bytes=max_file_bytes, apply=apply))

    return {
        "schema_version": 1,
        "applied": apply,
        "retention_days": retention_days,
        "redact": redact,
        "compact": compact,
        "max_file_bytes": max_file_bytes,
        "actions": actions,
    }


def _run_directories(root: Path, config: dict[str, Any]) -> list[Path]:
    directories: list[Path] = []
    for base in _evidence_roots(root, config):
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            if child.is_dir() and not child.name.startswith("."):
                directories.append(child)
    return directories


def _evidence_files(root: Path, config: dict[str, Any]) -> list[Path]:
    files: list[Path] = []
    for base in _evidence_roots(root, config):
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file() and _is_text_evidence(path):
                files.append(path)
    return files


def _evidence_roots(root: Path, config: dict[str, Any]) -> list[Path]:
    defaults = {
        "runs": "harness/runs",
        "capability_runs": "harness/capability-runs",
        "autopilot_runs": "harness/autopilot-runs",
        "ci_runs": "harness/ci-runs",
        "git_runs": "harness/git-runs",
        "pr_runs": "harness/pr-runs",
        "release_runs": "harness/release-runs",
        "plugin_runs": "harness/plugin-runs",
    }
    paths = config.get("paths", {}) if isinstance(config.get("paths"), dict) else {}
    roots: list[Path] = []
    for key, default in defaults.items():
        path = Path(str(paths.get(key, default)))
        roots.append(path if path.is_absolute() else root / path)
    return roots


def _redaction_action(root: Path, path: Path, *, apply: bool) -> dict[str, Any] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None
    redacted = redact_text(text)
    if redacted == text:
        return None
    action = _action("redact_file", root, path, apply=apply)
    if apply:
        path.write_text(redacted, encoding="utf-8")
    return action


def _compact_action(root: Path, path: Path, *, max_file_bytes: int, apply: bool) -> dict[str, Any]:
    action = _action("compact_file", root, path, apply=apply)
    action["original_size"] = path.stat().st_size
    action["max_file_bytes"] = max_file_bytes
    if apply:
        data = path.read_bytes()
        keep = max(1, max_file_bytes // 2 - 24)
        marker = b"\n<attestflow evidence compacted>\n"
        compacted = data[:keep] + marker + data[-keep:]
        path.write_bytes(compacted)
        action["compacted_size"] = path.stat().st_size
    return action


def _action(kind: str, root: Path, path: Path, *, apply: bool) -> dict[str, Any]:
    return {
        "type": kind,
        "status": "applied" if apply else "planned",
        "path": _relative(root, path),
    }


def _mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def _is_text_evidence(path: Path) -> bool:
    return path.suffix.lower() in {".json", ".yml", ".yaml", ".md", ".log", ".txt", ".jsonl"}


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _relative(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)
