from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import attestflow.cli as cli
from attestflow.capabilities import get_capability, list_capabilities, run_task_capability
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

    def test_task_capability_runner_records_output_and_updates_task_evidence(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_ready_task(root, "TASK-0001")
            provider = root / "review_provider.py"
            provider.write_text(
                """
import json
import sys

payload = json.load(sys.stdin)
assert payload["capability"]["name"] == "reviewer"
assert payload["task"]["id"] == "TASK-0001"
json.dump(
    {
        "schema_version": 1,
        "status": "passed",
        "summary": "No blocking issues.",
        "findings": [],
        "evidence": ["review report"],
    },
    sys.stdout,
)
""".lstrip(),
                encoding="utf-8",
            )
            config = {"paths": {"tasks": "harness/tasks", "capability_runs": "harness/capability-runs"}}

            result = run_task_capability(root, config, "reviewer", "TASK-0001", command=f"python3 {provider}")

            self.assertEqual(result.capability, "reviewer")
            self.assertEqual(result.task_id, "TASK-0001")
            self.assertEqual(result.output["status"], "passed")
            task = load_data(root / "harness" / "tasks" / "ready" / "TASK-0001.json")
            self.assertTrue(task["evidence"]["capabilities"]["reviewer"].endswith("output.json"))
            self.assertTrue((result.run_path / "input.json").exists())
            self.assertTrue((result.run_path / "output.json").exists())

    def test_cli_capability_run_executes_task_scoped_capability(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_ready_task(root, "TASK-0001")
            provider = root / "bdd_provider.py"
            provider.write_text(
                """
import json
import sys

json.load(sys.stdin)
json.dump(
    {
        "schema_version": 1,
        "status": "passed",
        "summary": "BDD scenarios are adequate.",
        "findings": [],
        "evidence": ["scenario review"],
    },
    sys.stdout,
)
""".lstrip(),
                encoding="utf-8",
            )
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(["capability", "run", "bdd", "TASK-0001", "--command", f"python3 {provider}"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 0)
            self.assertIn("ran bdd for TASK-0001", output.getvalue())
            task = load_data(root / "harness" / "tasks" / "ready" / "TASK-0001.json")
            self.assertIn("bdd", task["evidence"]["capabilities"])

    def test_cli_capability_run_rejects_planner(self) -> None:
        error = io.StringIO()
        with redirect_stderr(error):
            exit_code = cli.main(["capability", "run", "planner", "TASK-0001", "--command", "printf '{}'"])

        self.assertEqual(exit_code, 1)
        self.assertIn("use attestflow plan", error.getvalue())


def write_ready_task(root: Path, task_id: str) -> None:
    path = root / "harness" / "tasks" / "ready" / f"{task_id}.json"
    task = {
        "schema_version": 1,
        "id": task_id,
        "title": "Task capability fixture",
        "state": "ready",
        "priority": 10,
        "type": "feature",
        "purpose": "Exercise task scoped capabilities.",
        "context": [],
        "scope": ["capability runner"],
        "out_of_scope": ["model SDK"],
        "requirements": {"confirmed": ["task is ready"], "unresolved": [], "assumptions": []},
        "bdd_scenarios": ["Task capability receives task context."],
        "unit_tests": ["tests/unit/test_capabilities.py"],
        "acceptance": ["capability evidence is recorded"],
        "dependencies": [],
        "blocks": [],
        "files": {"read": ["README.md"], "write": ["attestflow/capabilities.py"]},
        "agents": {"owner": "orchestrator", "allowed_roles": ["worker_agent"]},
        "external_inputs": {"credentials": [], "services": [], "user_decisions": []},
        "evidence": {"session": None, "run_id": None, "red": None, "green": None, "verify": None, "packet": None},
        "links": {"issues": [], "prs": [], "docs": []},
        "risks": [],
        "notes": [],
        "created_at": "2026-05-30T00:00:00Z",
        "updated_at": "2026-05-30T00:00:00Z",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(task, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
