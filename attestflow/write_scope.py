from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
import hashlib
from typing import Any


DEFAULT_SNAPSHOT_EXCLUDES = {
    ".git",
    ".venv",
    "__pycache__",
    "node_modules",
    ".pytest_cache",
    ".mypy_cache",
    "dist",
    "build",
}


def capture_write_scope_snapshot(root: Path, config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    resolved = root.resolve()
    snapshot: dict[str, dict[str, Any]] = {}
    for path in _iter_snapshot_files(resolved, config):
        rel = _relative_path(resolved, path)
        if not rel:
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        snapshot[rel] = {
            "path": rel,
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
            "binary": _is_binary(data),
        }
    return snapshot


def build_write_scope_report(
    root: Path,
    config: dict[str, Any],
    task: dict[str, Any],
    before: dict[str, dict[str, Any]],
    after: dict[str, dict[str, Any]],
    *,
    action: str,
) -> dict[str, Any]:
    changes = _snapshot_changes(before, after)
    write_scope = _task_write_scope(task)
    violations = [
        {**change, "reason": "outside files.write"}
        for change in changes
        if not _path_matches_any(str(change["path"]), write_scope)
    ]
    return {
        "schema_version": 1,
        "action": action,
        "status": "failed" if violations else "passed",
        "root": str(root),
        "write_scope": write_scope,
        "changes": changes,
        "violations": violations,
    }


def write_scope_failure_message(report: dict[str, Any]) -> str | None:
    violations = report.get("violations", [])
    if not violations:
        return None
    details = []
    for item in violations:
        path = str(item.get("path", ""))
        change_type = str(item.get("change_type", "changed"))
        previous = item.get("previous_path")
        if previous:
            details.append(f"{change_type} {previous} -> {path}")
        else:
            details.append(f"{change_type} {path}")
    return "write_scope violation: " + ", ".join(details)


def _snapshot_changes(
    before: dict[str, dict[str, Any]],
    after: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    before_paths = set(before)
    after_paths = set(after)
    added = set(after_paths - before_paths)
    deleted = set(before_paths - after_paths)
    changes: list[dict[str, Any]] = []

    renamed_pairs = _detect_renames(before, after, deleted, added)
    for old_path, new_path in renamed_pairs:
        deleted.discard(old_path)
        added.discard(new_path)
        old_item = before[old_path]
        new_item = after[new_path]
        binary = bool(old_item.get("binary") or new_item.get("binary"))
        changes.append(
            {
                "path": old_path,
                "change_type": "renamed_from",
                "previous_path": None,
                "new_path": new_path,
                "binary": binary,
            }
        )
        changes.append(
            {
                "path": new_path,
                "change_type": "renamed_to",
                "previous_path": old_path,
                "new_path": None,
                "binary": binary,
            }
        )

    for path in sorted(added):
        item = after[path]
        changes.append(
            {
                "path": path,
                "change_type": "added",
                "previous_path": None,
                "new_path": None,
                "binary": bool(item.get("binary")),
            }
        )
    for path in sorted(deleted):
        item = before[path]
        changes.append(
            {
                "path": path,
                "change_type": "deleted",
                "previous_path": None,
                "new_path": None,
                "binary": bool(item.get("binary")),
            }
        )
    for path in sorted(before_paths & after_paths):
        if before[path].get("sha256") == after[path].get("sha256"):
            continue
        changes.append(
            {
                "path": path,
                "change_type": "modified",
                "previous_path": None,
                "new_path": None,
                "binary": bool(before[path].get("binary") or after[path].get("binary")),
            }
        )
    return sorted(changes, key=lambda item: (str(item["path"]), str(item["change_type"])))


def _detect_renames(
    before: dict[str, dict[str, Any]],
    after: dict[str, dict[str, Any]],
    deleted: set[str],
    added: set[str],
) -> list[tuple[str, str]]:
    added_by_fingerprint: dict[tuple[Any, Any], list[str]] = {}
    for path in added:
        fingerprint = (after[path].get("sha256"), after[path].get("size"))
        added_by_fingerprint.setdefault(fingerprint, []).append(path)
    pairs: list[tuple[str, str]] = []
    used_added: set[str] = set()
    for old_path in sorted(deleted):
        fingerprint = (before[old_path].get("sha256"), before[old_path].get("size"))
        candidates = [path for path in sorted(added_by_fingerprint.get(fingerprint, [])) if path not in used_added]
        if not candidates:
            continue
        new_path = candidates[0]
        used_added.add(new_path)
        pairs.append((old_path, new_path))
    return pairs


def _iter_snapshot_files(root: Path, config: dict[str, Any]) -> Iterator[Path]:
    stack = [root]
    runtime_roots = set(_runtime_snapshot_roots(config))
    while stack:
        directory = stack.pop()
        try:
            children = sorted(directory.iterdir(), key=lambda path: str(path))
        except OSError:
            continue
        directories: list[Path] = []
        for child in children:
            rel = _relative_path(root, child)
            if not rel or _excluded(rel, runtime_roots):
                continue
            if child.is_dir():
                directories.append(child)
            elif child.is_file():
                yield child
        stack.extend(reversed(directories))


def _runtime_snapshot_roots(config: dict[str, Any]) -> list[str]:
    paths = config.get("paths", {}) if isinstance(config.get("paths"), dict) else {}
    keys = {
        "runs": "harness/runs",
        "locks": "harness/locks",
        "capability_runs": "harness/capability-runs",
        "autopilot_runs": "harness/autopilot-runs",
        "ci_runs": "harness/ci-runs",
        "pr_runs": "harness/pr-runs",
        "release_runs": "harness/release-runs",
    }
    roots = []
    for key, default in keys.items():
        normalized = _normalize_repo_path(str(paths.get(key, default)))
        if normalized:
            roots.append(normalized)
    return roots


def _excluded(rel_path: str, runtime_roots: set[str]) -> bool:
    parts = set(Path(rel_path).parts)
    if parts & DEFAULT_SNAPSHOT_EXCLUDES:
        return True
    return _path_matches_any(rel_path, list(runtime_roots))


def _is_binary(data: bytes) -> bool:
    if b"\x00" in data:
        return True
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def _task_write_scope(task: dict[str, Any]) -> list[str]:
    files = task.get("files", {}) if isinstance(task.get("files"), dict) else {}
    write = files.get("write", []) if isinstance(files, dict) else []
    if not isinstance(write, list):
        return []
    return [path for path in (_normalize_repo_path(str(item)) for item in write) if path]


def _normalize_repo_path(value: str) -> str | None:
    path = value.strip().replace("\\", "/")
    if not path:
        return None
    while path.startswith("./"):
        path = path[2:]
    if path.startswith("/") or path.startswith("../") or "/../" in path or path == "..":
        return None
    return path.rstrip("/")


def _path_matches_any(path: str, scopes: list[str]) -> bool:
    normalized = _normalize_repo_path(path)
    if not normalized:
        return False
    for scope in scopes:
        normalized_scope = _normalize_repo_path(scope)
        if not normalized_scope:
            continue
        if normalized == normalized_scope or normalized.startswith(normalized_scope.rstrip("/") + "/"):
            return True
    return False


def _relative_path(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return ""
