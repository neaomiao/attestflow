from __future__ import annotations

from pathlib import Path
import json
import os
import signal
import shlex
import shutil
import subprocess
from typing import Any

from .io import dump_data, load_data
from .provider_failures import classify_provider_failure, redact_text


def provider_timeout_seconds(provider_config: dict[str, Any]) -> float | None:
    options = provider_config.get("provider_options", {})
    configured = provider_config.get("timeout_seconds")
    if configured is None and isinstance(options, dict):
        configured = options.get("timeout_seconds")
    if type(configured) in {int, float} and configured > 0:
        return float(configured)
    return None


def provider_command_argv(command: str | list[str]) -> list[str]:
    if isinstance(command, list):
        return [str(item) for item in command if str(item)]
    return shlex.split(str(command))


def shell_command_exists(command: str | list[str]) -> bool:
    try:
        parts = provider_command_argv(command)
        executable = parts[0]
    except (ValueError, IndexError):
        return False
    resolved = shutil.which(executable) or (str(Path(executable)) if Path(executable).exists() else None)
    if not resolved:
        return False
    module = _python_module_from_command(executable, parts)
    if module:
        return _python_module_exists(resolved, module)
    return True


def _python_module_from_command(executable: str, parts: list[str]) -> str | None:
    if "python" not in Path(executable).name:
        return None
    try:
        module_flag_index = parts.index("-m")
    except ValueError:
        return None
    if module_flag_index + 1 >= len(parts):
        return None
    module = parts[module_flag_index + 1].strip()
    return module or None


