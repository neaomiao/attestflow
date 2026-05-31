from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Any

from .io import load_data


@dataclass(frozen=True)
class TaskWorktree:
    path: Path
    commit_before: str
    branch: str | None


@dataclass(frozen=True)
class AppliedTaskWorktree:
    path: Path
    commit_before: str
    commit_after: str
    applied_to_control: bool


def provision_task_worktree(
    root: Path,
    config: dict[str, Any],
    task: dict[str, Any],
    run_id: str,
) -> TaskWorktree | None:
    worktree_config = _worktree_config(config)
    if worktree_config.get("enabled") is not True:
        return None

    task_id = str(task["id"])
    commit_before = _git_output(root, "rev-parse", "HEAD")
    target = _worktree_path(root, config, worktree_config, task_id, run_id)
    if target.exists():
        raise ValueError(f"task worktree already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    _git_run(root, "worktree", "add", "--detach", str(target), commit_before)
    branch = _git_output(target, "rev-parse", "--abbrev-ref", "HEAD")
    return TaskWorktree(
        path=target,
        commit_before=commit_before,
        branch=None if branch == "HEAD" else branch,
    )


def apply_task_worktree(root: Path, run_path: Path, task_id: str) -> AppliedTaskWorktree | None:
    metadata_path = run_path / "metadata.yml"
    if not metadata_path.exists():
        return None
    metadata = load_data(metadata_path)
    workspace = metadata.get("workspace", {})
    if not isinstance(workspace, dict) or not workspace.get("worktree"):
        return None
    if workspace.get("worktree_finalized") is True:
        return None
    worktree = Path(str(workspace["worktree"]))
    commit_before = str(workspace.get("commit_before") or _git_output(worktree, "rev-parse", "HEAD"))

    _git_run(worktree, "add", "-A")
    if _git_output(worktree, "status", "--porcelain"):
        _git_run(
            worktree,
            "-c",
            "user.name=Attestflow",
            "-c",
            "user.email=attestflow@example.invalid",
            "commit",
            "-m",
            f"attestflow {task_id}",
        )
    commit_after = _git_output(worktree, "rev-parse", "HEAD")
    applied = False
    if commit_after != commit_before:
        _git_run(root, "merge", "--ff-only", commit_after)
        applied = True
    return AppliedTaskWorktree(
        path=worktree,
        commit_before=commit_before,
        commit_after=commit_after,
        applied_to_control=applied,
    )


def _worktree_config(config: dict[str, Any]) -> dict[str, Any]:
    sessions = config.get("sessions", {})
    worktree = sessions.get("worktree", {}) if isinstance(sessions, dict) else {}
    return worktree if isinstance(worktree, dict) else {}


def _worktree_path(
    root: Path,
    config: dict[str, Any],
    worktree_config: dict[str, Any],
    task_id: str,
    run_id: str,
) -> Path:
    project = config.get("project", {}) if isinstance(config.get("project"), dict) else {}
    project_name = str(project.get("name") or root.name)
    template = worktree_config.get("path_template") or "../.attestflow-worktrees/{project}/{task_id}-{run_id}"
    rendered = str(template).format(
        project=_safe_path_part(project_name),
        task_id=_safe_path_part(task_id),
        run_id=_safe_path_part(run_id),
    )
    path = Path(rendered)
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def _safe_path_part(value: str) -> str:
    safe = [ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in value]
    return "".join(safe).strip("-") or "item"


def _git_output(cwd: Path, *args: str) -> str:
    completed = _git_run(cwd, *args)
    return completed.stdout.strip()


def _git_run(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise ValueError(f"git command could not run: {exc}") from exc
    if completed.returncode != 0:
        output = " ".join(part for part in [completed.stdout.strip(), completed.stderr.strip()] if part)
        suffix = f": {output}" if output else ""
        raise ValueError(f"git {' '.join(args)} failed with exit code {completed.returncode}{suffix}")
    return completed
