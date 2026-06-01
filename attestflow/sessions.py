from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
from typing import Any

from .contracts import contract_validation_hint, validate_session_output
from .evidence import RunRecord, append_ledger
from .io import dump_data, load_data
from .provider_commands import provider_command_argv, provider_timeout_seconds
from .provider_failures import classify_provider_failure, redact_text
from .write_scope import build_write_scope_report, capture_write_scope_snapshot, write_scope_failure_message


BUILTIN_SESSION_PROVIDERS: dict[str, dict[str, str]] = {
    "codex": {"command": "codex", "description": "OpenAI Codex CLI via attestflow.agent_adapters."},
    "claude-code": {"command": "claude", "description": "Anthropic Claude Code CLI via attestflow.agent_adapters."},
    "opencode": {"command": "opencode", "description": "OpenCode CLI via attestflow.agent_adapters."},
}


@dataclass(frozen=True)
class AgentSessionRecord:
    session_id: str
    path: Path
    prompt_path: Path
    status: str


def list_session_providers() -> list[dict[str, str]]:
    return [
        {"name": name, "command": item["command"], "description": item["description"]}
        for name, item in sorted(BUILTIN_SESSION_PROVIDERS.items())
    ]


def create_agent_session(
    root: Path,
    config: dict[str, Any],
    task: dict[str, Any],
    run: RunRecord,
    *,
    workspace_root: Path | None = None,
    launch: bool = True,
    cancel_path: Path | None = None,
) -> AgentSessionRecord:
    workspace_root = workspace_root or root
    session_config = _session_config(config)
    agent_provider = str(session_config.get("agent_provider", "command"))
    role = str(session_config.get("role", task.get("agents", {}).get("owner", "worker_agent")))
    session_id = f"session-{run.run_id}"
    prompt_path = run.path / "prompt.md"
    session_path = run.path / "session.yml"
    created_at = datetime.now(timezone.utc).isoformat()

    prompt_path.write_text(_prompt_packet(workspace_root, config, task, run, session_id, role), encoding="utf-8")
    resume_command_template = session_config.get("resume_command") or _builtin_adapter_command(agent_provider)
    session = {
        "schema_version": 1,
        "session_id": session_id,
        "task_id": str(task["id"]),
        "run_id": run.run_id,
        "agent_provider": agent_provider,
        "role": role,
        "status": "prepared",
        "created_at": created_at,
        "updated_at": created_at,
        "launched_at": None,
        "resumed_at": None,
        "external_session_id": None,
        "workspace_root": str(workspace_root),
        "prompt_packet": "prompt.md",
        "adapter_input": None,
        "adapter_output": None,
        "launch_adapter_input": None,
        "launch_adapter_output": None,
        "launch_command": None,
        "launch_exit_code": None,
        "launch_stdout_log": None,
        "launch_stderr_log": None,
        "resume_command": _render_session_command(resume_command_template, root, run, session_id),
        "resume_adapter_input": None,
        "resume_adapter_output": None,
        "resume_exit_code": None,
        "resume_stdout_log": None,
        "resume_stderr_log": None,
        "launch_write_scope": None,
        "resume_write_scope": None,
        "failure": None,
    }

    launch_command_template = session_config.get("launch_command") or _builtin_adapter_command(agent_provider)
    if launch and launch_command_template:
        _apply_adapter_result(
            root,
            config,
            task,
            run,
            session,
            action="launch",
            command_template=str(launch_command_template),
            workspace_root=workspace_root,
            cancel_path=cancel_path,
        )

    dump_data(session, session_path)
    _record_session_metadata(run.path, session)
    _append_session_events(run.path, task, run, session)
    return AgentSessionRecord(
        session_id=session_id,
        path=session_path,
        prompt_path=prompt_path,
        status=str(session["status"]),
    )


