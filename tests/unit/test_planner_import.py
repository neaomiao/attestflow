from contextlib import redirect_stdout
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import attestflow.cli as cli
from attestflow.config import DEFAULT_CONFIG
from attestflow.io import load_data
from attestflow.planner import import_planner_tasks
from tests.unit.test_task_lifecycle import ready_task, write_task


class PlannerImportTests(unittest.TestCase):
    def test_import_planner_tasks_assigns_ids_and_resolves_local_dependencies(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            config["root"] = root
            write_task(root, "done", "TASK-0001", ready_task("TASK-0001"))
            plan = {
                "schema_version": 1,
                "goal": "Improve AI-first planning.",
                "tasks": [
                    {
                        "key": "planner_contract",
                        "title": "Add planner output contract",
                        "priority": 10,
                        "type": "docs",
                        "purpose": "Document the LLM output shape.",
                        "scope": ["planner JSON schema"],
                        "out_of_scope": ["programming agent provider presets"],
                        "requirements": {"confirmed": ["AI creates task drafts"], "unresolved": [], "assumptions": []},
                        "bdd_scenarios": ["Planner output can be imported."],
                        "unit_tests": ["tests/unit/test_planner_import.py"],
                        "acceptance": ["planner contract is documented"],
                        "files": {"read": ["README.md"], "write": ["docs/contracts/planner-output-schema.md"]},
                    },
                    {
                        "key": "planner_import",
                        "title": "Import planner tasks",
                        "priority": 20,
                        "type": "feature",
                        "purpose": "Let AI plans become validated task files.",
                        "scope": ["planner import command"],
                        "out_of_scope": ["calling a programming agent provider"],
                        "requirements": {"confirmed": ["import must validate tasks"], "unresolved": [], "assumptions": []},
                        "bdd_scenarios": ["Planner JSON imports ready tasks."],
                        "unit_tests": ["tests/unit/test_planner_import.py"],
                        "acceptance": ["import writes ready task JSON"],
                        "dependencies": ["planner_contract"],
                        "files": {"read": ["attestflow/tasks.py"], "write": ["attestflow/planner.py"]},
                    },
                ],
            }

            records = import_planner_tasks(root, config, plan)

            self.assertEqual([record.task["id"] for record in records], ["TASK-0002", "TASK-0003"])
            first = load_data(root / "harness" / "tasks" / "ready" / "TASK-0002.json")
            second = load_data(root / "harness" / "tasks" / "ready" / "TASK-0003.json")
            self.assertEqual(first["state"], "ready")
            self.assertEqual(first["agents"]["owner"], "orchestrator")
            self.assertEqual(second["dependencies"], ["TASK-0002"])
            self.assertEqual(second["evidence"]["run_id"], None)

    def test_import_planner_tasks_rejects_incomplete_ready_tasks(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            config["root"] = root
            plan = {
                "schema_version": 1,
                "tasks": [
                    {
                        "title": "Incomplete task",
                        "priority": 10,
                        "files": {"write": ["attestflow/planner.py"]},
                    }
                ],
            }

            with self.assertRaisesRegex(ValueError, "scope must be a non-empty list"):
                import_planner_tasks(root, config, plan)

            self.assertFalse((root / "harness" / "tasks" / "ready" / "TASK-0001.json").exists())

    def test_cli_task_import_reads_planner_json_file(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan_path = root / "plan.json"
            plan_path.write_text(
                """
{
  "schema_version": 1,
  "tasks": [
    {
      "title": "Import planner JSON",
      "priority": 10,
      "type": "feature",
      "purpose": "Expose planner import through CLI.",
      "scope": ["CLI task import"],
      "out_of_scope": ["programming agent provider calls"],
      "requirements": {"confirmed": ["CLI reads JSON"], "unresolved": [], "assumptions": []},
      "bdd_scenarios": ["CLI imports planner JSON."],
      "unit_tests": ["tests/unit/test_planner_import.py"],
      "acceptance": ["ready task file exists"],
      "files": {"write": ["attestflow/cli.py"]}
    }
  ]
}
""".strip()
                + "\n",
                encoding="utf-8",
            )
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(["task", "import", "--from-json", str(plan_path)])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 0)
            self.assertIn("imported 1 task(s): TASK-0001", output.getvalue())
            task = load_data(root / "harness" / "tasks" / "ready" / "TASK-0001.json")
            self.assertEqual(task["title"], "Import planner JSON")


if __name__ == "__main__":
    unittest.main()
