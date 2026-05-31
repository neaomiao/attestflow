from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path

from .autonomy import autonomy_doctor
from .capabilities import get_capability, list_capabilities, run_planner_capability, run_task_capability
from .ci import BUILTIN_CI_PROVIDERS, list_ci_providers, run_ci_status
from .config import load_config, validate_config
from .contracts import CONTRACT_TYPES, validate_contract_file
from .io import dump_data, load_data
from .evidence_export import export_task_evidence
from .orchestrator import AutopilotRunResult, ExecutionPlan, build_execution_plan, run_autopilot
from .planner import import_planner_tasks
from .pr import run_pr_ensure, run_pr_status
from .provider_commands import shell_command_exists as _shared_shell_command_exists
from .provider_contracts import run_provider_contract_suite
from .release import run_release_status
from .resume import resume_summary
from .runner import run_verification
from .secrets import secret_scan
from .sessions import list_session_providers, resume_agent_session
from .tasks import (
    TASK_STATES,
    block_task,
    close_task,
    iter_tasks,
    record_task_evidence_reference,
    select_dispatchable_tasks,
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
    templates_root = _templates_root()
    source = templates_root / "base"
    adapter = getattr(args, "adapter", "generic") or "generic"
    adapter_source = templates_root / "adapters" / str(adapter)
    target = Path(args.path).resolve()
    if not source.exists():
        print("ERROR: templates/base does not exist", file=sys.stderr)
        return 1
    if not adapter_source.exists():
        print(f"ERROR: unknown adapter: {adapter}", file=sys.stderr)
        return 1
    agent_provider = getattr(args, "agent_provider", "command") or "command"
    agent_command = getattr(args, "agent_command", None)
    provider_commands = _builtin_session_provider_commands()
    if agent_provider != "command" and agent_provider not in provider_commands:
        print(f"ERROR: unknown agent provider: {agent_provider}", file=sys.stderr)
        return 1
    shutil.copytree(source, target, dirs_exist_ok=True)
    shutil.copytree(adapter_source, target / "harness" / "adapters" / str(adapter), dirs_exist_ok=True)
    _configure_initialized_adapter(target, str(adapter))
    _configure_initialized_agent_provider(target, agent_provider, agent_command)
    for state in TASK_STATES:
        (target / "harness" / "tasks" / state).mkdir(parents=True, exist_ok=True)
    (target / "harness" / "runs").mkdir(parents=True, exist_ok=True)
    (target / "harness" / "capability-runs").mkdir(parents=True, exist_ok=True)
    (target / "harness" / "autopilot-runs").mkdir(parents=True, exist_ok=True)
    (target / "harness" / "ci-runs").mkdir(parents=True, exist_ok=True)
    (target / "harness" / "pr-runs").mkdir(parents=True, exist_ok=True)
    (target / "harness" / "release-runs").mkdir(parents=True, exist_ok=True)
    (target / "harness" / "locks").mkdir(parents=True, exist_ok=True)
    print(f"initialized attestflow harness in {target}")
    return 0


def _templates_root() -> Path:
    package_templates = Path(__file__).resolve().parent / "templates"
    source_templates = Path(__file__).resolve().parents[1] / "templates"
    return package_templates if (package_templates / "base").exists() else source_templates


def cmd_doctor(_: argparse.Namespace) -> int:
    config = load_config(ROOT)
    errors = _doctor_errors(ROOT, config)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("doctor passed")
    return 0


def cmd_autonomy_doctor(args: argparse.Namespace) -> int:
    payload = autonomy_doctor(ROOT, load_config(ROOT))
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"autonomy doctor: {payload['status']}")
        for check in payload["checks"]:
            print(f"{check['name']}\t{check['status']}\t{check['summary']}")
    return 1 if payload["status"] == "blocked" else 0


def cmd_validate_config(_: argparse.Namespace) -> int:
    errors = validate_config(load_config(ROOT))
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("config validation passed")
    return 0


def cmd_contract_validate(args: argparse.Namespace) -> int:
    path = Path(args.file)
    try:
        errors = validate_contract_file(args.type, path)
    except (OSError, ValueError) as exc:
        print(f"ERROR: failed to load contract file {path}: {exc}", file=sys.stderr)
        return 1
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"contract {args.type} valid: {path}")
    return 0


def _configure_initialized_adapter(target: Path, adapter: str) -> None:
    config_path = target / "harness.yml"
    config = load_data(config_path)
    project = config.get("project", {})
    project = project if isinstance(project, dict) else {}
    project["adapter"] = adapter
    if adapter == "python":
        _configure_python_adapter_defaults(target, config, project)
    if adapter == "node":
        _configure_node_adapter_defaults(target, config, project)
    if adapter == "go":
        _configure_go_adapter_defaults(target, config, project)
    if adapter == "rust":
        _configure_rust_adapter_defaults(target, config, project)
    config["project"] = project
    dump_data(config, config_path)