def launch_agent_session(
    root: Path,
    config: dict[str, Any],
    run_path: Path,
    *,
    cancel_path: Path | None = None,
) -> AgentSessionRecord:
    session_path = run_path / "session.yml"
    session = load_data(session_path)
    task_id = str(session.get("task_id"))
    task = _load_task(root, config, task_id)
    run = RunRecord(run_id=str(session["run_id"]), path=run_path)
    workspace_root = Path(str(session.get("workspace_root") or root))
    command_template = _session_config(config).get("launch_command") or _builtin_adapter_command(str(session.get("agent_provider", "")))
    if not command_template:
        return AgentSessionRecord(
            session_id=str(session["session_id"]),
            path=session_path,
            prompt_path=run_path / str(session.get("prompt_packet", "prompt.md")),
            status=str(session["status"]),
        )
    _apply_adapter_result(
        root,
        config,
        task,
        run,
        session,
        action="launch",
        command_template=str(command_template),
        workspace_root=workspace_root,
        cancel_path=cancel_path,
    )
    dump_data(session, session_path)
    _record_session_metadata(run_path, session)
    _append_session_launch_event(run_path, task, run, session)
    return AgentSessionRecord(
        session_id=str(session["session_id"]),
        path=session_path,
        prompt_path=run_path / str(session.get("prompt_packet", "prompt.md")),
        status=str(session["status"]),
    )


def resume_agent_session(root: Path, config: dict[str, Any], run_path: Path) -> AgentSessionRecord:
    session_path = run_path / "session.yml"
    session = load_data(session_path)
    task_id = str(session.get("task_id"))
    task = _load_task(root, config, task_id)
    run = RunRecord(run_id=str(session["run_id"]), path=run_path)
    workspace_root = Path(str(session.get("workspace_root") or root))
    command_template = _session_config(config).get("resume_command") or session.get("resume_command")
    if not command_template:
        raise ValueError("sessions.resume_command or session.resume_command must be configured")

    _apply_adapter_result(
        root,
        config,
        task,
        run,
        session,
        action="resume",
        command_template=str(command_template),
        workspace_root=workspace_root,
    )
    dump_data(session, session_path)
    _record_session_metadata(run_path, session)
    _append_session_resume_event(run_path, task, run, session)
    return AgentSessionRecord(
        session_id=str(session["session_id"]),
        path=session_path,
        prompt_path=run_path / str(session.get("prompt_packet", "prompt.md")),
        status=str(session["status"]),
    )


def _prompt_packet(root: Path, config: dict[str, Any], task: dict[str, Any], run: RunRecord, session_id: str, role: str) -> str:
    commands = config.get("commands", {})
    files = task.get("files", {}) if isinstance(task.get("files"), dict) else {}
    requirements = task.get("requirements", {}) if isinstance(task.get("requirements"), dict) else {}
    lines = [
        "# Attestflow Agent Session Packet",
        "",
        "## Session",
        "",
        f"- Session ID: {session_id}",
        f"- Run ID: {run.run_id}",
        f"- Task ID: {task.get('id')}",
        f"- Role: {role}",
        f"- Workspace: {root}",
        "",
        "## Task",
        "",
        f"- Title: {task.get('title')}",
        f"- Purpose: {task.get('purpose')}",
        f"- Scope: {task.get('scope', [])}",
        f"- Out of Scope: {task.get('out_of_scope', [])}",
        f"- Confirmed Requirements: {requirements.get('confirmed', [])}",
        f"- Unresolved Requirements: {requirements.get('unresolved', [])}",
        "",
        "## Ownership",
        "",
        f"- Read Files: {files.get('read', [])}",
        f"- Write Files: {files.get('write', [])}",
        "- Do not edit files outside the write scope unless the orchestrator changes the task.",
        "",
        "## Required Development Order",
        "",
        f"- BDD Scenarios: {task.get('bdd_scenarios', [])}",
        f"- Unit Tests: {task.get('unit_tests', [])}",
        f"- Acceptance: {task.get('acceptance', [])}",
        "",
        "## Verification Commands",
        "",
        f"- BDD: {commands.get('bdd')}",
        f"- Unit: {commands.get('unit')}",
        f"- Lint: {commands.get('lint')}",
        f"- Typecheck: {commands.get('typecheck')}",
        f"- Secret Scan: {commands.get('secret_scan')}",
        f"- Project Verify: {commands.get('project_verify')}",
        "",
        "## Completion Contract",
        "",
        f"- Record evidence under: {run.path}",
        f"- Before close, run: python -m attestflow verify --task {task.get('id')}",
        "- The task cannot move to done without current run evidence.",
        "",
    ]
    return "\n".join(lines)


