from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from .capabilities import get_capability, list_capabilities, run_planner_capability, run_task_capability
from .config import load_config, validate_config
from .io import dump_data, load_data
from .planner import import_planner_tasks
from .resume import resume_summary
from .runner import run_verification
from .secrets import secret_scan
from .sessions import list_session_providers, resume_agent_session
from .tasks import (
    TASK_STATES,
    block_task,
    close_task,
    iter_tasks,
    select_next_task,
    start_task,
    transition_task,
    unblock_task,
    validate_task,
    verify_task,
)


ROOT = Path.cwd()


PROVIDER_DOCTOR_DEFAULTS: dict[str, dict[str, object]] = {
    "codex": {"args": ["doctor", "--json"], "failure_patterns": []},
    "claude-code": {"args": ["auth", "status"], "failure_patterns": []},
    "opencode": {"args": ["providers", "list"], "failure_patterns": ["0 credentials"]},
}

PROVIDER_DOCTOR_TIMEOUT_SECONDS = 20


def cmd_init(args: argparse.Namespace) -> int:
    package_source = Path(__file__).resolve().parent / "templates" / "base"
    source_source = Path(__file__).resolve().parents[1] / "templates" / "base"
    source = package_source if package_source.exists() else source_source
    target = Path(args.path).resolve()
    if not source.exists():
        print("ERROR: templates/base does not exist", file=sys.stderr)
        return 1
    agent_provider = getattr(args, "agent_provider", "command") or "command"
    agent_command = getattr(args, "agent_command", None)
    provider_commands = _builtin_session_provider_commands()
    if agent_provider != "command" and agent_provider not in provider_commands:
        print(f"ERROR: unknown agent provider: {agent_provider}", file=sys.stderr)
        return 1
    shutil.copytree(source, target, dirs_exist_ok=True)
    _configure_initialized_agent_provider(target, agent_provider, agent_command)
    for state in TASK_STATES:
        (target / "harness" / "tasks" / state).mkdir(parents=True, exist_ok=True)
    (target / "harness" / "runs").mkdir(parents=True, exist_ok=True)
    (target / "harness" / "capability-runs").mkdir(parents=True, exist_ok=True)
    (target / "harness" / "locks").mkdir(parents=True, exist_ok=True)
    print(f"initialized attestflow harness in {target}")
    return 0


def cmd_doctor(_: argparse.Namespace) -> int:
    config = load_config(ROOT)
    errors = _doctor_errors(ROOT, config)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("doctor passed")
    return 0


def cmd_validate_config(_: argparse.Namespace) -> int:
    errors = validate_config(load_config(ROOT))
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("config validation passed")
    return 0


def _configure_initialized_agent_provider(target: Path, agent_provider: str, agent_command: str | None) -> None:
    config_path = target / "harness.yml"
    config = load_data(config_path)
    sessions = config.get("sessions", {})
    if isinstance(sessions, dict):
        sessions["agent_provider"] = agent_provider
        sessions["launch_command"] = None
        sessions["resume_command"] = None
        provider_options = sessions.get("provider_options", {})
        provider_options = provider_options if isinstance(provider_options, dict) else {}
        if agent_command:
            provider_options["command"] = str(agent_command)
        elif agent_provider == "command":
            provider_options = {}
        sessions["provider_options"] = provider_options
        config["sessions"] = sessions

    capabilities = config.get("capabilities", {})
    if isinstance(capabilities, dict):
        for capability in capabilities.values():
            if isinstance(capability, dict):
                capability["agent_provider"] = agent_provider
    dump_data(config, config_path)


def _doctor_errors(root: Path, config: dict) -> list[str]:
    errors = validate_config(config)
    errors.extend(_doctor_provider_errors(root, config))
    if (root / "harness.yml").exists():
        errors.extend(_doctor_runtime_layout_errors(root, config))
    return errors


