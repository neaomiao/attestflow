from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time
from typing import Any


GITHUB_ACTIONS_RUN_JSON_FIELDS = (
    "databaseId,status,conclusion,workflowName,displayTitle,headBranch,headSha,url,createdAt,updatedAt,event"
)
GITHUB_ACTIONS_VIEW_JSON_FIELDS = (
    "databaseId,status,conclusion,workflowName,displayTitle,headBranch,headSha,url,createdAt,updatedAt,event,jobs"
)
GITHUB_ACTIONS_STATUS_ARGS = ["run", "list", "--limit", "1", "--json", GITHUB_ACTIONS_RUN_JSON_FIELDS]
GITHUB_ACTIONS_TERMINAL_STATUSES = {"passed", "failed", "cancelled", "skipped", "blocked"}

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
    action = _action(payload)
    if action == "status":
        return _github_actions_status(payload, command, options)
    if action == "await":
        return _github_actions_await(payload, command, options)
    if action == "logs":
        return _github_actions_logs(payload, command, options)
    if action == "artifacts":
        return _github_actions_artifacts(payload, command, options)
    if action == "rerun":
        return _github_actions_rerun(payload, command, options)
    if action == "dispatch":
        return _github_actions_dispatch(payload, command, options)
    return _blocked(f"unsupported github-actions action: {action}")


def _github_actions_status(
    payload: dict[str, Any],
    command: str,
    options: dict[str, Any],
    *,
    action: str = "status",
) -> dict[str, Any]:
    resolved = _resolve_github_run(payload, command, options)
    if isinstance(resolved, dict) and resolved.get("status") == "blocked":
        resolved["action"] = action
        return resolved
    run = resolved if isinstance(resolved, dict) else {}
    if not run:
        return {
            "schema_version": 1,
            "provider": "github-actions",
            "action": action,
            "status": "unknown",
            "summary": "No GitHub Actions runs found",
            "checks": [],
        }
    output = _github_run_output(run, action=action)
    if output["status"] == "failed" and _bool_option(options, "include_failure_details", True):
        _enrich_github_failure_details(output, payload, command, options, best_effort=True)
    return output


def _github_actions_await(payload: dict[str, Any], command: str, options: dict[str, Any]) -> dict[str, Any]:
    max_wait = _float_option(options, "max_wait_seconds", 600.0)
    interval = _float_option(options, "poll_interval_seconds", 10.0)
    deadline = time.monotonic() + max_wait
    polls = 0
    last: dict[str, Any] | None = None
    while True:
        polls += 1
        last = _github_actions_status(payload, command, options, action="await")
        last["polls"] = polls
        if last.get("status") in GITHUB_ACTIONS_TERMINAL_STATUSES:
            return last
        if time.monotonic() >= deadline:
            last["summary"] = f"GitHub Actions did not finish before timeout: {last.get('status', 'unknown')}"
            return last
        if interval > 0:
            time.sleep(min(interval, max(0.0, deadline - time.monotonic())))


def _github_actions_logs(payload: dict[str, Any], command: str, options: dict[str, Any]) -> dict[str, Any]:
    resolved = _resolve_github_run(payload, command, options)
    if isinstance(resolved, dict) and resolved.get("status") == "blocked":
        resolved["action"] = "logs"
        return resolved
    run = resolved if isinstance(resolved, dict) else {}
    if not run:
        return _blocked("github-actions logs require a run_id or a resolvable run")
    output = _github_run_output(run, action="logs")
    details = _view_github_run(payload, command, options, str(output.get("external_id") or ""))
    if isinstance(details, dict) and details.get("status") == "blocked":
        return details
    if details:
        output.update(_github_run_output(details, action="logs"))
        _merge_github_jobs(output, details)
        _collect_github_annotations(output, payload, command, options)
    log = _github_failed_log(payload, command, options, str(output.get("external_id") or ""))
    if isinstance(log, dict):
        return log
    output["logs"] = {"failed": log}
    output["failure_summary"] = _github_failure_summary(output)
    return output