def _session_config(config: dict[str, Any]) -> dict[str, Any]:
    sessions = config.get("sessions", {})
    return sessions if isinstance(sessions, dict) else {}


def _builtin_adapter_command(agent_provider: str) -> str | None:
    if agent_provider not in BUILTIN_SESSION_PROVIDERS:
        return None
    adapter_path = Path(__file__).resolve().parent / "agent_adapters.py"
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(adapter_path))}"


def _render_session_command(command: Any, root: Path, run: RunRecord, session_id: str) -> str | None:
    if not command:
        return None
    values = {
        "root": str(root),
        "run_id": run.run_id,
        "run_path": str(run.path),
        "session_id": session_id,
        "prompt_packet": str(run.path / "prompt.md"),
        "session_log": str(run.path / "session-launch.stdout.log"),
    }
    return str(command).format(**values)


def _apply_adapter_result(
    root: Path,
    config: dict[str, Any],
    task: dict[str, Any],
    run: RunRecord,
    session: dict[str, Any],
    *,
    action: str,
    command_template: str,
    workspace_root: Path,
    cancel_path: Path | None = None,
) -> None:
    command = _render_session_command(command_template, root, run, str(session["session_id"]))
    if command is None:
        return
    started_at = datetime.now(timezone.utc).isoformat()
    payload = _adapter_input(root, config, task, run, session, action, workspace_root)
    before_snapshot = capture_write_scope_snapshot(workspace_root, config)
    result = _run_adapter_command(workspace_root, run.path, action, command, payload, cancel_path=cancel_path)
    after_snapshot = capture_write_scope_snapshot(workspace_root, config)
    ended_at = datetime.now(timezone.utc).isoformat()
    write_scope_report = build_write_scope_report(
        workspace_root,
        config,
        task,
        before_snapshot,
        after_snapshot,
        action=action,
    )
    write_scope_path = run.path / f"session-{action}-write-scope.json"
    dump_data(write_scope_report, write_scope_path)
    session[f"{action}_write_scope"] = write_scope_path.name
    scope_failure = write_scope_failure_message(write_scope_report)
    if scope_failure:
        failure = _write_adapter_failure(
            run.path,
            action,
            "tool_denied",
            result["exit_code"],
            "",
            "",
            scope_failure,
        )
        existing_failure = str(result["failure"]) if result["failure"] else ""
        result["failure"] = f"{existing_failure}; {failure['type']}: {scope_failure}" if existing_failure else f"{failure['type']}: {scope_failure}"
    session[f"{action}_command"] = command
    session[f"{action}_exit_code"] = result["exit_code"]
    session[f"{action}_stdout_log"] = result["stdout_log"]
    session[f"{action}_stderr_log"] = result["stderr_log"]
    if result.get("usage_path"):
        session[f"{action}_usage"] = result["usage_path"]
    session["adapter_input"] = result["input"]
    session[f"{action}_adapter_input"] = result["input"]
    session["updated_at"] = ended_at
    if result["output_path"]:
        session["adapter_output"] = result["output_path"]
        session[f"{action}_adapter_output"] = result["output_path"]

    output = result["output"]
    if result.get("cancelled"):
        session["status"] = f"{action}_cancelled"
        session["failure"] = result["failure"]
    elif result["failure"]:
        session["status"] = f"{action}_failed"
        session["failure"] = result["failure"]
    elif isinstance(output, dict):
        status = str(output["status"])
        session["status"] = status
        session["failure"] = None
        session["external_session_id"] = output.get("external_session_id") or session.get("external_session_id")
        if output.get("resume_command"):
            session["resume_command"] = str(output["resume_command"])
        session["summary"] = str(output.get("summary", ""))
    if action == "launch":
        session["launched_at"] = ended_at
    if action == "resume":
        session["resumed_at"] = ended_at
    session[f"{action}_started_at"] = started_at
    session[f"{action}_ended_at"] = ended_at


