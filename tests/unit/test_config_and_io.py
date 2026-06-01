from contextlib import redirect_stderr
import io
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

import attestflow.cli as cli
from attestflow.cli import cmd_doctor, cmd_init
from attestflow.config import load_config, validate_config
from attestflow.io import dump_data, load_data
from attestflow.runner import run_verification


class ConfigAndIoTests(unittest.TestCase):
    def test_round_trips_supported_yaml_subset(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.yml"
            expected = {
                "schema_version": 1,
                "project": {"name": "demo", "enabled": True},
                "commands": {"lint": None, "unit": "python -m unittest"},
                "paths": {"tasks": "harness/tasks"},
                "items": ["one", "two"],
            }

            dump_data(expected, path)

            self.assertEqual(load_data(path), expected)

    def test_load_config_requires_core_sections(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "harness.yml").write_text(
                """
schema_version: 1
project:
  name: demo
paths:
  tasks: harness/tasks
commands:
  unit: python -m unittest discover tests/unit
policies:
  require_bdd_before_unit: true
""".strip()
                + "\n",
                encoding="utf-8",
            )

            config = load_config(root)

            self.assertEqual(validate_config(config), [])

    def test_load_config_without_file_does_not_share_default_nested_state(self) -> None:
        with TemporaryDirectory() as first_tmp, TemporaryDirectory() as second_tmp:
            first = load_config(Path(first_tmp))
            first["commands"]["unit"] = "mutated"

            second = load_config(Path(second_tmp))

            self.assertNotEqual(second["commands"]["unit"], "mutated")

    def test_init_template_does_not_advertise_external_skills(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)

            exit_code = cmd_init(SimpleNamespace(path=str(root), adapter="generic", agent_provider="command", agent_command=None))
            config = load_data(root / "harness.yml")

            self.assertEqual(exit_code, 0)
            self.assertEqual(config["project"]["adapter"], "generic")
            self.assertTrue((root / "harness" / "adapters" / "generic" / "README.md").exists())
            self.assertNotIn("skills", config.get("integrations", {}))
            self.assertEqual(config["sessions"]["agent_provider"], "command")
            self.assertEqual(config["sessions"]["provider_options"], {})
            self.assertNotIn("provider", config["sessions"])
            self.assertEqual(config["capabilities"]["planner"]["agent_provider"], "command")

    def test_init_copies_selected_adapter_template(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)

            exit_code = cmd_init(SimpleNamespace(path=str(root), adapter="python", agent_provider="command", agent_command=None))
            config = load_data(root / "harness.yml")

            self.assertEqual(exit_code, 0)
            self.assertEqual(config["project"]["adapter"], "python")
            adapter_readme = root / "harness" / "adapters" / "python" / "README.md"
            self.assertTrue(adapter_readme.exists())
            self.assertIn("# Python Adapter", adapter_readme.read_text(encoding="utf-8"))

    def test_init_does_not_seed_runtime_queue_with_example_tasks(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)

            exit_code = cmd_init(SimpleNamespace(path=str(root), adapter="generic", agent_provider="command", agent_command=None))

            self.assertEqual(exit_code, 0)
            self.assertEqual(list((root / "harness" / "tasks" / "ready").glob("TASK-*.json")), [])
            self.assertTrue((root / "harness" / "planner-output.example.json").exists())

    def test_init_node_adapter_detects_package_manager_and_scripts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(
                """
{
  "scripts": {
    "test": "vitest run",
    "lint": "eslint .",
    "typecheck": "tsc --noEmit",
    "build": "vite build"
  }
}
""".strip()
                + "\n",
                encoding="utf-8",
            )
            (root / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")

            exit_code = cmd_init(SimpleNamespace(path=str(root), adapter="node", agent_provider="command", agent_command=None))
            config = load_data(root / "harness.yml")

            self.assertEqual(exit_code, 0)
            self.assertEqual(config["project"]["adapter"], "node")
            self.assertEqual(config["project"]["package_manager"], "pnpm")
            self.assertEqual(config["commands"]["unit"], "pnpm test")
            self.assertEqual(config["commands"]["lint"], "pnpm run lint")
            self.assertEqual(config["commands"]["typecheck"], "pnpm run typecheck")
            self.assertEqual(config["commands"]["project_verify"], "pnpm run build")

    def test_init_python_adapter_detects_pyproject_tools(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text(
                """
[project]
name = "demo"

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
line-length = 100

[tool.mypy]
python_version = "3.11"
""".lstrip(),
                encoding="utf-8",
            )

            exit_code = cmd_init(SimpleNamespace(path=str(root), adapter="python", agent_provider="command", agent_command=None))
            config = load_data(root / "harness.yml")

            self.assertEqual(exit_code, 0)
            self.assertEqual(config["project"]["adapter"], "python")
            self.assertEqual(config["project"]["test_runner"], "pytest")
            self.assertEqual(config["project"]["linter"], "ruff")
            self.assertEqual(config["project"]["typechecker"], "mypy")
            self.assertEqual(config["commands"]["unit"], "python -m pytest")
            self.assertEqual(config["commands"]["lint"], "python -m ruff check .")
            self.assertEqual(config["commands"]["typecheck"], "python -m mypy .")

    def test_init_rejects_unknown_adapter(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            error = io.StringIO()

            with redirect_stderr(error):
                exit_code = cmd_init(SimpleNamespace(path=str(root), adapter="missing", agent_provider="command", agent_command=None))

            self.assertEqual(exit_code, 1)
            self.assertIn("unknown adapter", error.getvalue())
            self.assertFalse((root / "harness.yml").exists())

    def test_init_can_write_builtin_agent_provider_preset(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_codex = root / "fake-codex"
            fake_codex.write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
            fake_codex.chmod(0o755)

            exit_code = cmd_init(
                SimpleNamespace(path=str(root), adapter="generic", agent_provider="codex", agent_command=str(fake_codex))
            )
            config = load_data(root / "harness.yml")

            self.assertEqual(exit_code, 0)
            self.assertEqual(config["sessions"]["agent_provider"], "codex")
            self.assertEqual(config["sessions"]["provider_options"]["command"], str(fake_codex))
            self.assertEqual(config["sessions"]["launch_command"], None)
            self.assertEqual(config["capabilities"]["planner"]["agent_provider"], "codex")
            self.assertEqual(config["capabilities"]["reviewer"]["agent_provider"], "codex")

    def test_doctor_checks_initialized_provider_command(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_codex = root / "fake-codex"
            fake_codex.write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
            fake_codex.chmod(0o755)
            cmd_init(SimpleNamespace(path=str(root), adapter="generic", agent_provider="codex", agent_command=str(fake_codex)))
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                exit_code = cmd_doctor(SimpleNamespace())
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 0)

    def test_doctor_runs_builtin_provider_preflight(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            args_log = root / "provider-args.txt"
            fake_codex = root / "fake-codex"
            fake_codex.write_text(
                f"""#!/usr/bin/env python3
import pathlib
import sys

pathlib.Path({str(args_log)!r}).write_text(" ".join(sys.argv[1:]), encoding="utf-8")
sys.exit(0)
""",
                encoding="utf-8",
            )
            fake_codex.chmod(0o755)
            cmd_init(SimpleNamespace(path=str(root), adapter="generic", agent_provider="codex", agent_command=str(fake_codex)))
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                exit_code = cmd_doctor(SimpleNamespace())
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 0)
            self.assertEqual(args_log.read_text(encoding="utf-8"), "doctor --json")

    def test_doctor_rejects_failing_provider_preflight(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_codex = root / "fake-codex"
            fake_codex.write_text(
                """#!/usr/bin/env python3
import sys

print("auth missing", file=sys.stderr)
sys.exit(7)
""",
                encoding="utf-8",
            )
            fake_codex.chmod(0o755)
            cmd_init(SimpleNamespace(path=str(root), adapter="generic", agent_provider="codex", agent_command=str(fake_codex)))
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                error = io.StringIO()
                with redirect_stderr(error):
                    exit_code = cmd_doctor(SimpleNamespace())
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 1)
            self.assertIn("session provider preflight failed for codex", error.getvalue())
            self.assertIn("auth missing", error.getvalue())

    def test_doctor_uses_configured_provider_preflight_args(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            args_log = root / "provider-args.txt"
            fake_codex = root / "fake-codex"
            fake_codex.write_text(
                f"""#!/usr/bin/env python3
import pathlib
import sys

pathlib.Path({str(args_log)!r}).write_text(" ".join(sys.argv[1:]), encoding="utf-8")
sys.exit(0)
""",
                encoding="utf-8",
            )
            fake_codex.chmod(0o755)
            cmd_init(SimpleNamespace(path=str(root), adapter="generic", agent_provider="codex", agent_command=str(fake_codex)))
            config = load_data(root / "harness.yml")
            config["sessions"]["provider_options"]["doctor_args"] = ["auth", "status"]
            dump_data(config, root / "harness.yml")
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                exit_code = cmd_doctor(SimpleNamespace())
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 0)
            self.assertEqual(args_log.read_text(encoding="utf-8"), "auth status")

    def test_doctor_rejects_opencode_with_no_credentials(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_opencode = root / "fake-opencode"
            fake_opencode.write_text(
                """#!/usr/bin/env python3
import sys

print("0 credentials")
sys.exit(0)
""",
                encoding="utf-8",
            )
            fake_opencode.chmod(0o755)
            cmd_init(
                SimpleNamespace(
                    path=str(root),
                    adapter="generic",
                    agent_provider="opencode",
                    agent_command=str(fake_opencode),
                )
            )
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                error = io.StringIO()
                with redirect_stderr(error):
                    exit_code = cmd_doctor(SimpleNamespace())
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 1)
            self.assertIn("session provider preflight output indicates opencode is not ready", error.getvalue())

    def test_doctor_rejects_missing_builtin_provider_command(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            cmd_init(
                SimpleNamespace(
                    path=str(root),
                    adapter="generic",
                    agent_provider="codex",
                    agent_command=str(root / "missing-codex"),
                )
            )
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                error = io.StringIO()
                with redirect_stderr(error):
                    exit_code = cmd_doctor(SimpleNamespace())
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 1)
            self.assertIn("session provider command not found", error.getvalue())

    def test_doctor_rejects_missing_ci_provider_command(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            cmd_init(SimpleNamespace(path=str(root), adapter="generic", agent_provider="command", agent_command=None))
            config = load_data(root / "harness.yml")
            config["integrations"]["ci_provider"] = {"provider": "command", "command": str(root / "missing-ci")}
            dump_data(config, root / "harness.yml")
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                error = io.StringIO()
                with redirect_stderr(error):
                    exit_code = cmd_doctor(SimpleNamespace())
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 1)
            self.assertIn("CI provider command not found", error.getvalue())

    def test_doctor_rejects_missing_pr_and_release_provider_commands(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            cmd_init(SimpleNamespace(path=str(root), adapter="generic", agent_provider="command", agent_command=None))
            config = load_data(root / "harness.yml")
            config["integrations"]["pr_provider"] = {"provider": "command", "command": str(root / "missing-pr")}
            config["integrations"]["release_provider"] = {"provider": "command", "command": str(root / "missing-release")}
            dump_data(config, root / "harness.yml")
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                error = io.StringIO()
                with redirect_stderr(error):
                    exit_code = cmd_doctor(SimpleNamespace())
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 1)
            text = error.getvalue()
            self.assertIn("PR provider command not found", text)
            self.assertIn("Release provider command not found", text)

    def test_doctor_rejects_missing_enabled_project_command_executable(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            cmd_init(SimpleNamespace(path=str(root), adapter="generic", agent_provider="command", agent_command=None))
            config = load_data(root / "harness.yml")
            config["commands"]["unit"] = f"{root / 'missing-test-runner'} test"
            config["commands"]["lint"] = None
            dump_data(config, root / "harness.yml")
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                error = io.StringIO()
                with redirect_stderr(error):
                    exit_code = cmd_doctor(SimpleNamespace())
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 1)
            self.assertIn("project command not found for unit", error.getvalue())
            self.assertNotIn("lint", error.getvalue())

    def test_doctor_rejects_missing_python_module_project_command(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            cmd_init(SimpleNamespace(path=str(root), adapter="generic", agent_provider="command", agent_command=None))
            config = load_data(root / "harness.yml")
            config["commands"]["unit"] = f"{sys.executable} -m definitely_missing_attestflow_tool"
            dump_data(config, root / "harness.yml")
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                error = io.StringIO()
                with redirect_stderr(error):
                    exit_code = cmd_doctor(SimpleNamespace())
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 1)
            self.assertIn("project command not found for unit", error.getvalue())
            self.assertIn("definitely_missing_attestflow_tool", error.getvalue())

    def test_cli_validate_task_missing_file_reports_error_without_traceback(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                error = io.StringIO()
                with redirect_stderr(error):
                    exit_code = cli.main(["validate-task", str(root / "missing-task.json")])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 1)
            self.assertIn("ERROR:", error.getvalue())
            self.assertIn("missing-task.json", error.getvalue())
            self.assertNotIn("Traceback", error.getvalue())

    def test_cli_reports_malformed_config_without_traceback(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "harness.yml").write_text("not yaml\n", encoding="utf-8")
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                error = io.StringIO()
                with redirect_stderr(error):
                    exit_code = cli.main(["validate-config"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 1)
            self.assertIn("ERROR:", error.getvalue())
            self.assertNotIn("Traceback", error.getvalue())

    def test_doctor_checks_all_runtime_output_directories(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            cmd_init(SimpleNamespace(path=str(root), adapter="generic", agent_provider="command", agent_command=None))
            (root / "harness" / "autopilot-runs").rmdir()
            (root / "harness" / "git-runs").rmdir()
            (root / "harness" / "pr-runs").rmdir()
            (root / "harness" / "release-runs").rmdir()
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                error = io.StringIO()
                with redirect_stderr(error):
                    exit_code = cmd_doctor(SimpleNamespace())
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 1)
            text = error.getvalue()
            self.assertIn("missing autopilot_runs directory", text)
            self.assertIn("missing git_runs directory", text)
            self.assertIn("missing pr_runs directory", text)
            self.assertIn("missing release_runs directory", text)

    def test_doctor_rejects_enabled_worktree_outside_git_repository(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            cmd_init(SimpleNamespace(path=str(root), adapter="generic", agent_provider="command", agent_command=None))
            config = load_data(root / "harness.yml")
            config["sessions"]["worktree"]["enabled"] = True
            dump_data(config, root / "harness.yml")
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                error = io.StringIO()
                with redirect_stderr(error):
                    exit_code = cmd_doctor(SimpleNamespace())
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 1)
            self.assertIn("worktree is enabled but project root is not a git repository", error.getvalue())

    def test_validate_config_rejects_invalid_session_fields(self) -> None:
        config = {
            "schema_version": 1,
            "project": {"name": "demo"},
            "paths": {"tasks": "harness/tasks", "runs": "harness/runs"},
            "commands": {},
            "policies": {},
            "sessions": {
                "agent_provider": 123,
                "role": ["worker_agent"],
                "launch_command": False,
                "resume_command": 7,
                "provider_options": [],
            },
        }

        errors = validate_config(config)

        self.assertIn("sessions.agent_provider must be a string", errors)
        self.assertIn("sessions.role must be a string", errors)
        self.assertIn("sessions.launch_command must be a string or null", errors)
        self.assertIn("sessions.resume_command must be a string or null", errors)
        self.assertIn("sessions.provider_options must be a mapping", errors)

    def test_validate_config_rejects_invalid_session_timeout(self) -> None:
        config = {
            "schema_version": 1,
            "project": {"name": "demo"},
            "paths": {"tasks": "harness/tasks", "runs": "harness/runs"},
            "commands": {},
            "policies": {},
            "sessions": {
                "agent_provider": "codex",
                "provider_options": {"timeout_seconds": "30"},
            },
        }

        errors = validate_config(config)

        self.assertIn("sessions.provider_options.timeout_seconds must be a positive number", errors)

    def test_validate_config_rejects_invalid_worktree_fields(self) -> None:
        config = {
            "schema_version": 1,
            "project": {"name": "demo"},
            "paths": {"tasks": "harness/tasks", "runs": "harness/runs"},
            "commands": {},
            "policies": {},
            "sessions": {
                "worktree": {
                    "enabled": "yes",
                    "path_template": [],
                }
            },
        }

        errors = validate_config(config)

        self.assertIn("sessions.worktree.enabled must be a boolean", errors)
        self.assertIn("sessions.worktree.path_template must be a string or null", errors)

    def test_validate_config_rejects_invalid_security_provider_command_policy(self) -> None:
        config = {
            "schema_version": 1,
            "project": {"name": "demo"},
            "paths": {"tasks": "harness/tasks", "runs": "harness/runs"},
            "commands": {},
            "policies": {},
            "security": {
                "provider_commands": {
                    "allowlist": "python3",
                    "max_output_bytes": 0,
                    "require_approval_for_irreversible": "yes",
                }
            },
        }

        errors = validate_config(config)

        self.assertIn("security.provider_commands.allowlist must be a list of strings", errors)
        self.assertIn("security.provider_commands.max_output_bytes must be a positive integer", errors)
        self.assertIn("security.provider_commands.require_approval_for_irreversible must be a boolean", errors)

    def test_validate_config_rejects_invalid_capability_provider_fields(self) -> None:
        config = {
            "schema_version": 1,
            "project": {"name": "demo"},
            "paths": {"tasks": "harness/tasks", "runs": "harness/runs"},
            "commands": {},
            "policies": {},
            "capabilities": {
                "planner": {
                    "agent_provider": ["codex"],
                    "command": False,
                    "provider_options": [],
                    "timeout_seconds": 0,
                },
                "reviewer": {
                    "agent_provider": "command",
                    "command": None,
                    "provider_options": {"timeout_seconds": "30"},
                },
            },
        }

        errors = validate_config(config)

        self.assertIn("capabilities.planner.command must be a string or null", errors)
        self.assertIn("capabilities.planner.agent_provider must be a string", errors)
        self.assertIn("capabilities.planner.provider_options must be a mapping", errors)
        self.assertIn("capabilities.planner.timeout_seconds must be a positive number", errors)
        self.assertIn("capabilities.reviewer.provider_options.timeout_seconds must be a positive number", errors)

    def test_validate_config_rejects_invalid_autopilot_loop_policy(self) -> None:
        config = {
            "schema_version": 1,
            "project": {"name": "demo"},
            "paths": {"tasks": "harness/tasks", "runs": "harness/runs"},
            "commands": {},
            "policies": {},
            "autopilot": {
                "default_limit": 0,
                "max_steps": 0,
                "max_loop_cycles": 0,
                "loop_interval_seconds": -1,
                "resources": {
                    "model_concurrency": 0,
                    "max_test_cost": "large",
                    "ci_queue": -1,
                },
            },
        }

        errors = validate_config(config)

        self.assertIn("autopilot.default_limit must be a positive integer or null", errors)
        self.assertIn("autopilot.max_steps must be a positive integer", errors)
        self.assertIn("autopilot.max_loop_cycles must be a positive integer", errors)
        self.assertIn("autopilot.loop_interval_seconds must be a non-negative number", errors)
        self.assertIn("autopilot.resources.model_concurrency must be a positive integer", errors)
        self.assertIn("autopilot.resources.max_test_cost must be a positive integer", errors)
        self.assertIn("autopilot.resources.ci_queue must be a positive integer", errors)

    def test_validate_config_rejects_invalid_context_fields(self) -> None:
        config = {
            "schema_version": 1,
            "project": {"name": "demo"},
            "paths": {"tasks": "harness/tasks", "runs": "harness/runs"},
            "commands": {},
            "policies": {},
            "context": {
                "enabled": "yes",
                "max_tree_entries": 0,
                "max_file_bytes": True,
                "documents": [1],
                "focus_files": {"path": "README.md"},
            },
        }

        errors = validate_config(config)

        self.assertIn("context.enabled must be a boolean", errors)
        self.assertIn("context.max_tree_entries must be a positive integer", errors)
        self.assertIn("context.max_file_bytes must be a positive integer", errors)
        self.assertIn("context.documents must be a string or list of strings", errors)
        self.assertIn("context.focus_files must be a string or list of strings", errors)

    def test_validate_config_rejects_invalid_integration_provider_fields(self) -> None:
        config = {
            "schema_version": 1,
            "project": {"name": "demo"},
            "paths": {
                "tasks": "harness/tasks",
                "runs": "harness/runs",
                "ci_runs": 123,
                "git_runs": {},
                "pr_runs": False,
                "release_runs": [],
            },
            "commands": {},
            "policies": {},
            "integrations": {
                "ci_provider": {
                    "provider": ["github-actions"],
                    "command": False,
                    "provider_options": [],
                    "timeout_seconds": 0,
                },
                "git_provider": {
                    "provider": ["git"],
                    "command": False,
                    "provider_options": [],
                    "timeout_seconds": 0,
                },
                "pr_provider": {
                    "provider": ["github"],
                    "command": False,
                    "provider_options": {"timeout_seconds": "30"},
                },
                "release_provider": {
                    "provider": ["release"],
                    "command": False,
                    "provider_options": [],
                    "timeout_seconds": -1,
                }
            },
        }

        errors = validate_config(config)

        self.assertIn("paths.ci_runs must be a string", errors)
        self.assertIn("paths.git_runs must be a string", errors)
        self.assertIn("paths.pr_runs must be a string", errors)
        self.assertIn("paths.release_runs must be a string", errors)
        self.assertIn("integrations.git_provider.provider must be a string", errors)
        self.assertIn("integrations.git_provider.command must be a string or null", errors)
        self.assertIn("integrations.git_provider.provider_options must be a mapping", errors)
        self.assertIn("integrations.git_provider.timeout_seconds must be a positive number", errors)
        self.assertIn("integrations.ci_provider.provider must be a string", errors)
        self.assertIn("integrations.ci_provider.command must be a string or null", errors)
        self.assertIn("integrations.ci_provider.provider_options must be a mapping", errors)
        self.assertIn("integrations.ci_provider.timeout_seconds must be a positive number", errors)
        self.assertIn("integrations.pr_provider.provider must be a string", errors)
        self.assertIn("integrations.pr_provider.command must be a string or null", errors)
        self.assertIn("integrations.pr_provider.provider_options.timeout_seconds must be a positive number", errors)
        self.assertIn("integrations.release_provider.provider must be a string", errors)
        self.assertIn("integrations.release_provider.command must be a string or null", errors)
        self.assertIn("integrations.release_provider.provider_options must be a mapping", errors)
        self.assertIn("integrations.release_provider.timeout_seconds must be a positive number", errors)

    def test_run_verification_uses_configured_commands_and_skips_nulls(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = {
                "commands": {
                    "bdd": "python3 -c 'print(\"bdd ok\")'",
                    "unit": "python3 -c 'print(\"unit ok\")'",
                    "lint": None,
                    "typecheck": None,
                    "secret_scan": None,
                    "project_verify": None,
                }
            }

            result = run_verification(root, config, root / "verify-logs")

            self.assertEqual(result.failed, [])
            self.assertEqual([item.name for item in result.results], ["bdd", "unit"])
            self.assertTrue((root / "verify-logs" / "bdd.log").exists())


if __name__ == "__main__":
    unittest.main()
