from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path
from typing import Any

from .autonomy import autonomy_doctor
from .capabilities import get_capability, list_capabilities, run_planner_capability, run_task_capability
from .ci import BUILTIN_CI_PROVIDERS, list_ci_providers, run_ci_action, run_ci_status
from .config import load_config, validate_config
from .contracts import CONTRACT_TYPES, validate_contract_file
from .governance import SCHEMA_TYPES, governance_policy, json_schema_for, migrate_file, openapi_document
from .io import dump_data, load_data
from .evidence_export import export_autopilot_bundle, export_release_bundle, export_task_evidence, verify_evidence_bundle
from .git import list_git_providers, run_git_publish
from .observability import inspect_run, inspect_run_diff
from .orchestrator import (
    AutopilotRunResult,
    ExecutionPlan,
    build_execution_plan,
    read_autopilot_log_lines,
    request_autopilot_cancel,
    run_autopilot,
)
from .planner import import_planner_tasks
from .plugins import discover_plugins
from .pr import list_pr_providers, run_pr_ensure, run_pr_merge, run_pr_status
from .provider_commands import shell_command_exists as _shared_shell_command_exists
from .provider_contracts import run_provider_contract_suite
from .provider_smoke import run_provider_readiness_suite
from .recovery import recover_runtime
from .release import list_release_providers, run_release_status
from .resume import resume_summary
from .runner import run_verification
from .secrets import secret_scan
from .sessions import list_session_providers, resume_agent_session
from .source_ingest import SOURCE_KINDS, import_source
from .tasks import (
    TASK_STATES,
    TaskRecord,
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

BUILTIN_PROJECT_ADAPTERS = [
    "generic",
    "python",
    "node",
    "go",
    "rust",
    "monorepo",
    "docker",
    "bazel",
    "java",
    "kotlin",
    "dotnet",
    "swift",
    "dart",
    "ruby",
    "php",
]

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
    (target / "harness" / "git-runs").mkdir(parents=True, exist_ok=True)
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


def cmd_schema_migrate(args: argparse.Namespace) -> int:
    path = Path(args.from_json)
    try:
        result = migrate_file(path, kind=args.kind, write=bool(args.write))
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        mode = "wrote" if args.write else "checked"
        changed = "changed" if result["changed"] else "unchanged"
        print(f"schema migrate {mode}: {args.kind} {changed}")
        for migration in result["migrations"]:
            print(f"  {migration}")
    return 0


def cmd_schema_export(args: argparse.Namespace) -> int:
    try:
        schema = json_schema_for(args.type)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(schema, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(schema, ensure_ascii=False, indent=2))
    return 0


def cmd_schema_openapi(args: argparse.Namespace) -> int:
    document = openapi_document()
    if args.json:
        print(json.dumps(document, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(document, ensure_ascii=False, indent=2))
    return 0


def cmd_plugin_list(args: argparse.Namespace) -> int:
    report = discover_plugins(ROOT, load_config(ROOT))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        if not report["plugins"]:
            print("no plugins found")
        for plugin in report["plugins"]:
            print(f"{plugin['name']}\t{plugin['version']}\t{plugin['manifest']}")
        for error in report["errors"]:
            print(f"ERROR: {error['manifest']}: {error['error']}", file=sys.stderr)
    return 1 if report["errors"] else 0


def cmd_governance_policy(args: argparse.Namespace) -> int:
    policy = governance_policy()
    if args.json:
        print(json.dumps(policy, ensure_ascii=False, indent=2))
    else:
        print(f"provider_contract_version={policy['provider_contract_version']}")
        print("supported_schema_versions=" + ",".join(str(item) for item in policy["supported_schema_versions"]))
        print("stable_release_flow:")
        for step in policy["stable_release_flow"]:
            print(f"  {step}")
        print(f"pre_1_0_breaking_changes: {policy['pre_1_0_breaking_changes']}")
    return 0


def cmd_install_smoke(args: argparse.Namespace) -> int:
    errors = _install_smoke_errors(
        offline=bool(args.offline),
        check_template_mirror=bool(args.check_template_mirror),
        skip_path_check=bool(args.skip_path_check),
        adapter=str(args.adapter),
    )
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    checks = ["python", "templates", "init", "doctor"]
    if not args.skip_path_check:
        checks.append("PATH")
    if args.offline:
        checks.append("offline")
    if args.check_template_mirror:
        checks.append("template mirror")
    print(f"install smoke passed: {', '.join(checks)}")
    return 0


def _install_smoke_errors(
    *,
    offline: bool,
    check_template_mirror: bool,
    skip_path_check: bool,
    adapter: str,
) -> list[str]:
    errors: list[str] = []
    if sys.version_info < (3, 11):
        errors.append("Python 3.11+ is required")
    if not skip_path_check and shutil.which("attestflow") is None:
        errors.append("attestflow console script was not found on PATH")

    templates_root = _templates_root()
    if not (templates_root / "base" / "harness.yml").exists():
        errors.append(f"package templates are missing: {templates_root / 'base' / 'harness.yml'}")
    if check_template_mirror:
        errors.extend(_template_mirror_errors())
    if errors:
        return errors

    with tempfile.TemporaryDirectory(prefix="attestflow-install-smoke-") as tmp:
        target = Path(tmp) / "project"
        init_exit = cmd_init(
            argparse.Namespace(path=str(target), adapter=adapter, agent_provider="command", agent_command=None)
        )
        if init_exit != 0:
            errors.append(f"init smoke failed with exit code {init_exit}")
            return errors
        config = load_config(target)
        if offline:
            errors.extend(_offline_config_errors(config))
        errors.extend(_doctor_errors(target, config))
    return errors


def _template_mirror_errors() -> list[str]:
    checkout_templates = Path.cwd() / "templates"
    source_root = checkout_templates if checkout_templates.exists() else Path(__file__).resolve().parents[1] / "templates"
    package_root = Path(__file__).resolve().parent / "templates"
    if not source_root.exists():
        return [f"source template mirror is missing: {source_root}"]
    source_files = _relative_files(source_root)
    package_files = _relative_files(package_root)
    errors: list[str] = []
    missing = sorted(source_files - package_files)
    extra = sorted(package_files - source_files)
    if missing:
        errors.append(f"package template mirror is missing files: {', '.join(str(path) for path in missing)}")
    if extra:
        errors.append(f"package template mirror has extra files: {', '.join(str(path) for path in extra)}")
    for relative in sorted(source_files & package_files):
        if (source_root / relative).read_bytes() != (package_root / relative).read_bytes():
            errors.append(f"package template mirror differs from source template: {relative}")
    return errors


def _relative_files(root: Path) -> set[Path]:
    if not root.exists():
        return set()
    return {path.relative_to(root) for path in root.rglob("*") if path.is_file()}


def _offline_config_errors(config: dict) -> list[str]:
    integrations = config.get("integrations", {})
    if not isinstance(integrations, dict):
        return []
    configured = [
        key
        for key in ("ci_provider", "pr_provider", "release_provider")
        if isinstance(integrations.get(key), dict) and integrations.get(key)
    ]
    if configured:
        return [f"offline install smoke expected no external integrations, found: {', '.join(configured)}"]
    return []


def _configure_initialized_adapter(target: Path, adapter: str) -> None:
    config_path = target / "harness.yml"
    config = load_data(config_path)
    project = config.get("project", {})
    project = project if isinstance(project, dict) else {}
    project["adapter"] = adapter
    if adapter == "python":
        _configure_python_adapter_defaults(target, config, project)
    elif adapter == "node":
        _configure_node_adapter_defaults(target, config, project)
    elif adapter == "go":
        _configure_go_adapter_defaults(target, config, project)
    elif adapter == "rust":
        _configure_rust_adapter_defaults(target, config, project)
    elif adapter == "monorepo":
        _configure_monorepo_adapter_defaults(target, config, project)
    elif adapter == "docker":
        _configure_docker_adapter_defaults(target, config, project)
    elif adapter == "bazel":
        _configure_bazel_adapter_defaults(target, config, project)
    elif adapter == "java":
        _configure_java_adapter_defaults(target, config, project)
    elif adapter == "kotlin":
        _configure_kotlin_adapter_defaults(target, config, project)
    elif adapter == "dotnet":
        _configure_dotnet_adapter_defaults(target, config, project)
    elif adapter == "swift":
        _configure_swift_adapter_defaults(target, config, project)
    elif adapter == "dart":
        _configure_dart_adapter_defaults(target, config, project)
    elif adapter == "ruby":
        _configure_ruby_adapter_defaults(target, config, project)
    elif adapter == "php":
        _configure_php_adapter_defaults(target, config, project)
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
    if (target / "pnpm-lock.yaml").exists() or (target / "pnpm-workspace.yaml").exists():
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


def _configure_monorepo_adapter_defaults(target: Path, config: dict, project: dict) -> None:
    package_json = _load_node_package_json(target)
    scripts = package_json.get("scripts", {}) if isinstance(package_json, dict) else {}
    scripts = scripts if isinstance(scripts, dict) else {}
    package_manager = _detect_node_package_manager(target)
    project["package_manager"] = package_manager
    workspace_tools: list[str] = []
    if (target / "pnpm-workspace.yaml").exists():
        workspace_tools.append("pnpm-workspace")
    if (target / "turbo.json").exists():
        workspace_tools.append("turborepo")
    if (target / "nx.json").exists():
        workspace_tools.append("nx")
    project["workspace_tools"] = workspace_tools
    commands = config.get("commands", {})
    commands = commands if isinstance(commands, dict) else {}
    if "test" in scripts:
        commands["unit"] = _workspace_script_command(package_manager, "test")
    if "lint" in scripts:
        commands["lint"] = _workspace_script_command(package_manager, "lint")
    if "typecheck" in scripts:
        commands["typecheck"] = _workspace_script_command(package_manager, "typecheck")
    if "build" in scripts:
        commands["project_verify"] = _workspace_script_command(package_manager, "build")
    config["commands"] = commands


def _workspace_script_command(package_manager: str, script: str) -> str:
    if package_manager == "pnpm":
        return "pnpm -r test" if script == "test" else f"pnpm -r run {script}"
    if package_manager == "yarn":
        return f"yarn workspaces foreach run {script}"
    return f"npm run {script} --workspaces"


def _configure_docker_adapter_defaults(target: Path, config: dict, project: dict) -> None:
    project["container"] = "docker"
    compose_file = _detect_compose_file(target)
    if compose_file:
        project["compose_file"] = compose_file
    commands = config.get("commands", {})
    commands = commands if isinstance(commands, dict) else {}
    if (target / "Dockerfile").exists():
        commands["project_verify"] = "docker build ."
    execution = config.get("execution", {})
    execution = execution if isinstance(execution, dict) else {}
    docker = execution.get("docker", {})
    docker = docker if isinstance(docker, dict) else {}
    docker["enabled"] = True
    execution["docker"] = docker
    policies = config.get("policies", {})
    policies = policies if isinstance(policies, dict) else {}
    policies["docker_required"] = True
    config["commands"] = commands
    config["execution"] = execution
    config["policies"] = policies


def _detect_compose_file(target: Path) -> str | None:
    for name in ("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml"):
        if (target / name).exists():
            return name
    return None


def _configure_bazel_adapter_defaults(target: Path, config: dict, project: dict) -> None:
    project["build_system"] = "bazel"
    commands = config.get("commands", {})
    commands = commands if isinstance(commands, dict) else {}
    if any((target / name).exists() for name in ("MODULE.bazel", "WORKSPACE.bazel", "WORKSPACE")):
        commands["unit"] = "bazel test //..."
        commands["project_verify"] = "bazel build //..."
    config["commands"] = commands


def _configure_java_adapter_defaults(target: Path, config: dict, project: dict) -> None:
    project["module"] = "java"
    commands = config.get("commands", {})
    commands = commands if isinstance(commands, dict) else {}
    build_tool = _detect_jvm_build_tool(target)
    project["build_tool"] = build_tool
    if build_tool == "maven":
        commands["unit"] = "mvn test"
        commands["project_verify"] = "mvn verify"
    elif build_tool == "gradle":
        gradle = _gradle_command(target)
        commands["unit"] = f"{gradle} test"
        commands["project_verify"] = f"{gradle} build"
    config["commands"] = commands


def _configure_kotlin_adapter_defaults(target: Path, config: dict, project: dict) -> None:
    project["module"] = "kotlin"
    commands = config.get("commands", {})
    commands = commands if isinstance(commands, dict) else {}
    project["build_tool"] = "gradle" if _has_gradle_project(target) else _detect_jvm_build_tool(target)
    if project["build_tool"] == "gradle":
        gradle = _gradle_command(target)
        commands["unit"] = f"{gradle} test"
        commands["project_verify"] = f"{gradle} build"
    elif project["build_tool"] == "maven":
        commands["unit"] = "mvn test"
        commands["project_verify"] = "mvn verify"
    config["commands"] = commands


def _detect_jvm_build_tool(target: Path) -> str:
    if (target / "pom.xml").exists():
        return "maven"
    if _has_gradle_project(target):
        return "gradle"
    return "unknown"


def _has_gradle_project(target: Path) -> bool:
    return any((target / name).exists() for name in ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"))


def _gradle_command(target: Path) -> str:
    return "./gradlew" if (target / "gradlew").exists() else "gradle"


def _configure_dotnet_adapter_defaults(target: Path, config: dict, project: dict) -> None:
    project["module"] = "dotnet"
    commands = config.get("commands", {})
    commands = commands if isinstance(commands, dict) else {}
    if list(target.glob("*.sln")) or list(target.glob("*.csproj")):
        commands["unit"] = "dotnet test"
        commands["project_verify"] = "dotnet build"
    config["commands"] = commands


def _configure_swift_adapter_defaults(target: Path, config: dict, project: dict) -> None:
    project["module"] = "swift"
    commands = config.get("commands", {})
    commands = commands if isinstance(commands, dict) else {}
    if (target / "Package.swift").exists():
        commands["unit"] = "swift test"
        commands["project_verify"] = "swift build"
    config["commands"] = commands


def _configure_dart_adapter_defaults(target: Path, config: dict, project: dict) -> None:
    project["module"] = "dart"
    commands = config.get("commands", {})
    commands = commands if isinstance(commands, dict) else {}
    if (target / "pubspec.yaml").exists():
        commands["unit"] = "dart test"
        commands["typecheck"] = "dart analyze"
    config["commands"] = commands


def _configure_ruby_adapter_defaults(target: Path, config: dict, project: dict) -> None:
    project["module"] = "ruby"
    commands = config.get("commands", {})
    commands = commands if isinstance(commands, dict) else {}
    if (target / "Rakefile").exists():
        commands["unit"] = "bundle exec rake test"
        commands["project_verify"] = "bundle exec rake"
    elif (target / "Gemfile").exists():
        commands["unit"] = "bundle exec ruby -Itest"
    config["commands"] = commands


def _configure_php_adapter_defaults(target: Path, config: dict, project: dict) -> None:
    project["module"] = "php"
    commands = config.get("commands", {})
    commands = commands if isinstance(commands, dict) else {}
    composer = _load_composer_json(target)
    scripts = composer.get("scripts", {}) if isinstance(composer, dict) else {}
    scripts = scripts if isinstance(scripts, dict) else {}
    if "test" in scripts:
        commands["unit"] = "composer test"
    elif (target / "phpunit.xml").exists() or (target / "phpunit.xml.dist").exists():
        commands["unit"] = "vendor/bin/phpunit"
    if (target / "composer.json").exists():
        commands["project_verify"] = "composer validate"
    config["commands"] = commands


def _load_composer_json(target: Path) -> dict:
    composer_json = target / "composer.json"
    if not composer_json.exists():
        return {}
    try:
        data = json.loads(composer_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


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
    errors.extend(_doctor_command_provider_errors(config, "git_provider", "Git"))
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
        ("git_runs", "harness/git-runs"),
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
    provider_options = provider_config.get("provider_options", {})
    if not command and isinstance(provider_options, dict):
        command = provider_options.get("command")
    if not command:
        builtin_commands = _builtin_command_provider_commands(key)
        command = builtin_commands.get(provider)
    if not command:
        return [f"{label} provider command must be configured for {provider}"]
    if not _shell_command_exists(str(command)):
        return [f"{label} provider command not found for {provider}: {command}"]
    return []


def _builtin_command_provider_commands(key: str) -> dict[str, str]:
    if key == "git_provider":
        return {provider["name"]: provider["command"] for provider in list_git_providers()}
    if key == "pr_provider":
        return {provider["name"]: provider["command"] for provider in list_pr_providers()}
    if key == "release_provider":
        return {provider["name"]: provider["command"] for provider in list_release_providers()}
    return {}


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
    selected_modes = [args.dry_run, args.run, args.resume, args.status, args.cancel, args.logs]
    if sum(1 for selected in selected_modes if selected) > 1:
        print(
            "ERROR: choose only one autopilot mode: --dry-run, --run, --resume, or --status; "
            "or --cancel/--logs",
            file=sys.stderr,
        )
        return 1
    if not any(selected_modes):
        print("ERROR: use --dry-run, --run, --resume, or --status; or --cancel/--logs", file=sys.stderr)
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
        if args.cancel:
            run_path = _autopilot_run_path(ROOT, config, args.run_path)
            cancellation = request_autopilot_cancel(run_path, reason=args.reason)
        elif args.logs:
            run_path = _autopilot_run_path(ROOT, config, args.run_path)
            log_lines = read_autopilot_log_lines(run_path)
        elif args.status:
            status = _autopilot_status(ROOT, config, args.run_path)
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
        if args.cancel:
            payload = {"run_path": str(run_path), "cancellation": cancellation}
        elif args.logs:
            payload = {"run_path": str(run_path), "lines": log_lines}
        elif args.status:
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
        if args.cancel or args.logs or args.status or args.dry_run:
            return 0
        if not args.status and not args.dry_run:
            return 1 if result.failed or result.blocked or result.cancelled else 0
        return 0
    if args.cancel:
        suffix = f": {cancellation['reason']}" if cancellation.get("reason") else ""
        print(f"cancel requested: {run_path}{suffix}")
        return 0
    if args.logs:
        for line in log_lines:
            print(line)
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
    return 1 if result.failed or result.blocked or result.cancelled else 0


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
    if result.cancelled:
        print(f"cancelled {len(result.cancelled)} task(s): {', '.join(result.cancelled)}")
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
        "intake": result.intake,
        "intake_status": result.intake_status,
        "planned": result.planned,
        "planner": result.planner,
        "dispatched": result.dispatched,
        "failed": result.failed,
        "blocked": result.blocked,
        "cancelled": result.cancelled,
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
        "batch_executions": result.batch_executions or [],
    }


def _autopilot_status(root: Path, config: dict, run_path_arg: str | None = None) -> dict:
    metadata_path = _autopilot_metadata_path(root, config, run_path_arg)
    metadata = load_data(metadata_path)
    metadata["path"] = str(metadata_path.parent)
    if "run_id" not in metadata:
        metadata["run_id"] = metadata_path.parent.name
    return metadata


def _latest_autopilot_run_path(root: Path, config: dict) -> Path:
    return _latest_autopilot_metadata_path(root, config).parent


def _autopilot_run_path(root: Path, config: dict, run_path_arg: str | None = None) -> Path:
    if not run_path_arg:
        return _latest_autopilot_run_path(root, config)
    path = Path(run_path_arg)
    return path if path.is_absolute() else root / path


def _autopilot_metadata_path(root: Path, config: dict, run_path_arg: str | None = None) -> Path:
    if run_path_arg:
        return _autopilot_run_path(root, config, run_path_arg) / "metadata.json"
    return _latest_autopilot_metadata_path(root, config)


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
    cancelled = status.get("cancelled", [])
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
    if cancelled:
        print(f"cancelled={', '.join(str(item) for item in cancelled)}")
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


def cmd_inspect(args: argparse.Namespace) -> int:
    if args.run and args.diff:
        print("ERROR: use either --run or --diff, not both", file=sys.stderr)
        return 1
    config = load_config(ROOT)
    if args.diff:
        report = inspect_run_diff(ROOT, config, args.diff[0], args.diff[1])
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            _print_inspect_diff(report)
        return 0
    report = inspect_run(ROOT, config, args.run)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_inspect_report(report)
    return 0


def cmd_recover(args: argparse.Namespace) -> int:
    report = recover_runtime(
        ROOT,
        load_config(ROOT),
        apply=bool(args.apply),
        resume_interrupted=bool(args.resume_interrupted),
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    _print_recover_report(report)
    return 0


def _print_inspect_report(report: dict) -> None:
    pause_reason = report.get("pause_reason")
    print(
        "inspect run: "
        f"{report.get('run_id')} "
        f"status={report.get('status')} "
        f"steps={report.get('steps', 0)}"
        f"{f' pause_reason={pause_reason}' if pause_reason else ''}"
    )
    print(f"path: {report.get('path')}")
    if report.get("actions"):
        print(f"actions: {', '.join(str(item) for item in report['actions'])}")
    if report.get("planned"):
        print(f"planned: {', '.join(str(item) for item in report['planned'])}")
    if report.get("dispatched"):
        print(f"dispatched: {', '.join(str(item) for item in report['dispatched'])}")
    if report.get("release_status"):
        print(f"release_status: {report.get('release_status')}")
    print("timeline:")
    timeline = report.get("timeline") or []
    if not timeline:
        print("  none")
    for event in timeline:
        print(f"  {_format_timeline_event(event)}")
    print("blockers:")
    blockers = report.get("blockers") or []
    if not blockers:
        print("  none")
    for blocker in blockers:
        print(f"  {_format_blocker(blocker)}")
    print("provider failures:")
    provider_failures = report.get("provider_failures") or []
    if not provider_failures:
        print("  none")
    for failure in provider_failures:
        print(f"  {_format_provider_failure(failure)}")
    next_action = report.get("next_action")
    if next_action:
        print(f"next: {next_action}")


def _print_inspect_diff(report: dict) -> None:
    print(f"run diff: {report.get('left_run_id')} -> {report.get('right_run_id')}")
    scalar_changes = report.get("scalar_changes") or {}
    if not scalar_changes and not report.get("list_changes"):
        print("no changes")
        return
    for key in ("status", "pause_reason", "release_status"):
        if key in scalar_changes:
            change = scalar_changes[key]
            print(f"{key}: {change.get('from')} -> {change.get('to')}")
    for key, change in (report.get("list_changes") or {}).items():
        added = change.get("added") or []
        removed = change.get("removed") or []
        if added:
            print(f"{key}_added: {', '.join(str(item) for item in added)}")
        if removed:
            print(f"{key}_removed: {', '.join(str(item) for item in removed)}")


def _format_timeline_event(event: dict) -> str:
    parts = []
    if event.get("timestamp"):
        parts.append(str(event["timestamp"]))
    parts.append(str(event.get("event", "unknown")))
    data = event.get("data")
    if isinstance(data, dict) and data:
        parts.append(_compact_key_values(data))
    elif event.get("error"):
        parts.append(str(event["error"]))
    return " ".join(parts)


def _format_blocker(blocker: dict) -> str:
    parts = [str(blocker.get("task_id") or "unknown")]
    if blocker.get("blocker_id"):
        parts.append(str(blocker["blocker_id"]))
    if blocker.get("owner"):
        parts.append(f"owner={blocker['owner']}")
    if blocker.get("reason"):
        parts.append(f"reason={blocker['reason']}")
    if blocker.get("unblock_condition"):
        parts.append(f"unblock={blocker['unblock_condition']}")
    if blocker.get("source"):
        parts.append(f"source={blocker['source']}")
    return " ".join(parts)


def _format_provider_failure(failure: dict) -> str:
    parts = [str(failure.get("type") or "failed")]
    if failure.get("provider"):
        parts.append(f"provider={failure['provider']}")
    if failure.get("automatic_action"):
        parts.append(f"action={failure['automatic_action']}")
    if failure.get("summary"):
        parts.append(f"summary={failure['summary']}")
    if failure.get("source"):
        parts.append(f"source={failure['source']}")
    return " ".join(parts)


def _compact_key_values(data: dict) -> str:
    values = []
    for key in ("step", "task", "task_id", "tasks", "status", "reason", "error", "run_path", "path"):
        if key not in data:
            continue
        value = data[key]
        if isinstance(value, list):
            value = ",".join(str(item) for item in value)
        values.append(f"{key}={value}")
    if values:
        return " ".join(values)
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def _print_recover_report(report: dict) -> None:
    mode = "apply" if report.get("applied") else "dry-run"
    print(f"recover: {mode}")
    issues = report.get("issues") or []
    print(f"issues: {len(issues)}")
    for issue in issues:
        print(f"  {issue.get('type')}: {issue.get('path')} - {issue.get('summary')}")
        if issue.get("next_action"):
            print(f"    next: {issue['next_action']}")
    actions = report.get("actions") or []
    if actions:
        print(f"actions: {len(actions)}")
        for action in actions:
            detail = action.get("target") or action.get("path") or action.get("reason") or ""
            print(f"  {action.get('type')} {action.get('status')}: {detail}")


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
            print("ERROR: use evidence TASK, evidence export TASK --out DIR, evidence bundle, or evidence verify", file=sys.stderr)
            return 1
        if evidence_args[0] == "export":
            return _cmd_evidence_export(evidence_args[1:])
        if evidence_args[0] == "bundle":
            return _cmd_evidence_bundle(evidence_args[1:])
        if evidence_args[0] == "verify":
            return _cmd_evidence_verify(evidence_args[1:])
        if len(evidence_args) != 1:
            print("ERROR: use evidence TASK, evidence export TASK --out DIR, evidence bundle, or evidence verify", file=sys.stderr)
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


def _cmd_evidence_bundle(args: list[str]) -> int:
    run_id: str | None = None
    release_id: str | None = None
    output_dir: Path | None = None
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--run" and index + 1 < len(args):
            run_id = args[index + 1]
            index += 2
            continue
        if arg == "--release" and index + 1 < len(args):
            release_id = args[index + 1]
            index += 2
            continue
        if arg == "--out" and index + 1 < len(args):
            raw_output = Path(args[index + 1])
            output_dir = raw_output if raw_output.is_absolute() else ROOT / raw_output
            index += 2
            continue
        print(f"ERROR: unknown evidence bundle argument: {arg}", file=sys.stderr)
        return 1
    if bool(run_id) == bool(release_id):
        print("ERROR: evidence bundle requires exactly one of --run RUN or --release RELEASE", file=sys.stderr)
        return 1
    config = load_config(ROOT)
    try:
        if run_id:
            output_dir = output_dir or ROOT / "harness" / "evidence-bundles" / str(run_id)
            result = export_autopilot_bundle(ROOT, config, str(run_id), output_dir)
            print(f"exported autopilot evidence {result.identifier}: {result.output_dir}")
        else:
            output_dir = output_dir or ROOT / "harness" / "evidence-bundles" / str(release_id)
            result = export_release_bundle(ROOT, config, str(release_id), output_dir)
            print(f"exported release evidence {result.identifier}: {result.output_dir}")
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"manifest: {result.manifest_path}")
    print(f"audit: {result.output_dir / 'audit.md'}")
    return 0


def _cmd_evidence_verify(args: list[str]) -> int:
    if not args:
        print("ERROR: evidence verify requires BUNDLE_DIR", file=sys.stderr)
        return 1
    bundle_dir = Path(args[0])
    bundle_dir = bundle_dir if bundle_dir.is_absolute() else ROOT / bundle_dir
    check_source = False
    for arg in args[1:]:
        if arg == "--check-source":
            check_source = True
            continue
        print(f"ERROR: unknown evidence verify argument: {arg}", file=sys.stderr)
        return 1
    result = verify_evidence_bundle(ROOT, bundle_dir, check_source=check_source)
    if result.errors:
        for error in result.errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    for warning in result.warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    print(f"evidence bundle valid: {result.manifest_path}")
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


def cmd_provider_smoke(args: argparse.Namespace) -> int:
    result = run_provider_readiness_suite(
        ROOT,
        args.provider,
        command=args.command,
        timeout_seconds=args.timeout_seconds,
        retries=args.retries,
        skip_contract=args.skip_contract,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"provider smoke: {result['provider']} {result['status']}")
        print(f"version\t{result['version']['status']}")
        print(f"live_smoke\t{result['smoke']['status']}")
        print(f"contract\t{result['contract']['status']}")
        for section in ("version", "smoke"):
            failure = result[section].get("failure")
            if failure:
                print(f"  {section} ERROR: {failure['type']} {failure['summary']}")
    return 0 if result["status"] == "passed" else 1


def cmd_ci_status(args: argparse.Namespace) -> int:
    return _cmd_ci_action(args, "status")


def cmd_ci_action(args: argparse.Namespace) -> int:
    return _cmd_ci_action(args, str(args.ci_command))


def _cmd_ci_action(args: argparse.Namespace, action: str) -> int:
    try:
        config = load_config(ROOT)
        result = run_ci_action(
            ROOT,
            config,
            action=action,
            command=args.command,
            provider_options=_ci_provider_options_from_args(args),
        )
        if args.task:
            record_task_evidence_reference(ROOT, config, args.task, "ci", result.run_path / "output.json")
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"ci {result.status}: {result.run_path}")
    return 0


def _ci_provider_options_from_args(args: argparse.Namespace) -> dict[str, Any]:
    options: dict[str, Any] = {}
    for key in (
        "run_id",
        "head_sha",
        "branch",
        "workflow",
        "event",
        "status_filter",
        "limit",
        "max_wait_seconds",
        "poll_interval_seconds",
        "download_dir",
        "ref",
    ):
        value = getattr(args, key, None)
        if value is not None:
            options[key] = value
    if getattr(args, "failed", False):
        options["rerun_failed"] = True
    if getattr(args, "download", False):
        options["download_artifacts"] = True
    inputs = getattr(args, "input", None)
    if inputs:
        parsed: dict[str, str] = {}
        for item in inputs:
            if "=" not in item:
                raise ValueError(f"ci dispatch --input must be KEY=VALUE: {item}")
            key, value = item.split("=", 1)
            if not key.strip():
                raise ValueError(f"ci dispatch --input key must be non-empty: {item}")
            parsed[key.strip()] = value
        options["inputs"] = parsed
    return options


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


def cmd_pr_provider_list(_: argparse.Namespace) -> int:
    for provider in list_pr_providers():
        print(f"{provider['name']}\t{provider['command']}\t{provider['description']}")
    return 0


def cmd_publish(args: argparse.Namespace) -> int:
    try:
        config = load_config(ROOT)
        result = run_git_publish(ROOT, config, task_id=args.task, command=args.command)
        if args.task:
            record_task_evidence_reference(ROOT, config, args.task, "git", result.run_path / "output.json")
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"publish {result.status}: {result.run_path}")
    return 0


def cmd_publish_provider_list(_: argparse.Namespace) -> int:
    for provider in list_git_providers():
        print(f"{provider['name']}\t{provider['command']}\t{provider['description']}")
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


def cmd_pr_merge(args: argparse.Namespace) -> int:
    try:
        config = load_config(ROOT)
        result = run_pr_merge(ROOT, config, task_id=args.task, command=args.command)
        if args.task:
            record_task_evidence_reference(ROOT, config, args.task, "pr_merge", result.run_path / "output.json")
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"pr merge {result.status}: {result.run_path}")
    return 0