def _configure_python_adapter_defaults(target: Path, config: dict, project: dict) -> None:
    pyproject = _load_pyproject(target)
    tool = pyproject.get("tool", {}) if isinstance(pyproject, dict) else {}
    tool = tool if isinstance(tool, dict) else {}
    commands = config.get("commands", {})
    commands = commands if isinstance(commands, dict) else {}

    if _has_pytest_config(target, tool):
        project["test_runner"] = "pytest"
        commands["unit"] = "python -m pytest"
    if isinstance(tool.get("ruff"), dict):
        project["linter"] = "ruff"
        commands["lint"] = "python -m ruff check ."
    if isinstance(tool.get("mypy"), dict):
        project["typechecker"] = "mypy"
        commands["typecheck"] = "python -m mypy ."

    config["commands"] = commands


def _load_pyproject(target: Path) -> dict:
    pyproject = target / "pyproject.toml"
    if not pyproject.exists():
        return {}
    try:
        with pyproject.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _has_pytest_config(target: Path, tool: dict) -> bool:
    pytest_config = tool.get("pytest")
    if isinstance(pytest_config, dict) and isinstance(pytest_config.get("ini_options"), dict):
        return True
    return (target / "pytest.ini").exists()


def _configure_node_adapter_defaults(target: Path, config: dict, project: dict) -> None:
    package_json = _load_node_package_json(target)
    package_manager = _detect_node_package_manager(target)
    project["package_manager"] = package_manager
    scripts = package_json.get("scripts", {}) if isinstance(package_json, dict) else {}
    scripts = scripts if isinstance(scripts, dict) else {}
    commands = config.get("commands", {})
    commands = commands if isinstance(commands, dict) else {}
    if "test" in scripts:
        commands["unit"] = f"{package_manager} test"
    if "lint" in scripts:
        commands["lint"] = f"{package_manager} run lint"
    if "typecheck" in scripts:
        commands["typecheck"] = f"{package_manager} run typecheck"
    if "build" in scripts:
        commands["project_verify"] = f"{package_manager} run build"
    config["commands"] = commands


