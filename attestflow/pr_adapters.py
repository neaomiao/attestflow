from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from typing import Any


PR_PROVIDER_DEFAULTS: dict[str, dict[str, Any]] = {
    "github": {
        "command": "gh",
        "env": "ATTESTFLOW_GITHUB_PR_COMMAND",
        "status_args": ["pr", "view", "--json", "number,url,state,isDraft,headRefName,baseRefName"],
        "ensure_args": ["pr", "create", "--json", "number,url,state,isDraft,headRefName,baseRefName"],
    },
    "gitlab": {
        "command": "glab",
        "env": "ATTESTFLOW_GITLAB_PR_COMMAND",
        "status_args": ["mr", "view", "--output", "json"],
        "ensure_args": ["mr", "create", "--output", "json"],
    },
}


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"invalid PR adapter input JSON: {exc}\n")
        return 1
    provider = str(payload.get("provider", ""))
    if provider not in PR_PROVIDER_DEFAULTS:
        sys.stderr.write(f"unsupported built-in PR provider: {provider}\n")
        return 1
    print(json.dumps(run_pr_provider(payload, provider), ensure_ascii=False))
    return 0


def run_pr_provider(payload: dict[str, Any], provider: str) -> dict[str, Any]:
    options = _options(payload)
    command = _command(options, provider)
    if not _command_exists(command):
        return _blocked(provider, f"{provider} command not found: {command}")
    args = _action_args(options, provider, str(payload.get("action") or "status"))
    repository = options.get("repository")
    if repository:
        args.extend(["--repo", str(repository)])
    completed = _run_command(payload, provider, command, args, options)
    if isinstance(completed, dict):
        return completed
    try:
        raw = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return _blocked(provider, f"{provider} PR command did not return JSON: {exc}")
    item = _first_record(raw)
    if not item:
        return _blocked(provider, f"{provider} PR command returned no PR data")
    return _pr_output(provider, item)


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
        return _blocked(provider, f"{provider} PR command timed out{_output_suffix(exc.stdout, exc.stderr)}")
    except (OSError, ValueError) as exc:
        return _blocked(provider, f"{provider} PR command could not run: {exc}")
    if completed.returncode != 0:
        return _blocked(
            provider,
            f"{provider} PR command failed with exit code {completed.returncode}{_output_suffix(completed.stdout, completed.stderr)}",
        )
    return completed


def _pr_output(provider: str, item: dict[str, Any]) -> dict[str, Any]:
    status = _map_pr_status(item)
    external_id = _first_string(item, "number", "iid", "id")
    url = _first_string(item, "url", "web_url", "html_url")
    branch = _first_string(item, "headRefName", "source_branch", "head_ref", "branch")
    target_branch = _first_string(item, "baseRefName", "target_branch", "base_ref")
    return {
        "schema_version": 1,
        "provider": provider,
        "status": status,
        "summary": f"{provider} PR {external_id or 'current'}: {status}",
        "external_id": external_id,
        "url": url,
        "branch": branch,
        "target_branch": target_branch,
        "checks": item.get("checks", []) if isinstance(item.get("checks", []), list) else [],
        "raw": item,
    }


def _map_pr_status(item: dict[str, Any]) -> str:
    if item.get("isDraft") is True or item.get("draft") is True:
        return "draft"
    state = str(item.get("state") or item.get("status") or "").lower()
    if state in {"merged", "merged_event"}:
        return "merged"
    if state in {"open", "opened"}:
        return "open"
    if state in {"draft"}:
        return "draft"
    if state in {"closed", "declined", "failed"}:
        return "failed"
    if state in {"blocked"}:
        return "blocked"
    return "unknown"


def _options(payload: dict[str, Any]) -> dict[str, Any]:
    options = payload.get("provider_options", {})
    return options if isinstance(options, dict) else {}


def _command(options: dict[str, Any], provider: str) -> str:
    defaults = PR_PROVIDER_DEFAULTS[provider]
    return str(options.get("command") or os.environ.get(str(defaults["env"])) or defaults["command"])


def _action_args(options: dict[str, Any], provider: str, action: str) -> list[str]:
    key = "ensure_args" if action == "ensure" else "status_args"
    configured = options.get(key)
    if configured is None:
        configured = PR_PROVIDER_DEFAULTS[provider][key]
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
        "checks": [],
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
