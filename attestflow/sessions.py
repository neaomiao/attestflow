from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
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
    session_config = config.get("sessions", {})
    provider = str(session_config.get("provider", "command"))
    role = str(session_config.get("role", task.get("agents", {}).get("owner", "worker_agent")))
    session_id = f"session-{run.run_id}"
    prompt_path = run.path / "prompt.md"
    session_path = run.path / "session.yml"
    launch_log = run.path / "session-launch.log"
    created_at = datetime.now(timezone.utc).isoformat()

    prompt_path.write_text(_prompt_packet(root, config, task, run, session_id, role), encoding="utf-8")
    session = {
        "schema_version": 1,
        "session_id": session_id,
        "task_id": str(task["id"]),
        "run_id": run.run_id,
        "provider": provider,
        "role": role,
        "status": "prepared",
        "created_at": created_at,
        "launched_at": None,
        "prompt_packet": "prompt.md",
        "launch_command": None,
        "launch_exit_code": None,
        "launch_log": None,
        "resume_command": None,
    }

    launch_command_template = session_config.get("launch_command")
    if launch_command_template:
        command = _render_launch_command(str(launch_command_template), root, run, session_id)
        result = _run_launch_command(command, root, launch_log)
        session["status"] = "launched" if result.returncode == 0 else "launch_failed"
        session["launched_at"] = datetime.now(timezone.utc).isoformat()
        session["launch_command"] = command
        session["launch_exit_code"] = result.returncode
        session["launch_log"] = "session-launch.log"
        session["resume_command"] = _render_resume_command(session_config.get("resume_command"), root, run, session_id)

    dump_data(session, session_path)
    _record_session_metadata(run.path, session)
    _append_session_events(run.path, task, run, session)
    return AgentSessionRecord(
        session_id=session_id,
        path=session_path,
        prompt_path=prompt_path,
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


def _render_launch_command(command: str, root: Path, run: RunRecord, session_id: str) -> str:
    values = {
        "root": str(root),
        "run_id": run.run_id,
        "run_path": str(run.path),
        "session_id": session_id,
        "prompt_packet": str(run.path / "prompt.md"),
        "session_log": str(run.path / "session-launch.log"),
    }
    return command.format(**values)


def _render_resume_command(command: Any, root: Path, run: RunRecord, session_id: str) -> str | None:
    if not command:
        return None
    return _render_launch_command(str(command), root, run, session_id)


def _run_launch_command(command: str, root: Path, launch_log: Path) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=root,
        shell=True,
        text=True,
        capture_output=True,
        check=False,
    )
    launch_log.write_text((completed.stdout or "") + (completed.stderr or ""), encoding="utf-8")
    return completed


def _record_session_metadata(run_path: Path, session: dict[str, Any]) -> None:
    metadata_path = run_path / "metadata.yml"
    metadata = load_data(metadata_path)
    metadata["agent_session"] = {
        "session_id": session["session_id"],
        "provider": session["provider"],
        "role": session["role"],
        "status": session["status"],
        "prompt_packet": session["prompt_packet"],
        "session_record": "session.yml",
    }
    dump_data(metadata, metadata_path)


def _append_session_events(run_path: Path, task: dict[str, Any], run: RunRecord, session: dict[str, Any]) -> None:
    actor_role = str(task.get("agents", {}).get("owner", "orchestrator"))
    data = {
        "session_id": session["session_id"],
        "provider": session["provider"],
        "role": session["role"],
        "status": session["status"],
        "prompt_packet": session["prompt_packet"],
    }
    append_ledger(run_path, "session_created", str(task["id"]), run.run_id, actor_role, data)
    if session["launch_command"]:
        append_ledger(
            run_path,
            "session_launched",
            str(task["id"]),
            run.run_id,
            actor_role,
            {**data, "exit_code": session["launch_exit_code"], "launch_log": session["launch_log"]},
        )
