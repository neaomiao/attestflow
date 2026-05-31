from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path
import re
import subprocess
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
    "docs/contracts/ci-provider-schema.md",
    "docs/contracts/planner-output-schema.md",
    "docs/contracts/pr-provider-schema.md",
    "docs/contracts/release-provider-schema.md",
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
    max_index_files = _positive_int(context_config, "max_index_files", max_tree_entries)
    includes = _string_list(context_config.get("documents")) or DEFAULT_DOCUMENTS
    focus = _dedupe(_string_list(focus_files) + _focus_from_config(context_config))
    index_files = _collect_index_files(root, max_index_files)

    return {
        "enabled": True,
        "tree": _collect_tree(root, max_tree_entries),
        "documents": _collect_named_files(root, includes, max_file_bytes),
        "files": _collect_named_files(root, focus, max_file_bytes),
        "symbols": _collect_symbols(root, index_files),
        "dependencies": _collect_dependencies(root, index_files),
        "semantic_index": _collect_semantic_index(root, index_files, max_file_bytes),
        "change_history": _collect_change_history(root),
        "test_map": _collect_test_map(index_files),
        "dynamic_context": _dynamic_context_protocol(),
        "limits": {
            "max_tree_entries": max_tree_entries,
            "max_file_bytes": max_file_bytes,
            "max_index_files": max_index_files,
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


def _collect_index_files(root: Path, limit: int) -> list[str]:
    files: list[str] = []
    for path in _iter_repository_files(root):
        rel = _relative_path(root, path)
        if not rel or not _indexable(rel):
            continue
        files.append(rel)
        if len(files) >= limit:
            break
    return files


def _collect_symbols(root: Path, files: list[str]) -> list[dict[str, Any]]:
    symbols: list[dict[str, Any]] = []
    for rel in files:
        if not rel.endswith(".py"):
            continue
        tree = _python_ast(root / rel)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                symbols.append({"path": rel, "name": node.name, "kind": "class", "line": node.lineno})
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols.append({"path": rel, "name": node.name, "kind": "function", "line": node.lineno})
    return sorted(symbols, key=lambda item: (str(item["path"]), int(item["line"]), str(item["name"])))


def _collect_dependencies(root: Path, files: list[str]) -> list[dict[str, Any]]:
    dependencies: list[dict[str, Any]] = []
    for rel in files:
        if not rel.endswith(".py"):
            continue
        tree = _python_ast(root / rel)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    dependencies.append({"source": rel, "target": alias.name, "kind": "python_import", "line": node.lineno})
            elif isinstance(node, ast.ImportFrom):
                module = "." * int(node.level or 0) + str(node.module or "")
                for alias in node.names:
                    target = f"{module}.{alias.name}" if module else alias.name
                    dependencies.append({"source": rel, "target": target, "kind": "python_import", "line": node.lineno})
    return sorted(dependencies, key=lambda item: (str(item["source"]), str(item["target"])))


def _collect_semantic_index(root: Path, files: list[str], max_file_bytes: int) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for rel in files:
        content = _read_text_prefix(root / rel, max_file_bytes)
        if content is None:
            continue
        terms = sorted(set(_tokenize(content) + _tokenize(Path(rel).stem)))[:40]
        if terms:
            entries.append({"path": rel, "terms": terms})
    return entries


def _collect_change_history(root: Path) -> list[dict[str, Any]]:
    try:
        completed = subprocess.run(
            ["git", "log", "--name-only", "--pretty=format:%H%x09%cs%x09%s", "-5"],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        return []
    if completed.returncode != 0:
        return []
    history: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in completed.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split("\t", 2)
        if len(parts) == 3 and re.fullmatch(r"[0-9a-f]{7,40}", parts[0]):
            if current:
                history.append(current)
            current = {"commit": parts[0], "date": parts[1], "subject": parts[2], "files": []}
            continue
        if current is not None and not _excluded(line):
            current["files"].append(line)
    if current:
        history.append(current)
    return history


def _collect_test_map(files: list[str]) -> list[dict[str, Any]]:
    tests = [path for path in files if _is_test_path(path)]
    sources = [path for path in files if not _is_test_path(path)]
    mappings: list[dict[str, Any]] = []
    for source in sources:
        source_stem = Path(source).stem
        source_terms = set(_tokenize(source_stem))
        for test in tests:
            test_terms = set(_tokenize(Path(test).stem))
            if source_stem in Path(test).stem or bool(source_terms & test_terms):
                mappings.append({"source": source, "test": test, "strategy": "name_match"})
    return sorted(mappings, key=lambda item: (str(item["source"]), str(item["test"])))


def _dynamic_context_protocol() -> dict[str, Any]:
    return {
        "request_schema_version": 1,
        "allowed_requests": [
            "file_slice",
            "symbol_lookup",
            "dependency_neighbors",
            "semantic_search",
            "change_history",
            "test_mapping",
        ],
        "response_contract": {
            "request_id": "stable caller supplied id",
            "status": "passed|blocked",
            "items": "context fragments with path, kind, and content or references",
        },
    }


def _python_ast(path: Path) -> ast.AST | None:
    content = _read_text_prefix(path, path.stat().st_size if path.exists() else 0)
    if content is None:
        return None
    try:
        return ast.parse(content)
    except SyntaxError:
        return None


def _read_text_prefix(path: Path, max_bytes: int) -> str | None:
    data = path.read_bytes()[:max_bytes]
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _indexable(rel_path: str) -> bool:
    suffixes = {".py", ".js", ".jsx", ".ts", ".tsx", ".md", ".toml", ".yml", ".yaml", ".json"}
    return Path(rel_path).suffix.lower() in suffixes


def _is_test_path(rel_path: str) -> bool:
    path = Path(rel_path)
    return "test" in path.stem or "tests" in path.parts


def _tokenize(text: str) -> list[str]:
    return [token.lower() for token in re.findall(r"[A-Za-z][A-Za-z0-9_]{1,}", text)]


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
    runtime_roots = {
        "harness/runs",
        "harness/capability-runs",
        "harness/ci-runs",
        "harness/pr-runs",
        "harness/release-runs",
    }
    return rel_path in runtime_roots or rel_path.startswith(
        (
            "harness/runs/",
            "harness/capability-runs/",
            "harness/ci-runs/",
            "harness/pr-runs/",
            "harness/release-runs/",
        )
    )
