from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from typing import Any

try:
    from .agent_adapters import PROVIDER_DEFAULTS
except ImportError:  # pragma: no cover - supports direct script execution.
    from agent_adapters import PROVIDER_DEFAULTS


CAPABILITY_ARGS: dict[str, list[str]] = {
    "codex": ["exec", "--json", "--sandbox", "workspace-write"],
    "claude-code": ["-p", "--output-format", "json"],
    "opencode": ["run", "--format", "json"],
}


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"invalid capability adapter input JSON: {exc}\n")
        return 1

    provider = str(payload.get("agent_provider", ""))
    if provider not in PROVIDER_DEFAULTS:
        sys.stderr.write(f"unsupported built-in capability provider: {provider}\n")
        return 1

    command = _command(provider, payload)
    if not _command_exists(command):
        sys.stderr.write(f"{provider} command not found: {command}\n")
        return 1

    try:
        completed = subprocess.run(
            [command, *_args(provider, payload), _prompt(payload)],
            cwd=str(payload.get("root") or "."),
            text=True,
            capture_output=True,
            check=False,
        )
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"{provider} command could not run: {exc}\n")
        return 1

    if completed.stderr:
        sys.stderr.write(completed.stderr)
    if completed.returncode != 0:
        sys.stderr.write(f"{provider} command failed with exit code {completed.returncode}\n")
        return 1

    output = _extract_contract_json(completed.stdout, payload)
    if output is None:
        sys.stderr.write(f"{provider} command did not return capability contract JSON\n")
        return 1
    print(json.dumps(output, ensure_ascii=False))
    return 0


def _command(provider: str, payload: dict[str, Any]) -> str:
    options = _options(payload)
    env_name = f"ATTESTFLOW_{_env_provider(provider)}_COMMAND"
    return str(options.get("command") or os.environ.get(env_name) or PROVIDER_DEFAULTS[provider]["command"])


def _args(provider: str, payload: dict[str, Any]) -> list[str]:
    options = _options(payload)
    env_name = f"ATTESTFLOW_{_env_provider(provider)}_CAPABILITY_ARGS"
    configured = options.get("capability_args")
    if configured is None and os.environ.get(env_name):
        configured = shlex.split(os.environ[env_name])
    if configured is None:
        configured = CAPABILITY_ARGS[provider]
    return [str(item) for item in configured] if isinstance(configured, list) else shlex.split(str(configured))


def _options(payload: dict[str, Any]) -> dict[str, Any]:
    options = payload.get("provider_options", {})
    return options if isinstance(options, dict) else {}


def _prompt(payload: dict[str, Any]) -> str:
    capability = payload.get("capability", {}) if isinstance(payload.get("capability"), dict) else {}
    capability_name = str(capability.get("name", ""))
    contract = "planner JSON" if capability_name == "planner" else "capability output JSON"
    return "\n".join(
        [
            "You are running an Attestflow capability as a programming agent provider.",
            f"Capability: {capability_name}",
            f"Return only {contract}.",
            "Return only JSON. Do not include markdown fences or explanatory prose.",
            "Do not edit runtime task JSON directly; Attestflow records evidence.",
            "",
            "Capability input JSON:",
            json.dumps(payload, ensure_ascii=False, indent=2),
        ]
    )


def _extract_contract_json(text: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    capability = payload.get("capability", {}) if isinstance(payload.get("capability"), dict) else {}
    capability_name = str(capability.get("name", ""))
    for item in _json_candidates(text):
        found = _find_contract_json(item, capability_name)
        if found is not None:
            return found
    return None


def _json_candidates(text: str) -> list[Any]:
    stripped = text.strip()
    if not stripped:
        return []
    candidates: list[Any] = []
    for raw in [stripped, *stripped.splitlines()]:
        try:
            candidates.append(json.loads(raw))
        except json.JSONDecodeError:
            embedded = _embedded_json(raw)
            if embedded is not None:
                candidates.append(embedded)
    return candidates


def _embedded_json(text: str) -> Any | None:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def _find_contract_json(value: Any, capability_name: str) -> dict[str, Any] | None:
    if isinstance(value, dict):
        if _is_contract(value, capability_name):
            return value
        for item in value.values():
            found = _find_contract_json(item, capability_name)
            if found is not None:
                return found
    if isinstance(value, list):
        for item in value:
            found = _find_contract_json(item, capability_name)
            if found is not None:
                return found
    if isinstance(value, str):
        for item in _json_candidates(value):
            found = _find_contract_json(item, capability_name)
            if found is not None:
                return found
    return None


def _is_contract(value: dict[str, Any], capability_name: str) -> bool:
    if value.get("schema_version") != 1:
        return False
    if capability_name == "planner":
        return isinstance(value.get("tasks"), list)
    return value.get("status") in {"passed", "failed", "blocked"}


def _command_exists(command: str) -> bool:
    return bool(shutil.which(command) or Path(command).exists())


def _env_provider(provider: str) -> str:
    return provider.upper().replace("-", "_")


if __name__ == "__main__":
    raise SystemExit(main())
