from __future__ import annotations

from pathlib import Path
import re


SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|secret[_-]?key|password|passwd|private[_-]?key)"
    r"\b\s*[:=]\s*['\"]?([^'\"\s#]+)"
)
PLACEHOLDERS = {
    "",
    "change-me",
    "change-me-local-only",
    "dev-only",
    "dev_only",
    "example",
    "placeholder",
    "none",
    "null",
    "local",
    "local-only",
}
ALLOWLIST = {".env.example", "docs/local-secrets.template.md"}
SKIP_DIRS = {".git", "__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache"}


def secret_scan(root: Path) -> list[str]:
    findings: list[str] = []
    for path in _iter_files(root):
        rel = path.relative_to(root).as_posix()
        if rel in ALLOWLIST:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            match = SECRET_ASSIGNMENT.search(line)
            if not match:
                continue
            value = match.group(2).strip().strip("'\"").lower()
            if value in PLACEHOLDERS or value.startswith("change-me"):
                continue
            if "{" in value or "(" in value:
                continue
            if len(value) >= 12:
                findings.append(f"{rel}:{line_no}: possible committed secret")
    return findings


def _iter_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.is_file():
            files.append(path)
    return files