def _github_actions_artifacts(payload: dict[str, Any], command: str, options: dict[str, Any]) -> dict[str, Any]:
    run_id = _run_id_from_options_or_run(payload, command, options)
    if not run_id:
        return _blocked("github-actions artifacts require a run_id or a resolvable run")
    repository = str(options.get("repository") or "").strip()
    artifacts: list[dict[str, Any]] = []
    if repository:
        args = _artifact_args(options, repository, run_id)
        completed = _run_gh(payload, "github-actions artifacts", command, args, options)
        if isinstance(completed, dict):
            return completed
        try:
            raw = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            return _blocked(f"github-actions artifacts did not return JSON: {exc}")
        raw_artifacts = raw.get("artifacts") if isinstance(raw, dict) else raw
        artifacts = [item for item in raw_artifacts if isinstance(item, dict)] if isinstance(raw_artifacts, list) else []
    download_dir = options.get("download_dir")
    if download_dir or _bool_option(options, "download_artifacts", False):
        target = str(download_dir or Path(str(payload.get("root") or ".")) / "attestflow-artifacts" / run_id)
        args = ["run", "download", run_id, "--dir", target]
        _append_repository(args, options)
        completed = _run_gh(payload, "github-actions artifact download", command, args, options)
        if isinstance(completed, dict):
            return completed
    else:
        target = None
    output = {
        "schema_version": 1,
        "provider": "github-actions",
        "action": "artifacts",
        "status": "passed",
        "summary": f"GitHub Actions artifacts collected for run {run_id}",
        "external_id": run_id,
        "artifacts": artifacts,
        "checks": [{"name": "github-actions artifacts", "status": "passed", "external_id": run_id}],
    }
    if target:
        output["download_dir"] = target
    return output


def _github_actions_rerun(payload: dict[str, Any], command: str, options: dict[str, Any]) -> dict[str, Any]:
    run_id = _run_id_from_options_or_run(payload, command, options)
    if not run_id:
        return _blocked("github-actions rerun requires a run_id or a resolvable run")
    args = ["run", "rerun", run_id]
    if _bool_option(options, "rerun_failed", False):
        args.append("--failed")
    _append_repository(args, options)
    completed = _run_gh(payload, "github-actions rerun", command, args, options)
    if isinstance(completed, dict):
        return completed
    return {
        "schema_version": 1,
        "provider": "github-actions",
        "action": "rerun",
        "status": "queued",
        "summary": f"GitHub Actions rerun requested for run {run_id}",
        "external_id": run_id,
        "checks": [{"name": "github-actions rerun", "status": "queued", "external_id": run_id}],
    }


def _github_actions_dispatch(payload: dict[str, Any], command: str, options: dict[str, Any]) -> dict[str, Any]:
    workflow = str(options.get("workflow") or "").strip()
    if not workflow:
        return _blocked("github-actions dispatch requires provider_options.workflow")
    ref = str(options.get("ref") or options.get("branch") or "").strip()
    if not ref:
        return _blocked("github-actions dispatch requires provider_options.ref or provider_options.branch")
    args = ["workflow", "run", workflow, "--ref", ref]
    inputs = options.get("inputs", {})
    if isinstance(inputs, dict):
        for key, value in sorted(inputs.items()):
            args.extend(["-f", f"{key}={value}"])
    _append_repository(args, options)
    completed = _run_gh(payload, "github-actions dispatch", command, args, options)
    if isinstance(completed, dict):
        return completed
    return {
        "schema_version": 1,
        "provider": "github-actions",
        "action": "dispatch",
        "status": "queued",
        "summary": f"GitHub Actions workflow dispatched: {workflow}@{ref}",
        "workflow": workflow,
        "branch": ref,
        "checks": [{"name": workflow, "status": "queued"}],
    }


