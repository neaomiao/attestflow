from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from typing import Any


RELEASE_PROVIDER_DEFAULTS: dict[str, dict[str, Any]] = {
    "github-release": {
        "command": "gh",
        "env": "ATTESTFLOW_GITHUB_RELEASE_COMMAND",
        "release_args": ["release", "view", "--json", "id,tagName,url,isDraft,isLatest"],
    },
    "gitlab-release": {
        "command": "glab",
        "env": "ATTESTFLOW_GITLAB_RELEASE_COMMAND",
        "release_args": ["release", "view", "--output", "json"],
    },
    "linear": {
        "command": "linear",
        "env": "ATTESTFLOW_LINEAR_COMMAND",
        "release_args": ["issue", "view", "--json"],
    },
    "jira": {
        "command": "jira",
        "env": "ATTESTFLOW_JIRA_COMMAND",
        "release_args": ["issue", "view", "--json"],
    },
    "buildkite": {
        "command": "buildkite-agent",
        "env": "ATTESTFLOW_BUILDKITE_COMMAND",
        "release_args": ["pipeline", "status", "--format", "json"],
    },
    "circleci": {
        "command": "circleci",
        "env": "ATTESTFLOW_CIRCLECI_COMMAND",
        "release_args": ["workflow", "view", "--output", "json"],
    },
    "self-hosted-release": {
        "command": "attestflow-release",
        "env": "ATTESTFLOW_SELF_HOSTED_RELEASE_COMMAND",
        "release_args": ["status", "--json"],
    },
}


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"invalid release adapter input JSON: {exc}\n")
        return 1
    provider = str(payload.get("provider", ""))
    if provider not in RELEASE_PROVIDER_DEFAULTS:
        sys.stderr.write(f"unsupported built-in release provider: {provider}\n")
        return 1
    print(json.dumps(run_release_provider(payload, provider), ensure_ascii=False))
    return 0


def run_release_provider(payload: dict[str, Any], provider: str) -> dict[str, Any]:
    options = _options(payload)
    command = _command(options, provider)
    if not _command_exists(command):
        return _blocked(provider, f"{provider} command not found: {command}")
    args = _release_args(options, provider)
    repository = options.get("repository")
    if repository:
        args.extend(["--repo", str(repository)])
    completed = _run_command(payload, provider, command, args, options)
    if isinstance(completed, dict):
        return completed
    try:
        raw = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return _blocked(provider, f"{provider} release command did not return JSON: {exc}")
    item = _first_record(raw)
    if not item:
        return _blocked(provider, f"{provider} release command returned no release data")
    return _release_output(provider, item)


def _run_command(
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
        return _blocked(provider, f"{provider} release command timed out{_output_suffix(exc.stdout, exc.stderr)}")
    except (OSError, ValueError) as exc:
        return _blocked(provider, f"{provider} release command could not run: {exc}")
    if completed.returncode != 0:
        return _blocked(
            provider,
            f"{provider} release command failed with exit code {completed.returncode}{_output_suffix(completed.stdout, completed.stderr)}",
        )
    return completed


def _release_output(provider: str, item: dict[str, Any]) -> dict[str, Any]:
    status = _map_release_status(provider, item)
    external_id = _external_id(item)
    url = _url(item)
    artifacts = item.get("artifacts") if isinstance(item.get("artifacts"), list) else []
    if url and not artifacts:
        artifacts = [{"name": external_id or provider, "url": url}]
    return {
        "schema_version": 1,
        "provider": provider,
        "status": status,
        "summary": f"{provider} release {external_id or 'current'}: {status}",
        "external_id": external_id,
        "url": url,
        "artifacts": artifacts,
        "raw": item,
    }


def _map_release_status(provider: str, item: dict[str, Any]) -> str:
    if provider in {"github-release"} and item.get("isDraft") is True:
        return "running"
    if provider in {"github-release", "gitlab-release"} and _external_id(item):
        return "released"
    status = _status_text(item)
    if status in {"released", "done", "complete", "completed", "success", "succeeded", "passed", "pass", "merged"}:
        return "released"
    if status in {"running", "in_progress", "building", "deploying", "started"}:
        return "running"
    if status in {"queued", "pending", "created", "scheduled", "todo", "backlog", "open"}:
        return "queued"
    if status in {"blocked", "on_hold", "manual"}:
        return "blocked"
    if status in {"failed", "failure", "failing", "error", "cancelled", "canceled"}:
        return "failed"
    if status in {"skipped", "none"}:
        return "skipped"
    return "unknown"


def _status_text(item: dict[str, Any]) -> str:
    for key in ("status", "state", "conclusion"):
        value = item.get(key)
        if isinstance(value, dict):
            nested = value.get("name") or value.get("type")
            if nested is not None:
                return str(nested).lower()
        if value is not None:
            return str(value).lower()
    fields = item.get("fields")
    if isinstance(fields, dict):
        status = fields.get("status")
        if isinstance(status, dict) and status.get("name") is not None:
            return str(status["name"]).lower()
    return ""


def _external_id(item: dict[str, Any]) -> str | None:
    return _first_string(item, "id", "key", "tag_name", "tagName", "name")


def _url(item: dict[str, Any]) -> str | None:
    value = _first_string(item, "url", "web_url", "html_url", "self")
    if value:
        return value
    links = item.get("_links")
    if isinstance(links, dict) and links.get("self") is not None:
        return str(links["self"])
    return None


def _options(payload: dict[str, Any]) -> dict[str, Any]:
    options = payload.get("provider_options", {})
    return options if isinstance(options, dict) else {}


def _command(options: dict[str, Any], provider: str) -> str:
    defaults = RELEASE_PROVIDER_DEFAULTS[provider]
    return str(options.get("command") or os.environ.get(str(defaults["env"])) or defaults["command"])


def _release_args(options: dict[str, Any], provider: str) -> list[str]:
    configured = options.get("release_args")
    if configured is None:
        configured = RELEASE_PROVIDER_DEFAULTS[provider]["release_args"]
    return [str(item) for item in configured] if isinstance(configured, list) else shlex.split(str(configured))


def _timeout(options: dict[str, Any]) -> int:
    configured = options.get("timeout_seconds", 30)
    return configured if type(configured) is int and configured > 0 else 30


def _command_exists(command: str) -> bool:
    return bool(shutil.which(command) or Path(command).exists())


def _blocked(provider: str, summary: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "provider": provider,
        "status": "blocked",
        "summary": summary,
        "artifacts": [],
    }


def _first_record(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list) and raw and isinstance(raw[0], dict):
        return raw[0]
    return {}


def _first_string(item: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = item.get(key)
        if value is not None and str(value):
            return str(value)
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
