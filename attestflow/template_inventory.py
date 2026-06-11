from __future__ import annotations

from pathlib import Path


def relative_template_files(root: Path) -> set[Path]:
    if not root.exists():
        return set()
    return {
        path.relative_to(root)
        for path in root.rglob("*")
        if path.is_file() and not is_local_analysis_artifact(path.relative_to(root))
    }


def is_local_analysis_artifact(relative: Path) -> bool:
    parts = relative.parts
    return (
        "graphify-out" in parts
        or any(part.startswith(".graphify") for part in parts)
        or "__pycache__" in parts
        or any(part.endswith(".pyc") for part in parts)
    )
