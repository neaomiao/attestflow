from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any


DEFAULT_EXCLUDES = {
    ".git",
    ".venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    ".pytest_cache",
    ".mypy_cache",
}
DEFAULT_DOCUMENTS = [
    "README.md",
    "AGENTS.md",
    "harness.yml",
    "pyproject.toml",
    "package.json",
    "docs/contracts/capability-schema.md",
    "docs/contracts/planner-output-schema.md",
    "docs/contracts/session-adapter-schema.md",
    "docs/contracts/task-schema.md",
    "docs/design/universal-harness.md",
]


def collect_repository_context(
    root: Path,
    config: dict[str, Any],
    *,
    focus_files: list[str] | None = None,
) -> dict[str, Any]:
    context_config = config.get("context", {})
    if not isinstance(context_config, dict):
        context_config = {}
    if context_config.get("enabled") is False:
        return {"enabled": False, "tree": [], "documents": [], "files": []}

    max_tree_entries = _positive_int(context_config, "max_tree_entries", 200)
    max_file_bytes = _positive_int(context_config, "max_file_bytes", 4000)
    includes = _string_list(context_config.get("documents")) or DEFAULT_DOCUMENTS
    focus = _dedupe(_string_list(focus_files) + _focus_from_config(context_config))

    return {
        "enabled": True,
        "tree": _collect_tree(root, max_tree_entries),
        "documents": _collect_named_files(root, includes, max_file_bytes),
        "files": _collect_named_files(root, focus, max_file_bytes),
        "limits": {
            "max_tree_entries": max_tree_entries,
            "max_file_bytes": max_file_bytes,
        },
    }


def _collect_tree(root: Path, limit: int) -> list[str]:
    entries: list[str] = []
    if not root.exists():
        return entries
    for path in _iter_repository_files(root):
        rel = _relative_path(root, path)
        entries.append(rel)
        if len(entries) >= limit:
            break
    return entries


def _iter_repository_files(root: Path) -> Iterator[Path]:
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            children = sorted(directory.iterdir(), key=lambda path: str(path.relative_to(root)))
        except OSError:
            continue
        directories: list[Path] = []
        for child in children:
            rel = _relative_path(root, child)
            if not rel or _excluded(rel):
                continue
            if child.is_dir():
                directories.append(child)
            elif child.is_file():
                yield child
        stack.extend(reversed(directories))


def _collect_named_files(root: Path, paths: list[str], max_bytes: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in _dedupe(paths):
        rel = item.strip().strip("/")
        if not rel or _excluded(rel):
            continue
        path = (root / rel).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError:
            continue
        if not path.is_file():
            continue
        content = _read_text_prefix(path, max_bytes)
        if content is None:
            continue
        items.append({"path": rel, "content": content, "truncated": path.stat().st_size > max_bytes})
    return items


def _read_text_prefix(path: Path, max_bytes: int) -> str | None:
    data = path.read_bytes()[:max_bytes]
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _positive_int(config: Any, key: str, default: int) -> int:
    if not isinstance(config, dict):
        return default
    value = config.get(key, default)
    return value if type(value) is int and value > 0 else default


def _focus_from_config(config: Any) -> list[str]:
    if not isinstance(config, dict):
        return []
    return _string_list(config.get("focus_files"))


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _relative_path(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return ""


def _excluded(rel_path: str) -> bool:
    parts = set(Path(rel_path).parts)
    if parts & DEFAULT_EXCLUDES:
        return True
    return rel_path in {"harness/runs", "harness/capability-runs"} or rel_path.startswith(
        ("harness/runs/", "harness/capability-runs/")
    )
