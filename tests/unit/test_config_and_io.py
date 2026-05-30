from contextlib import redirect_stderr
import io
from pathlib import Path
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

    def test_init_template_does_not_advertise_external_skills(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)

            exit_code = cmd_init(SimpleNamespace(path=str(root), adapter="generic", agent_provider="command", agent_command=None))
            config = load_data(root / "harness.yml")

            self.assertEqual(exit_code, 0)
            self.assertNotIn("skills", config.get("integrations", {}))
            self.assertEqual(config["sessions"]["agent_provider"], "command")
            self.assertEqual(config["sessions"]["provider_options"], {})
            self.assertNotIn("provider", config["sessions"])
            self.assertEqual(config["capabilities"]["planner"]["agent_provider"], "command")

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