def _resolve_github_run(payload: dict[str, Any], command: str, options: dict[str, Any]) -> dict[str, Any]:
    run_id = str(options.get("run_id") or "").strip()
    if run_id:
        return _view_github_run(payload, command, options, run_id, fields=GITHUB_ACTIONS_RUN_JSON_FIELDS)
    args = _status_args(options)
    _append_repository(args, options)
    completed = _run_gh(payload, "github-actions status", command, args, options)
    if isinstance(completed, dict):
        return completed
    try:
        runs = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return _blocked(f"github-actions status did not return JSON: {exc}")
    if not isinstance(runs, list) or not runs:
        return {}
    return _select_github_run(runs, options)


def _select_github_run(runs: list[Any], options: dict[str, Any]) -> dict[str, Any]:
    for raw in runs:
        if isinstance(raw, dict) and _github_run_matches(raw, options):
            return raw
    first = runs[0]
    return first if isinstance(first, dict) else {}


def _github_run_matches(run: dict[str, Any], options: dict[str, Any]) -> bool:
    run_id = str(options.get("run_id") or "").strip()
    if run_id and run_id != str(run.get("databaseId") or run.get("id") or ""):
        return False
    expected_sha = str(options.get("head_sha") or options.get("commit") or "").strip()
    if expected_sha and expected_sha != str(run.get("headSha") or run.get("head_sha") or run.get("commit") or ""):
        return False
    expected_branch = str(options.get("branch") or "").strip()
    if expected_branch and expected_branch != str(run.get("headBranch") or run.get("branch") or ""):
        return False
    expected_event = str(options.get("event") or "").strip()
    if expected_event and run.get("event") is not None and expected_event != str(run.get("event")):
        return False
    return True


def _github_run_output(run: dict[str, Any], *, action: str) -> dict[str, Any]:
    status = _map_github_status(str(run.get("status", "")), run.get("conclusion"))
    workflow = str(run.get("workflowName") or run.get("name") or "")
    title = str(run.get("displayTitle") or "")
    summary_subject = " / ".join(item for item in (workflow, title) if item)
    external_id = str(run.get("databaseId") or run.get("id") or "")
    return {
        "schema_version": 1,
        "provider": "github-actions",
        "action": action,
        "status": status,
        "summary": f"GitHub Actions {summary_subject or 'latest run'}: {status}",
        "external_id": external_id,
        "url": run.get("url"),
        "workflow": workflow or None,
        "title": title or None,
        "branch": run.get("headBranch"),
        "commit": run.get("headSha"),
        "event": run.get("event"),
        "started_at": run.get("createdAt"),
        "ended_at": run.get("updatedAt"),
        "checks": [
            {
                "name": workflow or "github-actions",
                "status": status,
                "external_id": external_id,
                "url": run.get("url"),
            }
        ],
        "raw": run,
    }


def _enrich_github_failure_details(
    output: dict[str, Any],
    payload: dict[str, Any],
    command: str,
    options: dict[str, Any],
    *,
    best_effort: bool,
) -> None:
    run_id = str(output.get("external_id") or "").strip()
    if not run_id:
        return
    warnings: list[str] = []
    details = _view_github_run(payload, command, options, run_id)
    if isinstance(details, dict) and details.get("status") == "blocked":
        warnings.append(str(details.get("summary") or "failed to read run details"))
        if not best_effort:
            output.update(details)
            return
    elif details:
        _merge_github_jobs(output, details)
        _collect_github_annotations(output, payload, command, options)
    log = _github_failed_log(payload, command, options, run_id)
    if isinstance(log, dict):
        warnings.append(str(log.get("summary") or "failed to read failed logs"))
        if not best_effort:
            output.update(log)
            return
    else:
        output["logs"] = {"failed": log}
    output["failure_summary"] = _github_failure_summary(output)
    if warnings:
        output["warnings"] = warnings