def _doctor_provider_errors(root: Path, config: dict) -> list[str]:
    sessions = config.get("sessions", {})
    if not isinstance(sessions, dict):
        return []
    agent_provider = str(sessions.get("agent_provider", "command"))
    provider_commands = _builtin_session_provider_commands()
    if agent_provider not in provider_commands:
        return []
    provider_options = sessions.get("provider_options", {})
    command = None
    if isinstance(provider_options, dict):
        command = provider_options.get("command")
    command = str(command or provider_commands[agent_provider])
    if not _command_exists(command):
        return [f"session provider command not found for {agent_provider}: {command}"]
    preflight_error = _doctor_provider_preflight_error(root, agent_provider, command, provider_options)
    if preflight_error:
        return [preflight_error]
    return []


def _doctor_provider_preflight_error(
    root: Path,
    agent_provider: str,
    command: str,
    provider_options: object,
) -> str | None:
    options = provider_options if isinstance(provider_options, dict) else {}
    if options.get("doctor_enabled") is False:
        return None
    args = _doctor_provider_args(agent_provider, options)
    if args is None:
        return None
    display = " ".join(shlex.quote(item) for item in [command, *args])
    try:
        completed = subprocess.run(
            [command, *args],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
            timeout=_doctor_provider_timeout(options),
        )
    except subprocess.TimeoutExpired as exc:
        return f"session provider preflight timed out for {agent_provider}: {display}{_doctor_output_suffix(exc.stdout, exc.stderr)}"
    except OSError as exc:
        return f"session provider preflight could not run for {agent_provider}: {display}: {exc}"
    if completed.returncode != 0:
        return (
            f"session provider preflight failed for {agent_provider}: {display} exited with "
            f"{completed.returncode}{_doctor_output_suffix(completed.stdout, completed.stderr)}"
        )
    output = _doctor_combined_output(completed.stdout, completed.stderr)
    for pattern in _doctor_failure_patterns(agent_provider, options):
        if pattern and pattern.lower() in output.lower():
            return (
                f"session provider preflight output indicates {agent_provider} is not ready: "
                f"matched {pattern!r}{_doctor_output_suffix(completed.stdout, completed.stderr)}"
            )
    return None


def _doctor_provider_args(agent_provider: str, options: dict) -> list[str] | None:
    env_name = f"ATTESTFLOW_{_provider_env_name(agent_provider)}_DOCTOR_ARGS"
    if os.environ.get(env_name):
        return shlex.split(os.environ[env_name])
    configured = options.get("doctor_args")
    if configured is None:
        configured = PROVIDER_DOCTOR_DEFAULTS.get(agent_provider, {}).get("args")
    if configured is None:
        return None
    if isinstance(configured, str):
        args = shlex.split(configured)
        return args or None
    if isinstance(configured, list):
        args = [str(item) for item in configured]
        return args or None
    return [str(configured)]


def _doctor_failure_patterns(agent_provider: str, options: dict) -> list[str]:
    configured = options.get("doctor_failure_patterns")
    if configured is None:
        configured = PROVIDER_DOCTOR_DEFAULTS.get(agent_provider, {}).get("failure_patterns", [])
    if isinstance(configured, str):
        return [configured]
    if isinstance(configured, list):
        return [str(item) for item in configured]
    return []


def _doctor_provider_timeout(options: dict) -> int:
    configured = options.get("doctor_timeout_seconds", PROVIDER_DOCTOR_TIMEOUT_SECONDS)
    return configured if type(configured) is int and configured > 0 else PROVIDER_DOCTOR_TIMEOUT_SECONDS


def _doctor_output_suffix(stdout: object, stderr: object) -> str:
    excerpt = _doctor_output_excerpt(_doctor_combined_output(stdout, stderr))
    return f": {excerpt}" if excerpt else ""


def _doctor_combined_output(stdout: object, stderr: object) -> str:
    parts = [part for part in (_doctor_text(stdout), _doctor_text(stderr)) if part.strip()]
    return "\n".join(parts)


