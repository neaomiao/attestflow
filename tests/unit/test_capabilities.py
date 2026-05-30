from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import attestflow.cli as cli
from attestflow.capabilities import get_capability, list_capabilities
from attestflow.io import load_data


class CapabilityTests(unittest.TestCase):
    def test_builtin_capabilities_define_professional_contracts(self) -> None:
        capabilities = {item["name"]: item for item in list_capabilities()}

        self.assertIn("planner", capabilities)
        self.assertIn("reviewer", capabilities)
        self.assertIn("verifier", capabilities)
        for capability in capabilities.values():
            self.assertEqual(capability["external_dependency"], False)
            for key in ("name", "specialist", "phase", "description", "inputs", "outputs", "gates", "evidence"):
                self.assertIn(key, capability)
                self.assertTrue(capability[key])

    def test_get_capability_rejects_unknown_names(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown capability"):
            get_capability("missing")

    def test_cli_capability_commands_expose_builtin_contracts(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = cli.main(["capability", "list"])

        self.assertEqual(exit_code, 0)
        self.assertIn("planner", output.getvalue())
        self.assertIn("reviewer", output.getvalue())

        detail = io.StringIO()
        with redirect_stdout(detail):
            exit_code = cli.main(["capability", "show", "planner"])

        self.assertEqual(exit_code, 0)
        planner = json.loads(detail.getvalue())
        self.assertEqual(planner["name"], "planner")
        self.assertEqual(planner["external_dependency"], False)

    def test_cli_plan_runs_command_provider_and_imports_runtime_tasks(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = root / "planner_provider.py"
            provider.write_text(
                """
import json
import sys

json.load(sys.stdin)
json.dump(
    {
        "schema_version": 1,
        "tasks": [
            {
                "title": "Plan capability task",
                "priority": 10,
                "type": "feature",
                "purpose": "Prove plan command imports model output.",
                "scope": ["plan command"],
                "out_of_scope": ["model SDK"],
                "requirements": {
                    "confirmed": ["command provider returns planner JSON"],
                    "unresolved": [],
                    "assumptions": [],
                },
                "bdd_scenarios": ["plan imports generated tasks"],
                "unit_tests": ["tests/unit/test_capabilities.py"],
                "acceptance": ["runtime task JSON exists"],
                "files": {"read": ["README.md"], "write": ["attestflow/capabilities.py"]},
            }
        ],
    },
    sys.stdout,
)
""".lstrip(),
                encoding="utf-8",
            )
            command = f"python3 {provider}"
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(["plan", "Add internal planner capability", "--command", command])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 0)
            self.assertIn("planned and imported 1 task(s): TASK-0001", output.getvalue())
            task = load_data(root / "harness" / "tasks" / "ready" / "TASK-0001.json")
            self.assertEqual(task["title"], "Plan capability task")
            runs = list((root / "harness" / "capability-runs").glob("planner-*"))
            self.assertEqual(len(runs), 1)
            self.assertTrue((runs[0] / "input.json").exists())
            self.assertTrue((runs[0] / "output.json").exists())

    def test_cli_plan_requires_a_planner_command(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                error = io.StringIO()
                with redirect_stderr(error):
                    exit_code = cli.main(["plan", "Add login"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 1)
            self.assertIn("capabilities.planner.command", error.getvalue())


if __name__ == "__main__":
    unittest.main()