def _load_node_package_json(target: Path) -> dict:
    package_json = target / "package.json"
    if not package_json.exists():
        return {}
    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _detect_node_package_manager(target: Path) -> str:
    if (target / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (target / "yarn.lock").exists():
        return "yarn"
    return "npm"


def _configure_go_adapter_defaults(target: Path, config: dict, project: dict) -> None:
    project["module"] = "go"
    commands = config.get("commands", {})
    commands = commands if isinstance(commands, dict) else {}
    if (target / "go.mod").exists():
        commands["unit"] = "go test ./..."
        commands["project_verify"] = "go test ./..."
    config["commands"] = commands


def _configure_rust_adapter_defaults(target: Path, config: dict, project: dict) -> None:
    project["module"] = "rust"
    commands = config.get("commands", {})
    commands = commands if isinstance(commands, dict) else {}
    if (target / "Cargo.toml").exists():
        commands["unit"] = "cargo test"
        commands["typecheck"] = "cargo check --all-targets --all-features"
        commands["project_verify"] = "cargo build"
    config["commands"] = commands


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
    errors.extend(_doctor_project_command_errors(config))
    errors.extend(_doctor_provider_errors(root, config))
    errors.extend(_doctor_ci_provider_errors(config))
    errors.extend(_doctor_command_provider_errors(config, "pr_provider", "PR"))
    errors.extend(_doctor_command_provider_errors(config, "release_provider", "Release"))
    errors.extend(_doctor_worktree_errors(root, config))
    if (root / "harness.yml").exists():
        errors.extend(_doctor_runtime_layout_errors(root, config))
    return errors


def _doctor_project_command_errors(config: dict) -> list[str]:
    commands = config.get("commands", {})
    if not isinstance(commands, dict):
        return []
    errors: list[str] = []
    for name, command in sorted(commands.items()):
        if command is None:
            continue
        if not isinstance(command, str):
            continue
        if not _shell_command_exists(command):
            errors.append(f"project command not found for {name}: {command}")
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
    for key, default in (
        ("runs", "harness/runs"),
        ("locks", "harness/locks"),
        ("capability_runs", "harness/capability-runs"),
        ("autopilot_runs", "harness/autopilot-runs"),
        ("ci_runs", "harness/ci-runs"),
        ("pr_runs", "harness/pr-runs"),
        ("release_runs", "harness/release-runs"),
    ):
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


def _doctor_ci_provider_errors(config: dict) -> list[str]:
    ci_provider = _ci_provider_config(config)
    if not ci_provider:
        return []
    provider = str(ci_provider.get("provider", "command"))
    command = ci_provider.get("command")
    if not command and provider in BUILTIN_CI_PROVIDERS:
        provider_options = ci_provider.get("provider_options", {})
        if isinstance(provider_options, dict):
            command = provider_options.get("command")
        command = command or BUILTIN_CI_PROVIDERS[provider]["command"]
    if not command:
        return [f"CI provider command must be configured for {provider}"]
    if not _shell_command_exists(str(command)):
        return [f"CI provider command not found for {provider}: {command}"]
    return []


def _ci_provider_config(config: dict) -> dict:
    integrations = config.get("integrations", {})
    ci_provider = integrations.get("ci_provider", {}) if isinstance(integrations, dict) else {}
    return ci_provider if isinstance(ci_provider, dict) else {}


def _doctor_command_provider_errors(config: dict, key: str, label: str) -> list[str]:
    integrations = config.get("integrations", {})
    provider_config = integrations.get(key, {}) if isinstance(integrations, dict) else {}
    if not isinstance(provider_config, dict) or not provider_config:
        return []
    provider = str(provider_config.get("provider", "command"))
    command = provider_config.get("command")
    if not command:
        return [f"{label} provider command must be configured for {provider}"]
    if not _shell_command_exists(str(command)):
        return [f"{label} provider command not found for {provider}: {command}"]
    return []


def _doctor_worktree_errors(root: Path, config: dict) -> list[str]:
    sessions = config.get("sessions", {})
    worktree = sessions.get("worktree", {}) if isinstance(sessions, dict) else {}
    if not isinstance(worktree, dict) or worktree.get("enabled") is not True:
        return []
    if not _shell_command_exists("git"):
        return ["worktree is enabled but git command was not found"]
    completed = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return ["worktree is enabled but project root is not a git repository"]
    return []


def _shell_command_exists(command: str) -> bool:
    return _shared_shell_command_exists(command)


def cmd_validate_task(args: argparse.Namespace) -> int:
    path = Path(args.path)
    try:
        task = load_data(path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    directory_state = path.parent.name if path.parent.name else None
    errors = validate_task(task, directory_state=directory_state)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("task validation passed")
    return 0


def _load_task_records_for_cli(config: dict) -> list | None:
    try:
        return iter_tasks(ROOT, config)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"ERROR: failed to load tasks: {exc}", file=sys.stderr)
        return None


def cmd_tasks(_: argparse.Namespace) -> int:
    config = load_config(ROOT)
    records = _load_task_records_for_cli(config)
    if records is None:
        return 1
    if not records:
        print("no task files found")
        return 0
    for record in records:
        task = record.task
        print(f"{task.get('id')}\t{task.get('state')}\t{task.get('priority')}\t{task.get('title')}")
    return 0


def cmd_next(_: argparse.Namespace) -> int:
    config = load_config(ROOT)
    try:
        selected = select_next_task(ROOT, config)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"ERROR: failed to load tasks: {exc}", file=sys.stderr)
        return 1
    if not selected:
        print("no ready tasks")
        return 0
    print(f"{selected.task.get('id')}\t{selected.path}")
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    config = load_config(ROOT)
    try:
        run = start_task(ROOT, config, args.task, actor_role=args.actor)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"started {args.task}: {run.run_id}")
    return 0


def cmd_dispatch(args: argparse.Namespace) -> int:
    config = load_config(ROOT)
    if not args.task:
        if args.limit < 1:
            print("ERROR: --limit must be at least 1", file=sys.stderr)
            return 1
        try:
            records = select_dispatchable_tasks(ROOT, config, limit=args.limit)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            print(f"ERROR: failed to load tasks: {exc}", file=sys.stderr)
            return 1
        if not records:
            print("ERROR: no dispatchable tasks", file=sys.stderr)
            return 1
        dispatched: list[str] = []
        for record in records:
            task_id = str(record.task["id"])
            try:
                run = start_task(ROOT, config, task_id, actor_role=args.actor)
            except (FileNotFoundError, ValueError) as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                return 1
            session = load_data(run.path / "session.yml")
            if session.get("status") not in {"prepared", "launched"}:
                print(f"ERROR: session launch for {task_id} ended with {session.get('status')}", file=sys.stderr)
                return 1
            dispatched.append(task_id)
        print(f"dispatched {len(dispatched)} task(s): {', '.join(dispatched)}")
        return 0
    if args.limit != 1:
        print("ERROR: --limit can only be used without an explicit task", file=sys.stderr)
        return 1
    try:
        run = start_task(ROOT, config, args.task, actor_role=args.actor)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    session = load_data(run.path / "session.yml")
    if session.get("status") not in {"prepared", "launched"}:
        print(f"ERROR: session launch for {args.task} ended with {session.get('status')}", file=sys.stderr)
        return 1
    print(f"dispatched {args.task}: {run.run_id} -> {session.get('session_id')}")
    return 0


