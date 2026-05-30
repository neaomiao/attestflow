from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from typing import Any


PROVIDER_DEFAULTS: dict[str, dict[str, Any]] = {
    "codex": {
        "command": "codex",
        "launch_args": ["exec", "--json", "--sandbox", "workspace-write"],
        "resume_args": ["exec", "resume", "{external_session_id}", "--json"],
        "resume_without_id_args": ["exec", "resume", "--last", "--json"],
    },
    "claude-code": {
        "command": "claude",
        "launch_args": ["-p", "--output-format", "json"],
        "resume_args": ["--resume", "{external_session_id}", "-p", "--output-format", "json"],
        "resume_without_id_args": ["--continue", "-p", "--output-format", "json"],
    },
    "opencode": {
        "command": "opencode",
        "launch_args": ["run", "--format", "json", "--title", "{session_id}"],
        "resume_args": ["run", "--format", "json", "--session", "{external_session_id}"],
        "resume_without_id_args": ["run", "--format", "json", "--continue"],
    },
}


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        _emit("blocked", None, f"invalid adapter input JSON: {exc}")
        return 0

    provider = str(payload.get("agent_provider", ""))
    action = str(payload.get("action", ""))
    if provider not in PROVIDER_DEFAULTS:
        _emit("blocked", None, f"unsupported built-in session provider: {provider}")
        return 0
    if action not in {"launch", "resume"}:
        _emit("blocked", None, f"unsupported session adapter action: {action}")
        return 0

    command = _command(provider, payload)
    if not _command_exists(command):
        _emit("blocked", None, f"{provider} command not found: {command}")
        return 0

    prompt = str(payload.get("prompt_packet", {}).get("content", ""))
    try:
        args = _args(provider, action, payload)
        completed = subprocess.run(
            [command, *args, prompt],
            cwd=str(payload.get("root") or "."),
            text=True,
            capture_output=True,
            check=False,
        )
    except (OSError, ValueError, KeyError, IndexError) as exc:
        _emit("blocked", None, f"{provider} command could not run: {exc}")
        return 0
    if completed.stderr:
        sys.stderr.write(completed.stderr)
    if completed.returncode != 0:
        _emit("blocked", None, f"{provider} command failed with exit code {completed.returncode}")
        return 0

    external_session_id = _external_session_id(completed.stdout) or _existing_external_session_id(payload)
    status = "launched" if action == "launch" else "resumed"
    summary = f"{provider} {action} completed"
    _emit(status, external_session_id, summary)
    return 0


def _command(provider: str, payload: dict[str, Any]) -> str:
    options = _options(payload)
    env_name = f"ATTESTFLOW_{_env_provider(provider)}_COMMAND"
    return str(options.get("command") or os.environ.get(env_name) or PROVIDER_DEFAULTS[provider]["command"])


def _args(provider: str, action: str, payload: dict[str, Any]) -> list[str]:
    options = _options(payload)
    option_key = f"{action}_args"
    env_name = f"ATTESTFLOW_{_env_provider(provider)}_{action.upper()}_ARGS"
    configured = options.get(option_key)
    if configured is None and os.environ.get(env_name):
        configured = shlex.split(os.environ[env_name])
    if configured is None and action == "resume" and not _existing_external_session_id(payload):
        configured = PROVIDER_DEFAULTS[provider]["resume_without_id_args"]
    if configured is None:
        configured = PROVIDER_DEFAULTS[provider][option_key]
    raw_args = [str(item) for item in configured] if isinstance(configured, list) else shlex.split(str(configured))
    rendered = [_render_arg(arg, payload) for arg in raw_args]
    return [arg for arg in rendered if arg]


def _render_arg(arg: str, payload: dict[str, Any]) -> str:
    session = payload.get("session", {}) if isinstance(payload.get("session"), dict) else {}
    values = {
        "session_id": str(session.get("session_id", "")),
        "external_session_id": str(session.get("external_session_id", "")),
        "task_id": str(session.get("task_id", "")),
        "run_id": str(session.get("run_id", "")),
        "root": str(payload.get("root", "")),
    }
    return arg.format(**values)


def _options(payload: dict[str, Any]) -> dict[str, Any]:
    options = payload.get("provider_options", {})
    return options if isinstance(options, dict) else {}


def _command_exists(command: str) -> bool:
    return bool(shutil.which(command) or Path(command).exists())


def _external_session_id(text: str) -> str | None:
    for item in _json_items(text):
        found = _find_first_key(item, {"thread_id", "session_id", "sessionId", "sessionID", "conversation_id"})
        if found:
            return str(found)
    return None


def _json_items(text: str) -> list[Any]:
    stripped = text.strip()
    if not stripped:
        return []
    try:
        return [json.loads(stripped)]
    except json.JSONDecodeError:
        pass
    items: list[Any] = []
    for line in stripped.splitlines():
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return items


def _find_first_key(value: Any, keys: set[str]) -> Any:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in keys and item:
                return item
        for item in value.values():
            found = _find_first_key(item, keys)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = _find_first_key(item, keys)
            if found:
                return found
    return None


def _existing_external_session_id(payload: dict[str, Any]) -> str | None:
    session = payload.get("session", {}) if isinstance(payload.get("session"), dict) else {}
    value = session.get("external_session_id")
    return str(value) if value else None


def _env_provider(provider: str) -> str:
    return provider.upper().replace("-", "_")


def _emit(status: str, external_session_id: str | None, summary: str) -> None:
    output = {
        "schema_version": 1,
        "status": status,
        "summary": summary,
        "resume_command": _self_command(),
    }
    if external_session_id:
        output["external_session_id"] = external_session_id
    print(json.dumps(output, ensure_ascii=False))


def _self_command() -> str:
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(Path(__file__).resolve()))}"


if __name__ == "__main__":
    raise SystemExit(main())