def _adapter_input(
    root: Path,
    config: dict[str, Any],
    task: dict[str, Any],
    run: RunRecord,
    session: dict[str, Any],
    action: str,
    workspace_root: Path,
) -> dict[str, Any]:
    prompt_ref = str(session.get("prompt_packet", "prompt.md"))
    prompt_path = run.path / prompt_ref
    workspace = _run_workspace(run.path)
    return {
        "schema_version": 1,
        "action": action,
        "agent_provider": session.get("agent_provider"),
        "root": str(workspace_root),
        "control_root": str(root),
        "workspace": workspace,
        "session": {
            "session_id": session.get("session_id"),
            "task_id": session.get("task_id"),
            "run_id": session.get("run_id"),
            "role": session.get("role"),
            "status": session.get("status"),
            "external_session_id": session.get("external_session_id"),
        },
        "run": {"run_id": run.run_id, "path": str(run.path)},
        "task": task,
        "provider_options": _provider_options(config),
        "prompt_packet": {
            "path": prompt_ref,
            "absolute_path": str(prompt_path),
            "content": prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else "",
        },
        "commands": config.get("commands", {}),
        "instructions": [
            "Launch or resume one independent programming agent session for this task only.",
            "Return only JSON that follows docs/contracts/session-adapter-schema.md.",
            "Do not edit runtime task JSON directly; Attestflow records session evidence.",
        ],
    }


def _run_workspace(run_path: Path) -> dict[str, Any]:
    metadata_path = run_path / "metadata.yml"
    if not metadata_path.exists():
        return {}
    metadata = load_data(metadata_path)
    workspace = metadata.get("workspace", {})
    return workspace if isinstance(workspace, dict) else {}


def _provider_options(config: dict[str, Any]) -> dict[str, Any]:
    options = _session_config(config).get("provider_options", {})
    return options if isinstance(options, dict) else {}