def cmd_autopilot(args: argparse.Namespace) -> int:
    selected_modes = [args.dry_run, args.run, args.resume, args.status]
    if sum(1 for selected in selected_modes if selected) > 1:
        print("ERROR: choose only one autopilot mode: --dry-run, --run, --resume, or --status", file=sys.stderr)
        return 1
    if not any(selected_modes):
        print("ERROR: use --dry-run, --run, --resume, or --status", file=sys.stderr)
        return 1
    if args.goal and not args.run:
        print("ERROR: --goal can only be used with --run", file=sys.stderr)
        return 1
    if args.loop and not (args.run or args.resume):
        print("ERROR: --loop can only be used with --run or --resume", file=sys.stderr)
        return 1
    if args.until and not (args.run or args.resume):
        print("ERROR: --until can only be used with --run or --resume", file=sys.stderr)
        return 1
    loop_cycles: int | None = None
    loop_stop_reason: str | None = None
    try:
        config = load_config(ROOT)
        limit = _autopilot_limit(config, args.limit) if args.dry_run or args.run or args.resume else None
        max_steps = _autopilot_max_steps(config, args.max_steps) if args.run or args.resume else None
        run_until_terminal = args.until == "terminal"
        should_loop = args.loop or run_until_terminal
        max_cycles = _autopilot_loop_max_cycles(config, args.max_cycles, until_terminal=run_until_terminal) if should_loop else None
        interval_seconds = _autopilot_loop_interval_seconds(config, args.interval_seconds) if should_loop else None
        if args.status:
            status = _latest_autopilot_status(ROOT, config)
        elif args.dry_run:
            plan = build_execution_plan(ROOT, config, limit=limit)
        else:
            resume_path = _latest_autopilot_run_path(ROOT, config) if args.resume else None
            if should_loop:
                result, loop_cycles, loop_stop_reason = _run_autopilot_loop(
                    ROOT,
                    config,
                    limit=limit,
                    max_steps=max_steps,
                    actor_role=args.actor,
                    resume_path=resume_path,
                    goal=args.goal,
                    max_cycles=max_cycles,
                    interval_seconds=interval_seconds,
                )
            else:
                result = run_autopilot(
                    ROOT,
                    config,
                    limit=limit,
                    max_steps=max_steps,
                    actor_role=args.actor,
                    resume_path=resume_path,
                    goal=args.goal,
                )
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.json:
        if args.status:
            payload = status
        elif args.dry_run:
            payload = _execution_plan_json(plan)
        else:
            payload = _autopilot_run_json(result)
            if loop_cycles is not None:
                payload["loop_cycles"] = loop_cycles
            if loop_stop_reason is not None:
                payload["loop_stop_reason"] = loop_stop_reason
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        if not args.status and not args.dry_run:
            return 1 if result.failed or result.blocked else 0
        return 0
    if args.status:
        _print_autopilot_status(status)
        return 0
    if args.dry_run:
        _print_execution_plan(plan)
        return 0
    _print_autopilot_run_result(result)
    if loop_cycles is not None:
        print(f"loop cycles={loop_cycles}")
    if loop_stop_reason is not None:
        print(f"loop_stop_reason={loop_stop_reason}")
    return 1 if result.failed or result.blocked else 0


def _run_autopilot_loop(
    root: Path,
    config: dict,
    *,
    limit: int | None,
    max_steps: int,
    actor_role: str,
    resume_path: Path | None,
    goal: str | None,
    max_cycles: int,
    interval_seconds: float,
) -> tuple[AutopilotRunResult, int, str]:
    cycles = 0
    current_resume_path = resume_path
    current_goal = goal
    result: AutopilotRunResult | None = None
    while cycles < max_cycles:
        result = run_autopilot(
            root,
            config,
            limit=limit,
            max_steps=max_steps,
            actor_role=actor_role,
            resume_path=current_resume_path,
            goal=current_goal,
        )
        cycles += 1
        if result.status != "paused":
            break
        current_resume_path = result.path
        current_goal = None
        if cycles < max_cycles and interval_seconds:
            time.sleep(interval_seconds)
    if result is None:  # pragma: no cover - max_cycles validation prevents this
        raise ValueError("--max-cycles must be at least 1")
    stop_reason = "max_cycles_reached" if result.status == "paused" and cycles >= max_cycles else "terminal_status"
    _record_autopilot_loop_result(result.path, cycles, stop_reason)
    return result, cycles, stop_reason


def _autopilot_limit(config: dict, override: int | None) -> int | None:
    if override is not None:
        if override < 1:
            raise ValueError("--limit must be at least 1")
        return override
    autopilot = config.get("autopilot", {})
    value = autopilot.get("default_limit", 1) if isinstance(autopilot, dict) else 1
    if value is None:
        return None
    if type(value) is not int or value < 1:
        raise ValueError("autopilot.default_limit must be a positive integer or null")
    return value