def _doctor_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _doctor_output_excerpt(text: str) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) > 500:
        return cleaned[:497] + "..."
    return cleaned


def _provider_env_name(agent_provider: str) -> str:
    return agent_provider.upper().replace("-", "_")


def _doctor_runtime_layout_errors(root: Path, config: dict) -> list[str]:
    errors: list[str] = []
    paths = config.get("paths", {}) if isinstance(config.get("paths"), dict) else {}
    task_root = root / str(paths.get("tasks", "harness/tasks"))
    for state in TASK_STATES:
        if not (task_root / state).is_dir():
            errors.append(f"missing task state directory: {task_root / state}")
    for key, default in (("runs", "harness/runs"), ("locks", "harness/locks"), ("capability_runs", "harness/capability-runs")):
        path = root / str(paths.get(key, default))
        if not path.is_dir():
            errors.append(f"missing {key} directory: {path}")
    if task_root.exists():
        for path in sorted(task_root.glob("*/*.json")):
            try:
                task = load_data(path)
            except ValueError as exc:
                errors.append(str(exc))
                continue
            for error in validate_task(task, directory_state=path.parent.name):
                errors.append(f"{path}: {error}")
    return errors


def _builtin_session_provider_commands() -> dict[str, str]:
    return {provider["name"]: provider["command"] for provider in list_session_providers()}


def _command_exists(command: str) -> bool:
    return bool(shutil.which(command) or Path(command).exists())


def cmd_validate_task(args: argparse.Namespace) -> int:
    path = Path(args.path)
    task = load_data(path)
    directory_state = path.parent.name if path.parent.name else None
    errors = validate_task(task, directory_state=directory_state)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("task validation passed")
    return 0


def cmd_tasks(_: argparse.Namespace) -> int:
    config = load_config(ROOT)
    records = iter_tasks(ROOT, config)
    if not records:
        print("no task files found")
        return 0
    for record in records:
        task = record.task
        print(f"{task.get('id')}\t{task.get('state')}\t{task.get('priority')}\t{task.get('title')}")
    return 0


def cmd_next(_: argparse.Namespace) -> int:
    config = load_config(ROOT)
    selected = select_next_task(ROOT, config)
    if not selected:
        print("no ready tasks")
        return 0
    print(f"{selected.task.get('id')}\t{selected.path}")
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    config = load_config(ROOT)
    run = start_task(ROOT, config, args.task, actor_role=args.actor)
    print(f"started {args.task}: {run.run_id}")
    return 0


def cmd_dispatch(args: argparse.Namespace) -> int:
    config = load_config(ROOT)
    run = start_task(ROOT, config, args.task, actor_role=args.actor)
    session = load_data(run.path / "session.yml")
    if session.get("status") not in {"prepared", "launched"}:
        print(f"ERROR: session launch for {args.task} ended with {session.get('status')}", file=sys.stderr)
        return 1
    print(f"dispatched {args.task}: {run.run_id} -> {session.get('session_id')}")
    return 0


def cmd_block(args: argparse.Namespace) -> int:
    block_task(
        ROOT,
        load_config(ROOT),
        args.task,
        reason=args.reason,
        unblock_condition=args.unblock_condition,
        owner=args.owner,
        blocker_type=args.type,
        source="cli",
    )
    print(f"blocked {args.task}: {args.reason}")
    return 0


def cmd_unblock(args: argparse.Namespace) -> int:
    record = unblock_task(ROOT, load_config(ROOT), args.task, args.blocker, resolution=args.resolution)
    print(f"unblocked {args.task}: {args.blocker} -> {record.task['state']}")
    return 0


def cmd_transition(args: argparse.Namespace) -> int:
    transition_task(ROOT, load_config(ROOT), args.task, args.state)
    print(f"moved {args.task} to {args.state}")
    return 0


def cmd_close(args: argparse.Namespace) -> int:
    close_task(ROOT, load_config(ROOT), args.task)
    print(f"closed {args.task}")
    return 0