def _run_adapter_command(
    root: Path,
    run_path: Path,
    action: str,
    command: str,
    payload: dict[str, Any],
    *,
    cancel_path: Path | None = None,
) -> dict[str, Any]:
    prefix = "session-adapter" if action == "launch" else "session-resume-adapter"
    input_path = run_path / f"{prefix}-input.json"
    output_path = run_path / f"{prefix}-output.json"
    stdout_path = run_path / f"session-{action}.stdout.log"
    stderr_path = run_path / f"session-{action}.stderr.log"
    dump_data(payload, input_path)
    result: dict[str, Any] = {
        "exit_code": None,
        "input": input_path.name,
        "output_path": None,
	        "stdout_log": stdout_path.name,
	        "stderr_log": stderr_path.name,
	        "usage_path": None,
	        "output": None,
        "failure": None,
        "cancelled": False,
    }
    process = subprocess.Popen(
        provider_command_argv(command),
        cwd=root,
        text=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    timeout_seconds = provider_timeout_seconds({"provider_options": payload.get("provider_options", {})})
    payload_text = json.dumps(payload, ensure_ascii=False)
    try:
        stdout, stderr = _communicate_with_cancel(
            process,
            payload_text,
            timeout_seconds=timeout_seconds,
            cancel_path=cancel_path,
        )
    except _AdapterCancelled as exc:
        _terminate_process_group(process)
        stdout, stderr = _collect_after_timeout(process)
        stdout = stdout or _adapter_output_text(exc.stdout)
        stderr = stderr or _adapter_output_text(exc.stderr)
        message = "adapter command cancelled"
        stdout_path.write_text(redact_text(stdout), encoding="utf-8")
        stderr_path.write_text(redact_text(_append_stderr_message(stderr, message)), encoding="utf-8")
        _write_adapter_failure(run_path, action, "tool_denied", -2, stdout, stderr, message)
        result["exit_code"] = -2
        result["failure"] = message
        result["cancelled"] = True
        return result
    except subprocess.TimeoutExpired as exc:
        _terminate_process_group(process)
        stdout, stderr = _collect_after_timeout(process)
        stdout = stdout or _adapter_output_text(exc.stdout)
        stderr = stderr or _adapter_output_text(exc.stderr)
        message = f"adapter command timed out after {timeout_seconds:g} seconds"
        stdout_path.write_text(redact_text(stdout), encoding="utf-8")
        stderr_path.write_text(redact_text(_append_stderr_message(stderr, message)), encoding="utf-8")
        _write_adapter_failure(run_path, action, "timeout", -1, stdout, stderr, message)
        result["exit_code"] = -1
        result["failure"] = message
        return result
    stdout_path.write_text(redact_text(stdout), encoding="utf-8")
    stderr_path.write_text(redact_text(stderr), encoding="utf-8")
    result["exit_code"] = process.returncode
    if process.returncode != 0:
        message = f"adapter command failed with exit code {process.returncode}"
        failure = _write_adapter_failure(run_path, action, None, process.returncode, stdout, stderr, message)
        result["failure"] = f"{failure['type']}: {message}"
        return result
    try:
        output = json.loads(stdout or "")
    except json.JSONDecodeError as exc:
        message = f"adapter command did not return valid JSON: {exc}"
        _write_adapter_failure(run_path, action, "invalid_output", process.returncode, stdout, stderr, message)
        result["failure"] = message
        return result
    if not isinstance(output, dict):
        message = "adapter command must return a JSON object"
        _write_adapter_failure(run_path, action, "invalid_output", process.returncode, stdout, stderr, message)
        result["failure"] = message
        return result
    dump_data(output, output_path)
    errors = _validate_adapter_output(output, action)
    if errors:
        contract_type = "session-launch-output" if action == "launch" else "session-resume-output"
        result["output_path"] = output_path.name
        message = "; ".join(errors) + "\n" + contract_validation_hint(contract_type, output_path)
        _write_adapter_failure(run_path, action, "invalid_output", process.returncode, stdout, stderr, message)
        result["failure"] = message
        return result
    usage_path = _write_adapter_usage(run_path, action, output)
    if usage_path:
        result["usage_path"] = usage_path.name
    result["output_path"] = output_path.name
    result["output"] = output
    return result


def _write_adapter_usage(run_path: Path, action: str, output: dict[str, Any]) -> Path | None:
    usage = output.get("usage")
    if not isinstance(usage, dict):
        return None
    path = run_path / f"session-{action}-usage.json"
    dump_data(usage, path)
    return path


class _AdapterCancelled(Exception):
    def __init__(self, stdout: str | bytes | None = None, stderr: str | bytes | None = None) -> None:
        super().__init__("adapter command cancelled")
        self.stdout = stdout
        self.stderr = stderr


def _communicate_with_cancel(
    process: subprocess.Popen[str],
    payload_text: str,
    *,
    timeout_seconds: float | None,
    cancel_path: Path | None,
) -> tuple[str, str]:
    if cancel_path is None:
        return process.communicate(input=payload_text, timeout=timeout_seconds)
    interval = 0.1
    deadline = None if timeout_seconds is None else datetime.now(timezone.utc).timestamp() + timeout_seconds
    input_text: str | None = payload_text
    while True:
        if cancel_path.exists():
            raise _AdapterCancelled()
        timeout = interval
        if deadline is not None:
            remaining = deadline - datetime.now(timezone.utc).timestamp()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(process.args, timeout_seconds)
            timeout = min(interval, remaining)
        try:
            return process.communicate(input=input_text, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            input_text = None
            if cancel_path.exists():
                raise _AdapterCancelled(stdout=exc.stdout, stderr=exc.stderr) from exc
            if deadline is not None and datetime.now(timezone.utc).timestamp() >= deadline:
                raise


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
    return _adapter_output_text(stdout), _adapter_output_text(stderr)


def _adapter_output_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _append_stderr_message(stderr: str, message: str) -> str:
    return (stderr.rstrip() + "\n" if stderr.strip() else "") + message + "\n"


def _write_adapter_failure(
    run_path: Path,
    action: str,
    reason: str | None,
    returncode: int | None,
    stdout: str,
    stderr: str,
    error: str,
) -> dict[str, Any]:
    failure = classify_provider_failure(
        f"session-{action}",
        reason=reason,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        error=error,
    )
    dump_data(failure, run_path / f"session-{action}-failure.json")
    return failure


def _validate_adapter_output(output: dict[str, Any], action: str) -> list[str]:
    return validate_session_output(output, action, label="adapter output")


def _record_session_metadata(run_path: Path, session: dict[str, Any]) -> None:
    metadata_path = run_path / "metadata.yml"
    metadata = load_data(metadata_path)
    metadata["agent_session"] = {
        "session_id": session["session_id"],
        "agent_provider": session["agent_provider"],
        "role": session["role"],
        "status": session["status"],
        "external_session_id": session.get("external_session_id"),
        "prompt_packet": session["prompt_packet"],
        "session_record": "session.yml",
    }
    dump_data(metadata, metadata_path)


def _append_session_events(run_path: Path, task: dict[str, Any], run: RunRecord, session: dict[str, Any]) -> None:
    actor_role = str(task.get("agents", {}).get("owner", "orchestrator"))
    data = {
        "session_id": session["session_id"],
        "agent_provider": session["agent_provider"],
        "role": session["role"],
        "status": session["status"],
        "external_session_id": session.get("external_session_id"),
        "prompt_packet": session["prompt_packet"],
    }
    append_ledger(run_path, "session_created", str(task["id"]), run.run_id, actor_role, data)
    if session["launch_command"]:
        _append_session_launch_event(run_path, task, run, session)


def _append_session_launch_event(run_path: Path, task: dict[str, Any], run: RunRecord, session: dict[str, Any]) -> None:
    actor_role = str(task.get("agents", {}).get("owner", "orchestrator"))
    event = "session_launched" if session["status"] == "launched" else "session_launch_failed"
    append_ledger(
        run_path,
        event,
        str(task["id"]),
        run.run_id,
        actor_role,
        {
            "session_id": session["session_id"],
            "agent_provider": session["agent_provider"],
            "role": session["role"],
            "status": session["status"],
            "external_session_id": session.get("external_session_id"),
            "prompt_packet": session["prompt_packet"],
            "exit_code": session["launch_exit_code"],
            "stdout_log": session["launch_stdout_log"],
            "stderr_log": session["launch_stderr_log"],
            "adapter_input": session["adapter_input"],
            "adapter_output": session["launch_adapter_output"],
            "failure": session.get("failure"),
        },
    )


def _append_session_resume_event(run_path: Path, task: dict[str, Any], run: RunRecord, session: dict[str, Any]) -> None:
    actor_role = str(task.get("agents", {}).get("owner", "orchestrator"))
    event = "session_resumed" if session["status"] == "resumed" else "session_resume_failed"
    append_ledger(
        run_path,
        event,
        str(task["id"]),
        run.run_id,
        actor_role,
        {
            "session_id": session["session_id"],
            "agent_provider": session["agent_provider"],
            "role": session["role"],
            "status": session["status"],
            "external_session_id": session.get("external_session_id"),
            "exit_code": session["resume_exit_code"],
            "stdout_log": session["resume_stdout_log"],
            "stderr_log": session["resume_stderr_log"],
            "adapter_input": session["resume_adapter_input"],
            "adapter_output": session["resume_adapter_output"],
            "failure": session.get("failure"),
        },
    )


def _load_task(root: Path, config: dict[str, Any], task_id: str) -> dict[str, Any]:
    task_root = root / config.get("paths", {}).get("tasks", "harness/tasks")
    for path in sorted(task_root.glob(f"*/{task_id}.json")):
        return load_data(path)
    return {"id": task_id}