def _autopilot_max_steps(config: dict, override: int | None) -> int:
    if override is not None:
        if override < 1:
            raise ValueError("--max-steps must be at least 1")
        return override
    autopilot = config.get("autopilot", {})
    value = autopilot.get("max_steps", 1) if isinstance(autopilot, dict) else 1
    if type(value) is not int or value < 1:
        raise ValueError("autopilot.max_steps must be a positive integer")
    return value


def _autopilot_loop_max_cycles(config: dict, override: int | None, *, until_terminal: bool = False) -> int:
    if override is not None:
        if override < 1:
            raise ValueError("--max-cycles must be at least 1")
        return override
    autopilot = config.get("autopilot", {})
    default = 100 if until_terminal else 1
    value = autopilot.get("max_loop_cycles", default) if isinstance(autopilot, dict) else default
    if type(value) is not int or value < 1:
        raise ValueError("autopilot.max_loop_cycles must be a positive integer")
    return value


def _autopilot_loop_interval_seconds(config: dict, override: float | None) -> float:
    if override is not None:
        if override < 0:
            raise ValueError("--interval-seconds must be non-negative")
        return override
    autopilot = config.get("autopilot", {})
    value = autopilot.get("loop_interval_seconds", 0) if isinstance(autopilot, dict) else 0
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        raise ValueError("autopilot.loop_interval_seconds must be a non-negative number")
    return float(value)


def _record_autopilot_loop_result(run_path: Path, cycles: int, stop_reason: str) -> None:
    metadata_path = run_path / "metadata.json"
    metadata = load_data(metadata_path)
    previous = metadata.get("loop_cycles", 0)
    metadata["loop_cycles"] = (previous if isinstance(previous, int) else 0) + cycles
    metadata["loop_stop_reason"] = stop_reason
    dump_data(metadata, metadata_path)


def _print_execution_plan(plan: ExecutionPlan) -> None:
    limit = "all" if plan.limit is None else str(plan.limit)
    print(f"autopilot dry run (limit={limit})")
    if plan.actions:
        print("actions:")
        for action in plan.actions:
            detail = action.capability or action.target_state or "-"
            mode = "repair" if action.repair else "normal"
            print(f"  {action.task_id}\tstate={action.state}\taction={action.action}\ttarget={detail}\tmode={mode}")
    elif plan.batches:
        for batch in plan.batches:
            print(f"batch {batch.index}:")
            for task in batch.tasks:
                write_scope = ", ".join(task.write_files) if task.write_files else "-"
                print(f"  {task.task_id}\tpriority={task.priority}\twrite={write_scope}\t{task.title}")
    else:
        print("no executable batches")
    if plan.skipped:
        print("skipped:")
        for task in plan.skipped:
            print(f"  {task.task_id}\tstate={task.state}\treason={'; '.join(task.reasons)}")


def _execution_plan_json(plan: ExecutionPlan) -> dict:
    return {
        "limit": plan.limit,
        "completed": plan.completed,
        "actions": [
            {
                "id": action.task_id,
                "state": action.state,
                "action": action.action,
                "path": str(action.path),
                "capability": action.capability,
                "target_state": action.target_state,
                "repair": action.repair,
            }
            for action in plan.actions
        ],
        "batches": [
            {
                "index": batch.index,
                "tasks": [
                    {
                        "id": task.task_id,
                        "title": task.title,
                        "priority": task.priority,
                        "path": str(task.path),
                        "dependencies": task.dependencies,
                        "write_files": task.write_files,
                    }
                    for task in batch.tasks
                ],
            }
            for batch in plan.batches
        ],
        "skipped": [
            {
                "id": task.task_id,
                "state": task.state,
                "path": str(task.path),
                "reasons": task.reasons,
            }
            for task in plan.skipped
        ],
    }


def _print_autopilot_run_result(result: AutopilotRunResult) -> None:
    print(f"autopilot run: {result.path}")
    if result.pause_reason:
        print(f"status={result.status}: {result.pause_reason}")
    else:
        print(f"status={result.status}")
    if result.planned:
        print(f"planned {len(result.planned)} task(s): {', '.join(result.planned)}")
    if result.actions:
        print(f"executed {len(result.actions)} action(s): {', '.join(result.actions)}")
    if result.dispatched:
        print(f"dispatched {len(result.dispatched)} task(s): {', '.join(result.dispatched)}")
    else:
        print("dispatched 0 task(s)")
    if result.failed:
        print(f"failed {len(result.failed)} task(s): {', '.join(result.failed)}")
    if result.blocked:
        print(f"blocked {len(result.blocked)} task(s): {', '.join(result.blocked)}")
    next_command = _autopilot_next_command_from_result(result)
    if next_command:
        print(f"next: {next_command}")