def cmd_evidence(args: argparse.Namespace) -> int:
    config = load_config(ROOT)
    for record in iter_tasks(ROOT, config):
        if record.task.get("id") != args.task:
            continue
        evidence = record.task.get("evidence", {})
        packet = evidence.get("packet") if isinstance(evidence, dict) else None
        if not packet:
            print(f"ERROR: {args.task} has no evidence.packet", file=sys.stderr)
            return 1
        path = ROOT / str(packet)
        if not path.exists():
            print(f"ERROR: evidence packet does not exist: {path}", file=sys.stderr)
            return 1
        print(path.read_text(encoding="utf-8"))
        return 0
    print(f"ERROR: task not found: {args.task}", file=sys.stderr)
    return 1


def cmd_resume(_: argparse.Namespace) -> int:
    print(resume_summary(ROOT, load_config(ROOT)))
    return 0


def cmd_session_resume(args: argparse.Namespace) -> int:
    config = load_config(ROOT)
    for record in iter_tasks(ROOT, config):
        if record.task.get("id") != args.task:
            continue
        evidence = record.task.get("evidence", {})
        run_id = evidence.get("run_id") if isinstance(evidence, dict) else None
        if not run_id:
            print(f"ERROR: {args.task} has no evidence.run_id", file=sys.stderr)
            return 1
        run_path = ROOT / config.get("paths", {}).get("runs", "harness/runs") / str(run_id)
        try:
            resumed = resume_agent_session(ROOT, config, run_path)
        except (FileNotFoundError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        if resumed.status != "resumed":
            print(f"ERROR: session resume for {args.task} ended with {resumed.status}", file=sys.stderr)
            return 1
        print(f"resumed {args.task}: {resumed.session_id} -> {resumed.status}")
        return 0
    print(f"ERROR: task not found: {args.task}", file=sys.stderr)
    return 1


def cmd_provider_list(_: argparse.Namespace) -> int:
    for provider in list_session_providers():
        print(f"{provider['name']}\t{provider['command']}\t{provider['description']}")
    return 0


def cmd_secret_scan(_: argparse.Namespace) -> int:
    findings = secret_scan(ROOT)
    if findings:
        for finding in findings:
            print(f"ERROR: {finding}", file=sys.stderr)
        return 1
    print("secret scan passed")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    config = load_config(ROOT)
    if args.task:
        result = verify_task(ROOT, config, args.task)
    else:
        result = run_verification(
            ROOT,
            config,
            ROOT / "harness" / "runs" / "adhoc-verify" / "commands",
        )
    if result.failed:
        print("verification failed: " + ", ".join(result.failed), file=sys.stderr)
        return 1
    print("verification passed")
    return 0


def cmd_task_import(args: argparse.Namespace) -> int:
    if args.from_json == "-":
        plan = json.load(sys.stdin)
    else:
        with Path(args.from_json).open(encoding="utf-8") as handle:
            plan = json.load(handle)
    records = import_planner_tasks(ROOT, load_config(ROOT), plan)
    task_ids = ", ".join(str(record.task["id"]) for record in records)
    print(f"imported {len(records)} task(s): {task_ids}")
    return 0


def cmd_capability_list(_: argparse.Namespace) -> int:
    for capability in list_capabilities():
        print(f"{capability['name']}\t{capability['phase']}\t{capability['specialist']}")
    return 0


def cmd_capability_show(args: argparse.Namespace) -> int:
    try:
        capability = get_capability(args.name)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(capability, ensure_ascii=False, indent=2))
    return 0