def _python_module_exists(executable: str, module: str) -> bool:
    code = (
        "import importlib.util, sys; "
        "sys.exit(0 if importlib.util.find_spec(sys.argv[1]) is not None else 1)"
    )
    try:
        completed = subprocess.run(
            [executable, "-c", code, module],
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return False
    return completed.returncode == 0


def run_provider_json_command(
    root: Path,
    command: str | list[str],
    payload: dict[str, Any],
    run_path: Path,
    label: str,
    *,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    argv = provider_command_argv(command)
    policy = _provider_command_policy(payload)
    payload["security"] = _provider_security_payload(root, payload, policy)
    policy = payload["security"]["provider_commands"]
    dump_data(payload, run_path / "input.json")
    command_error = _command_allowlist_error(argv, policy)
    if command_error:
        _write_provider_logs(run_path, "", command_error)
        _write_provider_failure(run_path, label, "tool_denied", None, "", command_error, command_error)
        raise ValueError(f"{label} provider failure tool_denied: {command_error}")
    approval_error = _approval_error(payload)
    if approval_error:
        _write_provider_logs(run_path, "", approval_error)
        _write_provider_failure(run_path, label, "approval_required", None, "", approval_error, approval_error)
        raise ValueError(f"{label} provider failure approval_required: {approval_error}")
    process = subprocess.Popen(
        argv,
        cwd=root,
        env=_provider_process_env(policy),
        text=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(
            input=json.dumps(payload, ensure_ascii=False),
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        _terminate_process_group(process)
        stdout, stderr = _collect_after_timeout(process)
        stdout = stdout or _process_output_text(exc.stdout)
        stderr = stderr or _process_output_text(exc.stderr)
        message = f"{label} provider command timed out after {timeout_seconds:g} seconds"
        _write_provider_logs(run_path, stdout, _append_stderr_message(stderr, message))
        _write_provider_failure(run_path, label, "timeout", -1, stdout, stderr, message)
        raise ValueError(f"{label} provider failure timeout: {message}") from exc

    max_output_bytes = _max_output_bytes(policy)
    if max_output_bytes is not None and _output_size(stdout, stderr) > max_output_bytes:
        message = f"{label} provider output too large: {_output_size(stdout, stderr)} bytes exceeds {max_output_bytes} bytes"
        _write_provider_logs(run_path, _truncate_text(stdout, max_output_bytes), _append_stderr_message(_truncate_text(stderr, max_output_bytes), message))
        _write_provider_failure(run_path, label, "output_too_large", process.returncode, stdout, stderr, message)
        raise ValueError(f"{label} provider failure output_too_large: {message}")

    _write_provider_logs(run_path, stdout, stderr)
    if process.returncode != 0:
        message = f"{label} provider command failed with exit code {process.returncode}"
        failure = _write_provider_failure(run_path, label, None, process.returncode, stdout, stderr, message)
        raise ValueError(f"{label} provider failure {failure['type']}: {message}")
    try:
        output = json.loads(stdout or "")
    except json.JSONDecodeError as exc:
        message = f"{label} provider command did not return valid JSON: {exc}"
        _write_provider_failure(run_path, label, "invalid_output", process.returncode, stdout, stderr, message)
        raise ValueError(f"{label} provider failure invalid_output: {message}") from exc
    if not isinstance(output, dict):
        message = f"{label} provider command must return a JSON object"
        _write_provider_failure(run_path, label, "invalid_output", process.returncode, stdout, stderr, message)
        raise ValueError(f"{label} provider failure invalid_output: {message}")
    _write_provider_usage(run_path, output)
    return output


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        process.kill()


def _collect_after_timeout(process: subprocess.Popen[str]) -> tuple[str, str]:
    try:
        stdout, stderr = process.communicate(timeout=1)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()
        stdout, stderr = process.communicate()
    return _process_output_text(stdout), _process_output_text(stderr)


def _process_output_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _append_stderr_message(stderr: str, message: str) -> str:
    return (stderr.rstrip() + "\n" if stderr.strip() else "") + message + "\n"


def _write_provider_logs(run_path: Path, stdout: str, stderr: str) -> None:
    (run_path / "stdout.log").write_text(redact_text(stdout), encoding="utf-8")
    (run_path / "stderr.log").write_text(redact_text(stderr), encoding="utf-8")


def _write_provider_failure(
    run_path: Path,
    label: str,
    reason: str | None,
    returncode: int | None,
    stdout: str,
    stderr: str,
    error: str,
) -> dict[str, Any]:
    failure = classify_provider_failure(
        label,
        reason=reason,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        error=error,
    )
    dump_data(failure, run_path / "failure.json")
    return failure


def _write_provider_usage(run_path: Path, output: dict[str, Any]) -> None:
    usage = output.get("usage")
    if isinstance(usage, dict):
        dump_data(usage, run_path / "usage.json")


def _provider_command_policy(payload: dict[str, Any]) -> dict[str, Any]:
    security = payload.get("security", {})
    if not isinstance(security, dict):
        return {}
    provider_commands = security.get("provider_commands", {})
    return provider_commands if isinstance(provider_commands, dict) else {}


def _provider_security_payload(root: Path, payload: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    security = payload.get("security", {})
    normalized = dict(security) if isinstance(security, dict) else {}
    normalized_policy = dict(policy)
    normalized_policy["sandbox"] = _sandbox_policy(normalized_policy, security)
    normalized["provider_commands"] = normalized_policy
    normalized["approval"] = _approval_payload(root, payload, policy)
    return normalized


def _sandbox_policy(policy: dict[str, Any], security: Any) -> dict[str, Any]:
    sandbox = policy.get("sandbox", {})
    sandbox = sandbox if isinstance(sandbox, dict) else {}
    network = sandbox.get("network")
    if not network and isinstance(security, dict):
        network_config = security.get("network", {})
        if isinstance(network_config, dict):
            network = network_config.get("mode")
    mode = sandbox.get("mode")
    return {
        "mode": mode if mode in {"inherit-env", "restricted-env"} else "inherit-env",
        "allowed_env": _string_list(sandbox.get("allowed_env")),
        "blocked_env": _string_list(sandbox.get("blocked_env")),
        "blocked_env_prefixes": _string_list(sandbox.get("blocked_env_prefixes")),
        "network": network if network in {"provider-owned", "disabled"} else "provider-owned",
    }


def _provider_process_env(policy: dict[str, Any]) -> dict[str, str]:
    sandbox = _sandbox_policy(policy, {})
    if sandbox["mode"] == "restricted-env":
        env = _restricted_base_env()
        for key in sandbox["allowed_env"]:
            if key in os.environ:
                env[key] = os.environ[key]
    else:
        env = dict(os.environ)
    for key in sandbox["blocked_env"]:
        env.pop(key, None)
    for prefix in sandbox["blocked_env_prefixes"]:
        for key in list(env):
            if key.startswith(prefix):
                env.pop(key, None)
    if sandbox["network"] == "disabled":
        env["ATTESTFLOW_NETWORK"] = "disabled"
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            env.pop(key, None)
    return env


def _restricted_base_env() -> dict[str, str]:
    defaults = (
        "PATH",
        "HOME",
        "TMPDIR",
        "TEMP",
        "TMP",
        "LANG",
        "LC_ALL",
        "SYSTEMROOT",
        "COMSPEC",
        "PATHEXT",
    )
    return {key: os.environ[key] for key in defaults if key in os.environ}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _command_allowlist_error(argv: list[str], policy: dict[str, Any]) -> str | None:
    allowlist = policy.get("allowlist", [])
    if not allowlist:
        return None
    allowed = [str(item) for item in allowlist if str(item).strip()]
    if not allowed:
        return None
    executable = argv[0] if argv else ""
    executable_name = Path(executable).name
    for item in allowed:
        if executable == item or executable_name == item:
            return None
        if Path(item).is_absolute() and Path(executable).expanduser().resolve() == Path(item).expanduser().resolve():
            return None
    return f"provider command not allowed by security.provider_commands.allowlist: {executable}"


def _approval_payload(root: Path, payload: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    options = payload.get("provider_options", {})
    options = options if isinstance(options, dict) else {}
    required = bool(options.get("irreversible") or options.get("irreversible_action"))
    if not required or policy.get("require_approval_for_irreversible", True) is False:
        return {"required": required, "approved": True, "path": None, "id": options.get("approval_id")}
    approval_path = _approval_path(root, options)
    approved = False
    if approval_path and approval_path.exists():
        try:
            approval = load_data(approval_path)
        except (OSError, ValueError):
            approval = {}
        approved = isinstance(approval, dict) and (approval.get("approved") is True or approval.get("status") == "approved")
    return {
        "required": True,
        "approved": approved,
        "path": str(approval_path.relative_to(root)) if approval_path and approval_path.exists() else (str(approval_path) if approval_path else None),
        "id": options.get("approval_id"),
    }


def _approval_path(root: Path, options: dict[str, Any]) -> Path | None:
    configured = options.get("approval_path")
    if configured:
        path = Path(str(configured))
        return path if path.is_absolute() else root / path
    approval_id = options.get("approval_id")
    if approval_id:
        return root / "harness" / "approvals" / f"{approval_id}.json"
    return None


def _approval_error(payload: dict[str, Any]) -> str | None:
    security = payload.get("security", {})
    approval = security.get("approval", {}) if isinstance(security, dict) else {}
    if not isinstance(approval, dict):
        return None
    if approval.get("required") is True and approval.get("approved") is not True:
        approval_id = approval.get("id") or "<missing>"
        return f"approval required for irreversible provider action: {approval_id}"
    return None


def _max_output_bytes(policy: dict[str, Any]) -> int | None:
    value = policy.get("max_output_bytes")
    return int(value) if type(value) is int and value > 0 else None


def _output_size(stdout: str, stderr: str) -> int:
    return len(stdout.encode("utf-8")) + len(stderr.encode("utf-8"))


def _truncate_text(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    truncated = encoded[:max(0, max_bytes)].decode("utf-8", errors="ignore")
    return truncated + "\n<truncated>\n"
