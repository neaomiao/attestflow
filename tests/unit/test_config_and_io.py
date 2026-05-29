from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

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