def cmd_capability_run(args: argparse.Namespace) -> int:
    try:
        result = run_task_capability(ROOT, load_config(ROOT), args.name, args.task, command=args.command)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"ran {result.capability} for {result.task_id}: {result.run_path}")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    goal = " ".join(args.goal).strip()
    try:
        result = run_planner_capability(ROOT, load_config(ROOT), goal, command=args.command)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    task_ids = ", ".join(str(record.task["id"]) for record in result.records)
    print(f"planned and imported {len(result.records)} task(s): {task_ids}")
    print(f"capability run: {result.run_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m attestflow")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init")
    init.add_argument("--path", default=".")
    init.add_argument("--adapter", default="generic")
    init.add_argument("--agent-provider", choices=["command", *sorted(_builtin_session_provider_commands())], default="command")
    init.add_argument("--agent-command")
    init.set_defaults(func=cmd_init)

    subparsers.add_parser("doctor").set_defaults(func=cmd_doctor)
    subparsers.add_parser("validate-config").set_defaults(func=cmd_validate_config)

    validate_task_parser = subparsers.add_parser("validate-task")
    validate_task_parser.add_argument("path")
    validate_task_parser.set_defaults(func=cmd_validate_task)

    subparsers.add_parser("tasks").set_defaults(func=cmd_tasks)
    subparsers.add_parser("next").set_defaults(func=cmd_next)

    start = subparsers.add_parser("start")
    start.add_argument("task")
    start.add_argument("--actor", default="orchestrator")
    start.set_defaults(func=cmd_start)

    dispatch = subparsers.add_parser("dispatch")
    dispatch.add_argument("task")
    dispatch.add_argument("--actor", default="orchestrator")
    dispatch.set_defaults(func=cmd_dispatch)

    block = subparsers.add_parser("block")
    block.add_argument("task")
    block.add_argument("--reason", required=True)
    block.add_argument("--unblock-condition")
    block.add_argument("--owner", default="user")
    block.add_argument("--type", default="external_input")
    block.set_defaults(func=cmd_block)

    unblock = subparsers.add_parser("unblock")
    unblock.add_argument("task")
    unblock.add_argument("--blocker", required=True)
    unblock.add_argument("--resolution", required=True)
    unblock.set_defaults(func=cmd_unblock)

    transition = subparsers.add_parser("transition")
    transition.add_argument("task")
    transition.add_argument("state")
    transition.set_defaults(func=cmd_transition)

    close = subparsers.add_parser("close")
    close.add_argument("task")
    close.set_defaults(func=cmd_close)

    evidence = subparsers.add_parser("evidence")
    evidence.add_argument("task")
    evidence.set_defaults(func=cmd_evidence)

    subparsers.add_parser("resume").set_defaults(func=cmd_resume)
    session = subparsers.add_parser("session")
    session_subparsers = session.add_subparsers(dest="session_command", required=True)
    session_resume = session_subparsers.add_parser("resume")
    session_resume.add_argument("task")
    session_resume.set_defaults(func=cmd_session_resume)

    provider = subparsers.add_parser("provider")
    provider_subparsers = provider.add_subparsers(dest="provider_command", required=True)
    provider_subparsers.add_parser("list").set_defaults(func=cmd_provider_list)

    subparsers.add_parser("secret-scan").set_defaults(func=cmd_secret_scan)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--task")
    verify.set_defaults(func=cmd_verify)

    task = subparsers.add_parser("task")
    task_subparsers = task.add_subparsers(dest="task_command", required=True)
    task_import = task_subparsers.add_parser("import")
    task_import.add_argument("--from-json", required=True)
    task_import.set_defaults(func=cmd_task_import)

    capability = subparsers.add_parser("capability")
    capability_subparsers = capability.add_subparsers(dest="capability_command", required=True)
    capability_subparsers.add_parser("list").set_defaults(func=cmd_capability_list)
    capability_show = capability_subparsers.add_parser("show")
    capability_show.add_argument("name")
    capability_show.set_defaults(func=cmd_capability_show)
    capability_run = capability_subparsers.add_parser("run")
    capability_run.add_argument("name")
    capability_run.add_argument("task")
    capability_run.add_argument("--command")
    capability_run.set_defaults(func=cmd_capability_run)

    plan = subparsers.add_parser("plan")
    plan.add_argument("goal", nargs="+")
    plan.add_argument("--command")
    plan.set_defaults(func=cmd_plan)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
