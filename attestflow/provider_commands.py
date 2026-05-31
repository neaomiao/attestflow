from __future__ import annotations

from pathlib import Path
import json
import os
import signal
import shlex
import shutil
import subprocess
from typing import Any

from .io import dump_data
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
    dump_data(payload, run_path / "input.json")
    argv = provider_command_argv(command)
    process = subprocess.Popen(
        argv,
        cwd=root,
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