def cmd_release_status(args: argparse.Namespace) -> int:
    try:
        result = run_release_status(ROOT, load_config(ROOT), command=args.command)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"release {result.status}: {result.run_path}")
    return 0


def cmd_release_provider_list(_: argparse.Namespace) -> int:
    for provider in list_release_providers():
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


def cmd_source_import(args: argparse.Namespace) -> int:
    try:
        record = import_source(ROOT, load_config(ROOT), kind=args.kind, source_path=Path(args.from_json))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    payload = _source_import_payload(record)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"imported source {payload['kind']}:{payload['external_id']} -> {payload['task_id']}")
    return 0


def _source_import_payload(record: TaskRecord) -> dict:
    task = record.task
    source = task.get("source", {}) if isinstance(task, dict) else {}
    return {
        "schema_version": 1,
        "kind": source.get("kind"),
        "external_id": source.get("external_id"),
        "task_id": task.get("id"),
        "task": _relative_to_root(ROOT, record.path),
        "evidence": source.get("evidence"),
        "priority": task.get("priority"),
    }


def _relative_to_root(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


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
    init.add_argument("--adapter", choices=BUILTIN_PROJECT_ADAPTERS, default="generic")
    init.add_argument("--agent-provider", choices=["command", *sorted(_builtin_session_provider_commands())], default="command")
    init.add_argument("--agent-command")
    init.set_defaults(func=cmd_init)

    subparsers.add_parser("doctor").set_defaults(func=cmd_doctor)
    install_smoke = subparsers.add_parser("install-smoke")
    install_smoke.add_argument("--offline", action="store_true")
    install_smoke.add_argument("--check-template-mirror", action="store_true")
    install_smoke.add_argument("--skip-path-check", action="store_true")
    install_smoke.add_argument("--adapter", choices=BUILTIN_PROJECT_ADAPTERS, default="python")
    install_smoke.set_defaults(func=cmd_install_smoke)
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

    schema = subparsers.add_parser("schema")
    schema_subparsers = schema.add_subparsers(dest="schema_command", required=True)
    schema_migrate = schema_subparsers.add_parser("migrate")
    schema_migrate.add_argument("--kind", required=True, choices=["harness-config"])
    schema_migrate.add_argument("--from-json", required=True)
    schema_migrate.add_argument("--write", action="store_true")
    schema_migrate.add_argument("--json", action="store_true")
    schema_migrate.set_defaults(func=cmd_schema_migrate)
    schema_export = schema_subparsers.add_parser("export")
    schema_export.add_argument("--type", required=True, choices=sorted(SCHEMA_TYPES))
    schema_export.add_argument("--json", action="store_true")
    schema_export.set_defaults(func=cmd_schema_export)
    schema_openapi = schema_subparsers.add_parser("openapi")
    schema_openapi.add_argument("--json", action="store_true")
    schema_openapi.set_defaults(func=cmd_schema_openapi)

    plugin = subparsers.add_parser("plugin")
    plugin_subparsers = plugin.add_subparsers(dest="plugin_command", required=True)
    plugin_list = plugin_subparsers.add_parser("list")
    plugin_list.add_argument("--json", action="store_true")
    plugin_list.set_defaults(func=cmd_plugin_list)

    governance = subparsers.add_parser("governance")
    governance_subparsers = governance.add_subparsers(dest="governance_command", required=True)
    governance_policy_parser = governance_subparsers.add_parser("policy")
    governance_policy_parser.add_argument("--json", action="store_true")
    governance_policy_parser.set_defaults(func=cmd_governance_policy)

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
    autopilot.add_argument("--cancel", action="store_true")
    autopilot.add_argument("--logs", action="store_true")
    autopilot.add_argument("--limit", type=int)
    autopilot.add_argument("--max-steps", type=int)
    autopilot.add_argument("--loop", action="store_true")
    autopilot.add_argument("--until", choices=["terminal"])
    autopilot.add_argument("--max-cycles", type=int)
    autopilot.add_argument("--interval-seconds", type=float)
    autopilot.add_argument("--actor", default="orchestrator")
    autopilot.add_argument("--goal")
    autopilot.add_argument("--run-path")
    autopilot.add_argument("--reason")
    autopilot.add_argument("--json", action="store_true")
    autopilot.set_defaults(func=cmd_autopilot)

    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument("--run")
    inspect_parser.add_argument("--diff", nargs=2, metavar=("LEFT", "RIGHT"))
    inspect_parser.add_argument("--json", action="store_true")
    inspect_parser.set_defaults(func=cmd_inspect)

    recover = subparsers.add_parser("recover")
    recover.add_argument("--apply", action="store_true")
    recover.add_argument("--resume-interrupted", action="store_true")
    recover.add_argument("--json", action="store_true")
    recover.set_defaults(func=cmd_recover)

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
    provider_smoke = provider_subparsers.add_parser("smoke")
    provider_smoke.add_argument("--provider", required=True, choices=[item["name"] for item in list_session_providers()])
    provider_smoke.add_argument("--command")
    provider_smoke.add_argument("--timeout-seconds", type=float)
    provider_smoke.add_argument("--retries", type=int, default=1)
    provider_smoke.add_argument("--skip-contract", action="store_true")
    provider_smoke.add_argument("--json", action="store_true")
    provider_smoke.set_defaults(func=cmd_provider_smoke)
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
    ci_status.add_argument("--run-id")
    ci_status.add_argument("--head-sha")
    ci_status.add_argument("--branch")
    ci_status.add_argument("--workflow")
    ci_status.add_argument("--event")
    ci_status.add_argument("--status-filter")
    ci_status.add_argument("--limit", type=int)
    ci_status.set_defaults(func=cmd_ci_status)
    ci_await = ci_subparsers.add_parser("await")
    ci_await.add_argument("--command")
    ci_await.add_argument("--task")
    ci_await.add_argument("--run-id")
    ci_await.add_argument("--head-sha")
    ci_await.add_argument("--branch")
    ci_await.add_argument("--workflow")
    ci_await.add_argument("--event")
    ci_await.add_argument("--status-filter")
    ci_await.add_argument("--limit", type=int)
    ci_await.add_argument("--max-wait-seconds", type=float)
    ci_await.add_argument("--poll-interval-seconds", type=float)
    ci_await.set_defaults(func=cmd_ci_action)
    ci_logs = ci_subparsers.add_parser("logs")
    ci_logs.add_argument("--command")
    ci_logs.add_argument("--task")
    ci_logs.add_argument("--run-id")
    ci_logs.add_argument("--head-sha")
    ci_logs.add_argument("--branch")
    ci_logs.add_argument("--workflow")
    ci_logs.add_argument("--event")
    ci_logs.add_argument("--limit", type=int)
    ci_logs.set_defaults(func=cmd_ci_action)
    ci_artifacts = ci_subparsers.add_parser("artifacts")
    ci_artifacts.add_argument("--command")
    ci_artifacts.add_argument("--task")
    ci_artifacts.add_argument("--run-id")
    ci_artifacts.add_argument("--head-sha")
    ci_artifacts.add_argument("--branch")
    ci_artifacts.add_argument("--workflow")
    ci_artifacts.add_argument("--event")
    ci_artifacts.add_argument("--limit", type=int)
    ci_artifacts.add_argument("--download", action="store_true")
    ci_artifacts.add_argument("--download-dir")
    ci_artifacts.set_defaults(func=cmd_ci_action)
    ci_rerun = ci_subparsers.add_parser("rerun")
    ci_rerun.add_argument("--command")
    ci_rerun.add_argument("--task")
    ci_rerun.add_argument("--run-id")
    ci_rerun.add_argument("--head-sha")
    ci_rerun.add_argument("--branch")
    ci_rerun.add_argument("--workflow")
    ci_rerun.add_argument("--event")
    ci_rerun.add_argument("--limit", type=int)
    ci_rerun.add_argument("--failed", action="store_true")
    ci_rerun.set_defaults(func=cmd_ci_action)
    ci_dispatch = ci_subparsers.add_parser("dispatch")
    ci_dispatch.add_argument("--command")
    ci_dispatch.add_argument("--task")
    ci_dispatch.add_argument("--workflow")
    ci_dispatch.add_argument("--ref")
    ci_dispatch.add_argument("--branch")
    ci_dispatch.add_argument("--input", action="append", default=[])
    ci_dispatch.set_defaults(func=cmd_ci_action)
    ci_subparsers.add_parser("providers").set_defaults(func=cmd_ci_provider_list)

    publish = subparsers.add_parser("publish")
    publish.add_argument("--task")
    publish.add_argument("--command")
    publish.add_argument("--providers", action="store_true")
    publish.set_defaults(func=lambda args: cmd_publish_provider_list(args) if args.providers else cmd_publish(args))

    pr = subparsers.add_parser("pr")
    pr_subparsers = pr.add_subparsers(dest="pr_command", required=True)
    pr_subparsers.add_parser("providers").set_defaults(func=cmd_pr_provider_list)
    pr_ensure = pr_subparsers.add_parser("ensure")
    pr_ensure.add_argument("task", nargs="?")
    pr_ensure.add_argument("--command")
    pr_ensure.set_defaults(func=cmd_pr_ensure)
    pr_status = pr_subparsers.add_parser("status")
    pr_status.add_argument("task", nargs="?")
    pr_status.add_argument("--command")
    pr_status.set_defaults(func=cmd_pr_status)
    pr_merge = pr_subparsers.add_parser("merge")
    pr_merge.add_argument("task", nargs="?")
    pr_merge.add_argument("--command")
    pr_merge.set_defaults(func=cmd_pr_merge)

    release = subparsers.add_parser("release")
    release_subparsers = release.add_subparsers(dest="release_command", required=True)
    release_subparsers.add_parser("providers").set_defaults(func=cmd_release_provider_list)
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

    source = subparsers.add_parser("source")
    source_subparsers = source.add_subparsers(dest="source_command", required=True)
    source_import = source_subparsers.add_parser("import")
    source_import.add_argument("--kind", required=True, choices=sorted(SOURCE_KINDS))
    source_import.add_argument("--from-json", required=True)
    source_import.add_argument("--json", action="store_true")
    source_import.set_defaults(func=cmd_source_import)

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
