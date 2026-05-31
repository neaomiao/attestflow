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

CI_PROVIDER_DEFAULTS: dict[str, dict[str, Any]] = {
    "github-actions": {"command": "gh", "env": "ATTESTFLOW_GITHUB_ACTIONS_COMMAND", "args": GITHUB_ACTIONS_STATUS_ARGS},
    "gitlab-ci": {"command": "glab", "env": "ATTESTFLOW_GITLAB_CI_COMMAND", "args": ["ci", "status", "--output", "json"]},
    "buildkite": {"command": "buildkite-agent", "env": "ATTESTFLOW_BUILDKITE_COMMAND", "args": ["pipeline", "status", "--format", "json"]},
    "circleci": {"command": "circleci", "env": "ATTESTFLOW_CIRCLECI_COMMAND", "args": ["workflow", "list", "--output", "json"]},
}


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"invalid CI adapter input JSON: {exc}\n")
        return 1
    provider = str(payload.get("provider", ""))
    if provider not in CI_PROVIDER_DEFAULTS:
        sys.stderr.write(f"unsupported built-in CI provider: {provider}\n")
        return 1
    if provider == "github-actions":
        result = run_github_actions(payload)
    else:
        result = run_ci_provider(payload, provider)
    print(json.dumps(result, ensure_ascii=False))
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


def run_ci_provider(payload: dict[str, Any], provider: str) -> dict[str, Any]:
    options = _options(payload)
    command = _command(options, provider)
    if not _command_exists(command):
        return _blocked(f"{provider} command not found: {command}", provider=provider)
    args = _status_args(options, provider)
    repository = options.get("repository")
    if repository:
        args.extend(["--repo", str(repository)])
    completed = _run_provider_command(payload, provider, command, args, options)
    if isinstance(completed, dict):
        return completed
    try:
        raw = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return _blocked(f"{provider} status did not return JSON: {exc}", provider=provider)
    item = _first_record(raw)
    if not item:
        return {
            "schema_version": 1,
            "provider": provider,
            "status": "unknown",
            "summary": f"{provider} no runs found",
            "checks": [],
            "raw": raw,
        }
    status = _map_ci_status(item.get("status") or item.get("state") or item.get("conclusion"))
    external_id = _first_string(item, "id", "number", "pipeline_id", "workflow_id")
    url = _first_string(item, "web_url", "url", "html_url")
    branch = _first_string(item, "ref", "branch") or _nested_string(item, "vcs", "branch")
    commit = _first_string(item, "sha", "commit") or _nested_string(item, "vcs", "revision")
    name = _first_string(item, "name", "workflowName", "pipeline_name") or provider
    return {
        "schema_version": 1,
        "provider": provider,
        "status": status,
        "summary": f"{provider} {name}: {status}",
        "external_id": external_id,
        "url": url,
        "workflow": name,
        "branch": branch,
        "commit": commit,
        "checks": [{"name": name, "status": status, "external_id": external_id, "url": url}],
        "raw": item,
    }


def _run_provider_command(
    payload: dict[str, Any],
    provider: str,
    command: str,
    args: list[str],
    options: dict[str, Any],
) -> subprocess.CompletedProcess[str] | dict[str, Any]:
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
        return _blocked(f"{provider} status timed out{_output_suffix(exc.stdout, exc.stderr)}", provider=provider)
    except (OSError, ValueError) as exc:
        return _blocked(f"{provider} status could not run: {exc}", provider=provider)
    if completed.returncode != 0:
        return _blocked(
            f"{provider} status failed with exit code {completed.returncode}{_output_suffix(completed.stdout, completed.stderr)}",
            provider=provider,
        )
    return completed


def _command(options: dict[str, Any], provider: str = "github-actions") -> str:
    defaults = CI_PROVIDER_DEFAULTS[provider]
    env_value = os.environ.get(str(defaults["env"]))
    return str(options.get("command") or env_value or defaults["command"])


def _status_args(options: dict[str, Any], provider: str = "github-actions") -> list[str]:
    configured = options.get("status_args")
    if provider == "github-actions" and configured is None and os.environ.get("ATTESTFLOW_GITHUB_ACTIONS_STATUS_ARGS"):
        configured = shlex.split(os.environ["ATTESTFLOW_GITHUB_ACTIONS_STATUS_ARGS"])
    if configured is None:
        configured = CI_PROVIDER_DEFAULTS[provider]["args"]
    return [str(item) for item in configured] if isinstance(configured, list) else shlex.split(str(configured))


def _timeout(options: dict[str, Any]) -> int:
    configured = options.get("timeout_seconds", 30)
    return configured if type(configured) is int and configured > 0 else 30


def _command_exists(command: str) -> bool:
    return bool(shutil.which(command) or Path(command).exists())


def _blocked(summary: str, *, provider: str = "github-actions") -> dict[str, Any]:
    return {
        "schema_version": 1,
        "provider": provider,
        "status": "blocked",
        "summary": summary,
        "checks": [],
    }


def _first_record(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list) and raw and isinstance(raw[0], dict):
        return raw[0]
    return {}


def _map_ci_status(value: Any) -> str:
    text = str(value or "").lower()
    if text in {"success", "succeeded", "passed", "pass", "completed"}:
        return "passed"
    if text in {"failure", "failed", "failing", "error", "errored", "timed_out"}:
        return "failed"
    if text in {"running", "in_progress", "building", "waiting"}:
        return "running"
    if text in {"queued", "pending", "created", "scheduled", "requested"}:
        return "queued"
    if text in {"cancelled", "canceled"}:
        return "cancelled"
    if text == "skipped":
        return "skipped"
    if text in {"blocked", "manual", "action_required", "on_hold"}:
        return "blocked"
    return "unknown"


def _first_string(item: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = item.get(key)
        if value is not None and str(value):
            return str(value)
    return None


def _nested_string(item: dict[str, Any], outer: str, inner: str) -> str | None:
    nested = item.get(outer)
    if isinstance(nested, dict) and nested.get(inner) is not None:
        return str(nested[inner])
    return None


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