def _autopilot_run_json(result: AutopilotRunResult) -> dict:
    return {
        "run_id": result.run_id,
        "path": str(result.path),
        "status": result.status,
        "pause_reason": result.pause_reason,
        "limit": result.limit,
        "steps": result.steps,
        "actions": result.actions,
        "planned": result.planned,
        "planner": result.planner,
        "dispatched": result.dispatched,
        "failed": result.failed,
        "blocked": result.blocked,
        "release": result.release,
        "release_status": result.release_status,
        "release_repair_planner": result.release_repair_planner,
        "releaser": result.releaser,
        "releaser_tasks": result.releaser_tasks or [],
        "skipped": [
            {
                "id": task.task_id,
                "state": task.state,
                "path": str(task.path),
                "reasons": task.reasons,
            }
            for task in result.skipped
        ],
    }


def _latest_autopilot_status(root: Path, config: dict) -> dict:
    metadata_path = _latest_autopilot_metadata_path(root, config)
    metadata = load_data(metadata_path)
    metadata["path"] = str(metadata_path.parent)
    if "run_id" not in metadata:
        metadata["run_id"] = metadata_path.parent.name
    return metadata


def _latest_autopilot_run_path(root: Path, config: dict) -> Path:
    return _latest_autopilot_metadata_path(root, config).parent


def _latest_autopilot_metadata_path(root: Path, config: dict) -> Path:
    run_root = root / config.get("paths", {}).get("autopilot_runs", "harness/autopilot-runs")
    metadata_paths = sorted(run_root.glob("*/metadata.json"))
    if not metadata_paths:
        raise FileNotFoundError("no autopilot runs found")
    return metadata_paths[-1]


def _print_autopilot_status(status: dict) -> None:
    pause_reason = status.get("pause_reason")
    print(
        "autopilot status: "
        f"{status.get('run_id')} "
        f"status={status.get('status')} "
        f"steps={status.get('steps', 0)}"
        f"{f' pause_reason={pause_reason}' if pause_reason else ''}"
    )
    actions = status.get("actions", [])
    planned = status.get("planned", [])
    dispatched = status.get("dispatched", [])
    failed = status.get("failed", [])
    blocked = status.get("blocked", [])
    release = status.get("release")
    release_status = status.get("release_status")
    release_repair_planner = status.get("release_repair_planner")
    releaser = status.get("releaser")
    releaser_tasks = status.get("releaser_tasks", [])
    if actions:
        print(f"actions={len(actions)}")
    if planned:
        print(f"planned={', '.join(str(item) for item in planned)}")
    if dispatched:
        print(f"dispatched={len(dispatched)}")
    if releaser:
        print(f"releaser={releaser}")
    if releaser_tasks:
        print(f"releaser_tasks={', '.join(str(item) for item in releaser_tasks)}")
    if release_status:
        print(f"release_status={release_status}")
    if release:
        print(f"release={release}")
    if release_repair_planner:
        print(f"release_repair_planner={release_repair_planner}")
    if failed:
        print(f"failed={', '.join(str(item) for item in failed)}")
    if blocked:
        print(f"blocked={', '.join(str(item) for item in blocked)}")
    next_command = _autopilot_next_command_from_status(status)
    if next_command:
        print(f"next: {next_command}")


def _autopilot_next_command_from_result(result: AutopilotRunResult) -> str | None:
    if result.blocked:
        return (
            'resolve blockers with python -m attestflow unblock TASK --blocker BLOCKER_ID --resolution "...", '
            "then run python -m attestflow autopilot --resume"
        )
    if result.failed:
        return "inspect failed task evidence, fix the failure, then run python -m attestflow autopilot --resume"
    if result.pause_reason or result.status == "paused":
        return "python -m attestflow autopilot --resume"
    return None


def _autopilot_next_command_from_status(status: dict) -> str | None:
    blocked = status.get("blocked", [])
    failed = status.get("failed", [])
    if blocked:
        return (
            'resolve blockers with python -m attestflow unblock TASK --blocker BLOCKER_ID --resolution "...", '
            "then run python -m attestflow autopilot --resume"
        )
    if failed:
        return "inspect failed task evidence, fix the failure, then run python -m attestflow autopilot --resume"
    if status.get("pause_reason") or status.get("status") == "paused":
        return "python -m attestflow autopilot --resume"
    return None


def cmd_block(args: argparse.Namespace) -> int:
    try:
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
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"blocked {args.task}: {args.reason}")
    return 0


def cmd_unblock(args: argparse.Namespace) -> int:
    try:
        record = unblock_task(ROOT, load_config(ROOT), args.task, args.blocker, resolution=args.resolution)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"unblocked {args.task}: {args.blocker} -> {record.task['state']}")
    return 0


