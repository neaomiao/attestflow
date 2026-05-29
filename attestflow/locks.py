from __future__ import annotations

from pathlib import Path
from typing import Any


def locks_root(root: Path, config: dict[str, Any]) -> Path:
    return root / config.get("paths", {}).get("locks", "harness/locks")


def task_lock_path(root: Path, config: dict[str, Any], task_id: str) -> Path:
    return locks_root(root, config) / "tasks" / f"{task_id}.lock"


def file_lock_path(root: Path, config: dict[str, Any], file_path: str) -> Path:
    safe = file_path.replace("/", ".").replace("\\", ".")
    return locks_root(root, config) / "files" / f"{safe}.lock"


def acquire_task_lock(root: Path, config: dict[str, Any], task_id: str, run_id: str) -> Path:
    path = task_lock_path(root, config, task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise RuntimeError(f"task is already locked: {task_id}")
    path.write_text(run_id + "\n", encoding="utf-8")
    return path


def acquire_file_locks(
    root: Path, config: dict[str, Any], files: list[str], task_id: str
) -> list[Path]:
    acquired: list[Path] = []
    for file_name in files:
        path = file_lock_path(root, config, file_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise RuntimeError(f"file is already locked: {file_name}")
        path.write_text(task_id + "\n", encoding="utf-8")
        acquired.append(path)
    return acquired


def write_scope_locked(root: Path, config: dict[str, Any], files: list[str]) -> bool:
    return any(file_lock_path(root, config, file_name).exists() for file_name in files)


def release_locks_for_task(root: Path, config: dict[str, Any], task_id: str) -> None:
    task_path = task_lock_path(root, config, task_id)
    if task_path.exists():
        task_path.unlink()

    files_dir = locks_root(root, config) / "files"
    if not files_dir.exists():
        return
    for path in files_dir.glob("*.lock"):
        if path.read_text(encoding="utf-8").strip() == task_id:
            path.unlink()