def _merge_github_jobs(output: dict[str, Any], run: dict[str, Any]) -> None:
    jobs = run.get("jobs")
    if isinstance(jobs, list):
        output["jobs"] = [job for job in jobs if isinstance(job, dict)]
        annotations: list[dict[str, Any]] = []
        checks = []
        for job in output["jobs"]:
            name = str(job.get("name") or job.get("displayName") or "github-actions job")
            status = _map_github_status(str(job.get("status", "")), job.get("conclusion"))
            checks.append({"name": name, "status": status, "url": job.get("url")})
            raw_annotations = job.get("annotations", [])
            if isinstance(raw_annotations, list):
                annotations.extend(item for item in raw_annotations if isinstance(item, dict))
        if checks:
            output["checks"] = checks
        if annotations:
            output["annotations"] = annotations


def _collect_github_annotations(
    output: dict[str, Any],
    payload: dict[str, Any],
    command: str,
    options: dict[str, Any],
) -> None:
    repository = str(options.get("repository") or "").strip()
    jobs = output.get("jobs")
    if not repository or not isinstance(jobs, list):
        return
    annotations = list(output.get("annotations", [])) if isinstance(output.get("annotations"), list) else []
    warnings = list(output.get("warnings", [])) if isinstance(output.get("warnings"), list) else []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        check_run_id = str(job.get("databaseId") or job.get("id") or "").strip()
        if not check_run_id:
            continue
        args = ["api", f"repos/{repository}/check-runs/{check_run_id}/annotations"]
        completed = _run_gh(payload, "github-actions annotations", command, args, options)
        if isinstance(completed, dict):
            warnings.append(str(completed.get("summary") or f"failed to read annotations for {check_run_id}"))
            continue
        try:
            raw = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            warnings.append(f"github-actions annotations did not return JSON for {check_run_id}: {exc}")
            continue
        if isinstance(raw, list):
            annotations.extend(item for item in raw if isinstance(item, dict))
    if annotations:
        output["annotations"] = annotations
    if warnings:
        output["warnings"] = warnings


def _github_failure_summary(output: dict[str, Any]) -> dict[str, Any]:
    failed_jobs: list[str] = []
    failed_steps: list[dict[str, str]] = []
    for job in output.get("jobs", []) if isinstance(output.get("jobs"), list) else []:
        if not isinstance(job, dict):
            continue
        job_name = str(job.get("name") or job.get("displayName") or "github-actions job")
        job_status = _map_github_status(str(job.get("status", "")), job.get("conclusion"))
        if job_status == "failed":
            failed_jobs.append(job_name)
        steps = job.get("steps", [])
        if isinstance(steps, list):
            for step in steps:
                if isinstance(step, dict) and _map_github_status(str(step.get("status", "")), step.get("conclusion")) == "failed":
                    failed_steps.append({"job": job_name, "step": str(step.get("name") or "unnamed step")})
    return {
        "failed_jobs": failed_jobs,
        "failed_steps": failed_steps,
        "annotation_count": len(output.get("annotations", [])) if isinstance(output.get("annotations"), list) else 0,
        "has_failed_log": bool(str(output.get("logs", {}).get("failed", "")).strip())
        if isinstance(output.get("logs"), dict)
        else False,
    }


def _view_github_run(
    payload: dict[str, Any],
    command: str,
    options: dict[str, Any],
    run_id: str,
    *,
    fields: str = GITHUB_ACTIONS_VIEW_JSON_FIELDS,
) -> dict[str, Any]:
    configured = options.get("view_json_fields")
    json_fields = ",".join(str(item) for item in configured) if isinstance(configured, list) else str(configured or fields)
    args = ["run", "view", run_id, "--json", json_fields]
    _append_repository(args, options)
    completed = _run_gh(payload, "github-actions run view", command, args, options)
    if isinstance(completed, dict):
        return completed
    try:
        raw = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return _blocked(f"github-actions run view did not return JSON: {exc}")
    return raw if isinstance(raw, dict) else {}