def cmd_transition(args: argparse.Namespace) -> int:
    try:
        transition_task(ROOT, load_config(ROOT), args.task, args.state)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"moved {args.task} to {args.state}")
    return 0


def cmd_close(args: argparse.Namespace) -> int:
    try:
        close_task(ROOT, load_config(ROOT), args.task)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"closed {args.task}")
    return 0


def cmd_evidence(args: argparse.Namespace) -> int:
    evidence_args = getattr(args, "evidence_args", None)
    if evidence_args is not None:
        if not evidence_args:
            print("ERROR: use evidence TASK or evidence export TASK --out DIR", file=sys.stderr)
            return 1
        if evidence_args[0] == "export":
            return _cmd_evidence_export(evidence_args[1:])
        if len(evidence_args) != 1:
            print("ERROR: use evidence TASK or evidence export TASK --out DIR", file=sys.stderr)
            return 1
        task_id = evidence_args[0]
    else:
        task_id = args.task
    config = load_config(ROOT)
    records = _load_task_records_for_cli(config)
    if records is None:
        return 1
    for record in records:
        if record.task.get("id") != task_id:
            continue
        evidence = record.task.get("evidence", {})
        packet = evidence.get("packet") if isinstance(evidence, dict) else None
        if not packet:
            print(f"ERROR: {task_id} has no evidence.packet", file=sys.stderr)
            return 1
        path = ROOT / str(packet)
        if not path.exists():
            print(f"ERROR: evidence packet does not exist: {path}", file=sys.stderr)
            return 1
        print(path.read_text(encoding="utf-8"))
        return 0
    print(f"ERROR: task not found: {task_id}", file=sys.stderr)
    return 1


def _cmd_evidence_export(args: list[str]) -> int:
    if not args:
        print("ERROR: evidence export requires TASK", file=sys.stderr)
        return 1
    task_id = args[0]
    output_dir: Path | None = None
    index = 1
    while index < len(args):
        arg = args[index]
        if arg == "--out" and index + 1 < len(args):
            raw_output = Path(args[index + 1])
            output_dir = raw_output if raw_output.is_absolute() else ROOT / raw_output
            index += 2
            continue
        print(f"ERROR: unknown evidence export argument: {arg}", file=sys.stderr)
        return 1
    if output_dir is None:
        output_dir = ROOT / "harness" / "evidence-exports" / task_id
    try:
        result = export_task_evidence(ROOT, load_config(ROOT), task_id, output_dir)
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"exported evidence {result.task_id}: {result.output_dir}")
    print(f"manifest: {result.manifest_path}")
    return 0


def cmd_resume(_: argparse.Namespace) -> int:
    try:
        summary = resume_summary(ROOT, load_config(ROOT))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"ERROR: failed to load resume state: {exc}", file=sys.stderr)
        return 1
    print(summary)
    return 0


def cmd_session_resume(args: argparse.Namespace) -> int:
    config = load_config(ROOT)
    records = _load_task_records_for_cli(config)
    if records is None:
        return 1
    for record in records:
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


def cmd_provider_contract(args: argparse.Namespace) -> int:
    result = run_provider_contract_suite(ROOT, args.provider, command=args.command)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"provider contract: {result['provider']} {result['status']}")
        for fixture in result["fixtures"]:
            print(f"{fixture['name']}\t{fixture['status']}")
            for error in fixture.get("errors", []):
                print(f"  ERROR: {error}")
    return 0 if result["status"] == "passed" else 1


def cmd_ci_status(args: argparse.Namespace) -> int:
    try:
        config = load_config(ROOT)
        result = run_ci_status(ROOT, config, command=args.command)
        if args.task:
            record_task_evidence_reference(ROOT, config, args.task, "ci", result.run_path / "output.json")
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"ci {result.status}: {result.run_path}")
    return 0


def cmd_ci_provider_list(_: argparse.Namespace) -> int:
    for provider in list_ci_providers():
        print(f"{provider['name']}\t{provider['command']}\t{provider['description']}")
    return 0


def cmd_pr_status(args: argparse.Namespace) -> int:
    try:
        config = load_config(ROOT)
        result = run_pr_status(ROOT, config, task_id=args.task, command=args.command)
        if args.task:
            record_task_evidence_reference(ROOT, config, args.task, "pr", result.run_path / "output.json")
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"pr {result.status}: {result.run_path}")
    return 0


def cmd_pr_ensure(args: argparse.Namespace) -> int:
    try:
        config = load_config(ROOT)
        result = run_pr_ensure(ROOT, config, task_id=args.task, command=args.command)
        if args.task:
            record_task_evidence_reference(ROOT, config, args.task, "pr_request", result.run_path / "output.json")
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"pr ensure {result.status}: {result.run_path}")
    return 0


