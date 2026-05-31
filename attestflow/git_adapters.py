from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"invalid Git adapter input JSON: {exc}\n")
        return 1
    provider = str(payload.get("provider", ""))
    if provider != "git":
        sys.stderr.write(f"unsupported built-in Git provider: {provider}\n")
        return 1
    print(json.dumps(run_git_provider(payload), ensure_ascii=False))
    return 0


def run_git_provider(payload: dict[str, Any]) -> dict[str, Any]:
    options = _options(payload)
    root = Path(str(payload.get("root") or "."))
    if not _command_exists("git"):
        return _blocked("git command not found")
    if _git(root, "rev-parse", "--show-toplevel").returncode != 0:
        return _blocked("project root is not a git repository")

    branch = str(options.get("branch") or _git_output(root, "rev-parse", "--abbrev-ref", "HEAD")).strip()
    if not branch or branch == "HEAD":
        return _blocked("cannot publish from detached HEAD")
    default_branch = str(options.get("default_branch") or _project_default_branch(payload) or "main")
    if branch == default_branch and options.get("allow_default_branch") is not True:
        return _blocked(f"refusing to publish directly from default branch {default_branch}", branch=branch)

    remote = str(options.get("remote") or "origin")
    commit_before = _git_output(root, "rev-parse", "HEAD")
    status_before = _git_output(root, "status", "--porcelain=v1")
    changes = _status_paths(status_before)
    stage_error = _stage(root, payload, options)
    if stage_error:
        return _failed(stage_error, branch=branch, remote=remote, commit_before=commit_before, changes=changes)

    commit_made = False
    if _git(root, "diff", "--cached", "--quiet").returncode != 0:
        completed = _git(root, "commit", "-m", _commit_message(payload, options))
        if completed.returncode != 0:
            return _failed(
                "git commit failed" + _output_suffix(completed.stdout, completed.stderr),
                branch=branch,
                remote=remote,
                commit_before=commit_before,
                changes=changes,
            )
        commit_made = True

    commit_after = _git_output(root, "rev-parse", "HEAD")
    pushed = False
    if options.get("push", True) is not False:
        if not _remote_exists(root, remote):
            return _blocked(
                f"git remote not found: {remote}",
                branch=branch,
                remote=remote,
                commit_before=commit_before,
                commit_after=commit_after,
                changes=changes,
            )
        completed = _git(root, "push", "-u", remote, branch)
        if completed.returncode != 0:
            return _blocked(
                "git push failed" + _output_suffix(completed.stdout, completed.stderr),
                branch=branch,
                remote=remote,
                commit_before=commit_before,
                commit_after=commit_after,
                changes=changes,
            )
        pushed = True

    status = "published" if commit_made or pushed else "skipped"
    summary = _summary(status, commit_made=commit_made, pushed=pushed, branch=branch)
    return {
        "schema_version": 1,
        "provider": "git",
        "status": status,
        "summary": summary,
        "branch": branch,
        "remote": remote,
        "commit_before": commit_before,
        "commit_after": commit_after,
        "pushed": pushed,
        "changes": changes,
    }


def _stage(root: Path, payload: dict[str, Any], options: dict[str, Any]) -> str | None:
    stage = str(options.get("stage") or "").strip().lower()
    if stage == "none":
        return None
    paths = _stage_paths(payload, options)
    args = ["add", "-A"]
    if paths is not None:
        if not paths:
            return None
        args.extend(["--", *paths])
    completed = _git(root, *args)
    if completed.returncode == 0:
        return None
    return "git add failed" + _output_suffix(completed.stdout, completed.stderr)


def _stage_paths(payload: dict[str, Any], options: dict[str, Any]) -> list[str] | None:
    configured = options.get("stage_paths")
    if isinstance(configured, list):
        return [str(item) for item in configured if str(item)]
    if str(options.get("stage") or "").strip().lower() == "all":
        return None
    task = payload.get("task")
    files = task.get("files", {}) if isinstance(task, dict) else {}
    write_files = files.get("write") if isinstance(files, dict) else None
    if isinstance(write_files, list) and payload.get("task_id"):
        return [str(item) for item in write_files if str(item)]
    return None


def _commit_message(payload: dict[str, Any], options: dict[str, Any]) -> str:
    configured = str(options.get("commit_message") or "").strip()
    if configured:
        return configured
    task = payload.get("task")
    if isinstance(task, dict):
        task_id = str(task.get("id") or payload.get("task_id") or "").strip()
        title = str(task.get("title") or "").strip()
        if task_id and title:
            return f"{task_id}: {title}"
        if task_id:
            return f"{task_id}: publish changes"
    return "Attestflow publish changes"


def _project_default_branch(payload: dict[str, Any]) -> str | None:
    project = payload.get("project", {})
    if not isinstance(project, dict):
        return None
    value = str(project.get("default_branch") or "").strip()
    return value or None


def _remote_exists(root: Path, remote: str) -> bool:
    completed = _git(root, "remote")
    if completed.returncode != 0:
        return False
    return remote in {line.strip() for line in completed.stdout.splitlines()}


def _status_paths(status: str) -> list[str]:
    paths: list[str] = []
    for line in status.splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path:
            paths.append(path)
    return sorted(paths)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        return subprocess.CompletedProcess(["git", *args], 1, "", str(exc))


def _git_output(root: Path, *args: str) -> str:
    completed = _git(root, *args)
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _options(payload: dict[str, Any]) -> dict[str, Any]:
    options = payload.get("provider_options", {})
    return options if isinstance(options, dict) else {}


def _summary(status: str, *, commit_made: bool, pushed: bool, branch: str) -> str:
    if status == "skipped":
        return f"git publish skipped on {branch}: no staged changes and push disabled"
    actions = []
    if commit_made:
        actions.append("committed")
    if pushed:
        actions.append("pushed")
    return f"git publish {' and '.join(actions) or status} on {branch}"


def _command_exists(command: str) -> bool:
    return bool(shutil.which(command) or Path(command).exists())


def _blocked(summary: str, **extra: Any) -> dict[str, Any]:
    payload = {
        "schema_version": 1,
        "provider": "git",
        "status": "blocked",
        "summary": summary,
        "changes": [],
        "pushed": False,
    }
    payload.update({key: value for key, value in extra.items() if value is not None})
    return payload


def _failed(summary: str, **extra: Any) -> dict[str, Any]:
    payload = {
        "schema_version": 1,
        "provider": "git",
        "status": "failed",
        "summary": summary,
        "changes": [],
        "pushed": False,
    }
    payload.update({key: value for key, value in extra.items() if value is not None})
    return payload


def _output_suffix(stdout: object, stderr: object) -> str:
    text = " ".join(_text(item).strip() for item in (stdout, stderr) if _text(item).strip())
    if not text:
        return ""
    if len(text) > 500:
        text = text[:497] + "..."
    return f": {text}"


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
