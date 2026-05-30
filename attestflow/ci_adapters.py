from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from typing import Any


GITHUB_ACTIONS_STATUS_ARGS = [
    "run",
    "list",
    "--limit",
    "1",
    "--json",
    "databaseId,status,conclusion,workflowName,displayTitle,headBranch,headSha,url,createdAt,updatedAt",
]


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"invalid CI adapter input JSON: {exc}\n")
        return 1
    provider = str(payload.get("provider", ""))
    if provider != "github-actions":
        sys.stderr.write(f"unsupported built-in CI provider: {provider}\n")
        return 1
    print(json.dumps(run_github_actions(payload), ensure_ascii=False))
    return 0


def run_github_actions(payload: dict[str, Any]) -> dict[str, Any]:
    options = _options(payload)
    command = _command(options)
    if not _command_exists(command):
        return _blocked(f"github-actions command not found: {command}")
    args = _status_args(options)
    repository = options.get("repository")
    if repository:
        args.extend(["--repo", str(repository)])
    try:
        completed = subprocess.run(
            [command, *args],
            cwd=str(payload.get("root") or "."),
            text=True,
            capture_output=True,
            check=False,
            timeout=_timeout(options),
        )
    except subprocess.TimeoutExpired as exc:
        return _blocked(f"github-actions status timed out{_output_suffix(exc.stdout, exc.stderr)}")
    except (OSError, ValueError) as exc:
        return _blocked(f"github-actions status could not run: {exc}")
    if completed.returncode != 0:
        return _blocked(
            f"github-actions status failed with exit code {completed.returncode}{_output_suffix(completed.stdout, completed.stderr)}"
        )
    try:
        runs = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return _blocked(f"github-actions status did not return JSON: {exc}")
    if not isinstance(runs, list) or not runs:
        return {
            "schema_version": 1,
            "provider": "github-actions",
            "status": "unknown",
            "summary": "No GitHub Actions runs found",
            "checks": [],
        }
    run = runs[0] if isinstance(runs[0], dict) else {}
    status = _map_github_status(str(run.get("status", "")), run.get("conclusion"))
    workflow = str(run.get("workflowName") or "")
    title = str(run.get("displayTitle") or "")
    summary_subject = " / ".join(item for item in (workflow, title) if item)
    return {
        "schema_version": 1,
        "provider": "github-actions",
        "status": status,
        "summary": f"GitHub Actions {summary_subject or 'latest run'}: {status}",
        "external_id": str(run.get("databaseId") or ""),
        "url": run.get("url"),
        "workflow": workflow or None,
        "title": title or None,
        "branch": run.get("headBranch"),
        "commit": run.get("headSha"),
        "started_at": run.get("createdAt"),
        "ended_at": run.get("updatedAt"),
        "checks": [
            {
                "name": workflow or "github-actions",
                "status": status,
                "external_id": str(run.get("databaseId") or ""),
                "url": run.get("url"),
            }
        ],
        "raw": run,
    }


def _map_github_status(status: str, conclusion: Any) -> str:
    conclusion_text = str(conclusion or "").lower()
    status_text = status.lower()
    if conclusion_text == "success":
        return "passed"
    if conclusion_text in {"failure", "timed_out", "action_required", "startup_failure"}:
        return "failed"
    if conclusion_text == "cancelled":
        return "cancelled"
    if conclusion_text == "skipped":
        return "skipped"
    if status_text in {"in_progress", "waiting"}:
        return "running"
    if status_text in {"queued", "pending", "requested"}:
        return "queued"
    return "unknown"


def _options(payload: dict[str, Any]) -> dict[str, Any]:
    options = payload.get("provider_options", {})
    return options if isinstance(options, dict) else {}


def _command(options: dict[str, Any]) -> str:
    return str(options.get("command") or os.environ.get("ATTESTFLOW_GITHUB_ACTIONS_COMMAND") or "gh")


def _status_args(options: dict[str, Any]) -> list[str]:
    configured = options.get("status_args")
    if configured is None and os.environ.get("ATTESTFLOW_GITHUB_ACTIONS_STATUS_ARGS"):
        configured = shlex.split(os.environ["ATTESTFLOW_GITHUB_ACTIONS_STATUS_ARGS"])
    if configured is None:
        configured = GITHUB_ACTIONS_STATUS_ARGS
    return [str(item) for item in configured] if isinstance(configured, list) else shlex.split(str(configured))


def _timeout(options: dict[str, Any]) -> int:
    configured = options.get("timeout_seconds", 30)
    return configured if type(configured) is int and configured > 0 else 30


def _command_exists(command: str) -> bool:
    return bool(shutil.which(command) or Path(command).exists())


def _blocked(summary: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "provider": "github-actions",
        "status": "blocked",
        "summary": summary,
        "checks": [],
    }


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