def cmd_release_status(args: argparse.Namespace) -> int:
    try:
        result = run_release_status(ROOT, load_config(ROOT), command=args.command)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"release {result.status}: {result.run_path}")
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
    try:
        if args.task:
            result = verify_task(ROOT, config, args.task)
        else:
            result = run_verification(
                ROOT,
                config,
                ROOT / "harness" / "runs" / "adhoc-verify" / "commands",
            )
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if result.failed:
        print("verification failed: " + ", ".join(result.failed), file=sys.stderr)
        return 1
    print("verification passed")
    return 0


def cmd_task_import(args: argparse.Namespace) -> int:
    try:
        if args.from_json == "-":
            plan = json.load(sys.stdin)
        else:
            with Path(args.from_json).open(encoding="utf-8") as handle:
                plan = json.load(handle)
        records = import_planner_tasks(ROOT, load_config(ROOT), plan)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
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
    init.add_argument("--adapter", choices=["generic", "go", "node", "python", "rust"], default="generic")
    init.add_argument("--agent-provider", choices=["command", *sorted(_builtin_session_provider_commands())], default="command")
    init.add_argument("--agent-command")
    init.set_defaults(func=cmd_init)

    subparsers.add_parser("doctor").set_defaults(func=cmd_doctor)
    autonomy = subparsers.add_parser("autonomy")
    autonomy_subparsers = autonomy.add_subparsers(dest="autonomy_command", required=True)
    autonomy_doctor_parser = autonomy_subparsers.add_parser("doctor")
    autonomy_doctor_parser.add_argument("--json", action="store_true")
    autonomy_doctor_parser.set_defaults(func=cmd_autonomy_doctor)
    subparsers.add_parser("validate-config").set_defaults(func=cmd_validate_config)

    contract = subparsers.add_parser("contract")
    contract_subparsers = contract.add_subparsers(dest="contract_command", required=True)
    contract_validate = contract_subparsers.add_parser("validate")
    contract_validate.add_argument("type", choices=sorted(CONTRACT_TYPES))
    contract_validate.add_argument("file")
    contract_validate.set_defaults(func=cmd_contract_validate)

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
    dispatch.add_argument("task", nargs="?")
    dispatch.add_argument("--actor", default="orchestrator")
    dispatch.add_argument("--limit", type=int, default=1)
    dispatch.set_defaults(func=cmd_dispatch)

    autopilot = subparsers.add_parser("autopilot")
    autopilot.add_argument("--dry-run", action="store_true")
    autopilot.add_argument("--run", action="store_true")
    autopilot.add_argument("--resume", action="store_true")
    autopilot.add_argument("--status", action="store_true")
    autopilot.add_argument("--limit", type=int)
    autopilot.add_argument("--max-steps", type=int)
    autopilot.add_argument("--loop", action="store_true")
    autopilot.add_argument("--until", choices=["terminal"])
    autopilot.add_argument("--max-cycles", type=int)
    autopilot.add_argument("--interval-seconds", type=float)
    autopilot.add_argument("--actor", default="orchestrator")
    autopilot.add_argument("--goal")
    autopilot.add_argument("--json", action="store_true")
    autopilot.set_defaults(func=cmd_autopilot)

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
    evidence.add_argument("evidence_args", nargs=argparse.REMAINDER)
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
    provider_contract = provider_subparsers.add_parser("contract")
    provider_contract.add_argument("--provider", required=True, choices=[item["name"] for item in list_session_providers()])
    provider_contract.add_argument("--command")
    provider_contract.add_argument("--json", action="store_true")
    provider_contract.set_defaults(func=cmd_provider_contract)

    ci = subparsers.add_parser("ci")
    ci_subparsers = ci.add_subparsers(dest="ci_command", required=True)
    ci_status = ci_subparsers.add_parser("status")
    ci_status.add_argument("--command")
    ci_status.add_argument("--task")
    ci_status.set_defaults(func=cmd_ci_status)
    ci_subparsers.add_parser("providers").set_defaults(func=cmd_ci_provider_list)

    pr = subparsers.add_parser("pr")
    pr_subparsers = pr.add_subparsers(dest="pr_command", required=True)
    pr_ensure = pr_subparsers.add_parser("ensure")
    pr_ensure.add_argument("task", nargs="?")
    pr_ensure.add_argument("--command")
    pr_ensure.set_defaults(func=cmd_pr_ensure)
    pr_status = pr_subparsers.add_parser("status")
    pr_status.add_argument("task", nargs="?")
    pr_status.add_argument("--command")
    pr_status.set_defaults(func=cmd_pr_status)

    release = subparsers.add_parser("release")
    release_subparsers = release.add_subparsers(dest="release_command", required=True)
    release_status = release_subparsers.add_parser("status")
    release_status.add_argument("--command")
    release_status.set_defaults(func=cmd_release_status)

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
    try:
        return int(args.func(args))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
