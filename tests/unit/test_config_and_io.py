from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from attestflow.cli import cmd_init
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

            exit_code = cmd_init(SimpleNamespace(path=str(root), adapter="generic"))
            config = load_data(root / "harness.yml")

            self.assertEqual(exit_code, 0)
            self.assertNotIn("skills", config.get("integrations", {}))
            self.assertEqual(config["sessions"]["agent_provider"], "command")
            self.assertNotIn("provider", config["sessions"])
            self.assertEqual(config["capabilities"]["planner"]["agent_provider"], "command")

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
            },
        }

        errors = validate_config(config)

        self.assertIn("sessions.agent_provider must be a string", errors)
        self.assertIn("sessions.role must be a string", errors)
        self.assertIn("sessions.launch_command must be a string or null", errors)
        self.assertIn("sessions.resume_command must be a string or null", errors)

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
