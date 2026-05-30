from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
from typing import Any

from .evidence import RunRecord, append_ledger
from .io import dump_data, load_data


@dataclass(frozen=True)
class AgentSessionRecord:
    session_id: str
    path: Path
    prompt_path: Path
    status: str


def create_agent_session(root: Path, config: dict[str, Any], task: dict[str, Any], run: RunRecord) -> AgentSessionRecord:
    session_config = _session_config(config)
    agent_provider = str(session_config.get("agent_provider", "command"))
    role = str(session_config.get("role", task.get("agents", {}).get("owner", "worker_agent")))
    session_id = f"session-{run.run_id}"
    prompt_path = run.path / "prompt.md"
    session_path = run.path / "session.yml"
    created_at = datetime.now(timezone.utc).isoformat()

    prompt_path.write_text(_prompt_packet(root, config, task, run, session_id, role), encoding="utf-8")
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
        "prompt_packet": "prompt.md",
        "adapter_input": None,
        "adapter_output": None,
        "launch_adapter_input": None,
        "launch_adapter_output": None,
        "launch_command": None,
        "launch_exit_code": None,
        "launch_stdout_log": None,
        "launch_stderr_log": None,
        "resume_command": _render_session_command(session_config.get("resume_command"), root, run, session_id),
        "resume_adapter_input": None,
        "resume_adapter_output": None,
        "resume_exit_code": None,
        "resume_stdout_log": None,
        "resume_stderr_log": None,
        "failure": None,
    }

    launch_command_template = session_config.get("launch_command")
    if launch_command_template:
        _apply_adapter_result(
            root,
            config,
            task,
            run,
            session,
            action="launch",
            command_template=str(launch_command_template),
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


def resume_agent_session(root: Path, config: dict[str, Any], run_path: Path) -> AgentSessionRecord:
    session_path = run_path / "session.yml"
    session = load_data(session_path)
    task_id = str(session.get("task_id"))
    task = _load_task(root, config, task_id)
    run = RunRecord(run_id=str(session["run_id"]), path=run_path)
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
) -> None:
    command = _render_session_command(command_template, root, run, str(session["session_id"]))
    if command is None:
        return
    started_at = datetime.now(timezone.utc).isoformat()
    payload = _adapter_input(root, config, task, run, session, action)
    result = _run_adapter_command(root, run.path, action, command, payload)
    ended_at = datetime.now(timezone.utc).isoformat()
    session[f"{action}_command"] = command
    session[f"{action}_exit_code"] = result["exit_code"]
    session[f"{action}_stdout_log"] = result["stdout_log"]
    session[f"{action}_stderr_log"] = result["stderr_log"]
    session["adapter_input"] = result["input"]
    session[f"{action}_adapter_input"] = result["input"]
    session["updated_at"] = ended_at
    if result["output_path"]:
        session["adapter_output"] = result["output_path"]
        session[f"{action}_adapter_output"] = result["output_path"]

    output = result["output"]
    if result["failure"]:
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
) -> dict[str, Any]:
    prompt_ref = str(session.get("prompt_packet", "prompt.md"))
    prompt_path = run.path / prompt_ref
    return {
        "schema_version": 1,
        "action": action,
        "agent_provider": session.get("agent_provider"),
        "root": str(root),
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


def _run_adapter_command(
    root: Path,
    run_path: Path,
    action: str,
    command: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    prefix = "session-adapter" if action == "launch" else "session-resume-adapter"
    input_path = run_path / f"{prefix}-input.json"
    output_path = run_path / f"{prefix}-output.json"
    stdout_path = run_path / f"session-{action}.stdout.log"
    stderr_path = run_path / f"session-{action}.stderr.log"
    dump_data(payload, input_path)
    completed = subprocess.run(
        command,
        cwd=root,
        shell=True,
        text=True,
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        check=False,
    )
    stdout_path.write_text(completed.stdout or "", encoding="utf-8")
    stderr_path.write_text(completed.stderr or "", encoding="utf-8")
    result: dict[str, Any] = {
        "exit_code": completed.returncode,
        "input": input_path.name,
        "output_path": None,
        "stdout_log": stdout_path.name,
        "stderr_log": stderr_path.name,
        "output": None,
        "failure": None,
    }
    if completed.returncode != 0:
        result["failure"] = f"adapter command failed with exit code {completed.returncode}"
        return result
    try:
        output = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        result["failure"] = f"adapter command did not return valid JSON: {exc}"
        return result
    if not isinstance(output, dict):
        result["failure"] = "adapter command must return a JSON object"
        return result
    errors = _validate_adapter_output(output, action)
    if errors:
        result["failure"] = "; ".join(errors)
        return result
    dump_data(output, output_path)
    result["output_path"] = output_path.name
    result["output"] = output
    return result


def _validate_adapter_output(output: dict[str, Any], action: str) -> list[str]:
    errors: list[str] = []
    if output.get("schema_version") != 1:
        errors.append("adapter output schema_version must be 1")
    allowed = {"launch": {"launched", "blocked"}, "resume": {"resumed", "blocked"}}.get(action, set())
    if output.get("status") not in allowed:
        errors.append(f"adapter output status must be one of: {', '.join(sorted(allowed))}")
    if not str(output.get("summary", "")).strip():
        errors.append("adapter output summary must be non-empty")
    return errors


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
        event = "session_launched" if session["status"] == "launched" else "session_launch_failed"
        append_ledger(
            run_path,
            event,
            str(task["id"]),
            run.run_id,
            actor_role,
            {
                **data,
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