def _github_failed_log(payload: dict[str, Any], command: str, options: dict[str, Any], run_id: str) -> str | dict[str, Any]:
    args = ["run", "view", run_id, "--log-failed"]
    _append_repository(args, options)
    completed = _run_gh(payload, "github-actions failed logs", command, args, options)
    if isinstance(completed, dict):
        return completed
    return _clip_text(completed.stdout, _int_option(options, "max_log_bytes", 60000))


def _run_id_from_options_or_run(payload: dict[str, Any], command: str, options: dict[str, Any]) -> str | None:
    run_id = str(options.get("run_id") or "").strip()
    if run_id:
        return run_id
    resolved = _resolve_github_run(payload, command, options)
    if isinstance(resolved, dict) and resolved.get("status") == "blocked":
        return None
    value = resolved.get("databaseId") or resolved.get("id") if isinstance(resolved, dict) else None
    return str(value) if value is not None and str(value).strip() else None


def _run_gh(
    payload: dict[str, Any],
    label: str,
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
        return _blocked(f"{label} timed out{_output_suffix(exc.stdout, exc.stderr)}")
    except (OSError, ValueError) as exc:
        return _blocked(f"{label} could not run: {exc}")
    if completed.returncode != 0:
        return _blocked(f"{label} failed with exit code {completed.returncode}{_output_suffix(completed.stdout, completed.stderr)}")
    return completed


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


def _action(payload: dict[str, Any]) -> str:
    options = _options(payload)
    return str(payload.get("action") or options.get("action") or "status").strip().replace("_", "-") or "status"


def run_ci_provider(payload: dict[str, Any], provider: str) -> dict[str, Any]:
    if _action(payload) != "status":
        return _blocked(f"{provider} adapter supports only status action", provider=provider)
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
        if provider == "github-actions":
            args = [
                "run",
                "list",
                "--limit",
                str(_int_option(options, "limit", 1)),
                "--json",
                GITHUB_ACTIONS_RUN_JSON_FIELDS,
            ]
            filters = (
                ("branch", "--branch"),
                ("head_sha", "--commit"),
                ("commit", "--commit"),
                ("workflow", "--workflow"),
                ("event", "--event"),
                ("status_filter", "--status"),
            )
            seen_flags: set[str] = set()
            for key, flag in filters:
                value = options.get(key)
                if value is not None and str(value).strip() and flag not in seen_flags:
                    args.extend([flag, str(value)])
                    seen_flags.add(flag)
            return args
        configured = CI_PROVIDER_DEFAULTS[provider]["args"]
    return [str(item) for item in configured] if isinstance(configured, list) else shlex.split(str(configured))


def _artifact_args(options: dict[str, Any], repository: str, run_id: str) -> list[str]:
    configured = options.get("artifact_args")
    if configured is not None:
        return [str(item) for item in configured] if isinstance(configured, list) else shlex.split(str(configured))
    return ["api", f"repos/{repository}/actions/runs/{run_id}/artifacts"]


def _append_repository(args: list[str], options: dict[str, Any]) -> None:
    repository = options.get("repository")
    if repository:
        args.extend(["--repo", str(repository)])


def _timeout(options: dict[str, Any]) -> float:
    configured = options.get("timeout_seconds", 30)
    return configured if type(configured) in {int, float} and configured > 0 else 30


def _int_option(options: dict[str, Any], key: str, default: int) -> int:
    value = options.get(key, default)
    if type(value) is int and value > 0:
        return value
    if isinstance(value, str) and value.isdigit() and int(value) > 0:
        return int(value)
    return default


def _float_option(options: dict[str, Any], key: str, default: float) -> float:
    value = options.get(key, default)
    if type(value) in {int, float} and value >= 0:
        return float(value)
    try:
        parsed = float(str(value))
    except ValueError:
        return default
    return parsed if parsed >= 0 else default


def _bool_option(options: dict[str, Any], key: str, default: bool) -> bool:
    value = options.get(key, default)
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _clip_text(text: str, max_bytes: int) -> str:
    if max_bytes <= 0:
        return text
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    clipped = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return clipped + "\n[attestflow: log truncated]"


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
