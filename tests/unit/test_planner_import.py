from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import attestflow.cli as cli
from attestflow.config import DEFAULT_CONFIG
from attestflow.io import dump_data, load_data
from attestflow.planner import import_planner_tasks
from tests.unit.test_task_lifecycle import completed_task, ready_task, write_task


class PlannerImportTests(unittest.TestCase):
    def test_import_planner_tasks_assigns_ids_and_resolves_local_dependencies(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            config["root"] = root
            write_task(root, "done", "TASK-0001", completed_task(ready_task("TASK-0001")))
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

            spec_path = _write_approved_spec(root)

            records = import_planner_tasks(root, config, plan, approved_spec_path=spec_path)

            self.assertEqual([record.task["id"] for record in records], ["TASK-0002", "TASK-0003"])
            first = load_data(root / "harness" / "tasks" / "ready" / "TASK-0002.json")
            second = load_data(root / "harness" / "tasks" / "ready" / "TASK-0003.json")
            self.assertEqual(first["state"], "ready")
            self.assertEqual(first["agents"]["owner"], "orchestrator")
            self.assertEqual(first["blockers"], [])
            self.assertEqual(second["dependencies"], ["TASK-0002"])
            self.assertEqual(second["evidence"]["run_id"], None)

    def test_import_planner_tasks_requires_approved_spec_provenance(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            config["root"] = root

            with self.assertRaisesRegex(ValueError, "planner import requires approved spec provenance"):
                import_planner_tasks(root, config, {"schema_version": 1, "tasks": [{"title": "Unsafe"}]})

    def test_import_planner_tasks_allows_internal_controlled_provenance(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            config["root"] = root
            plan = _valid_plan()

            records = import_planner_tasks(
                root,
                config,
                plan,
                allow_unapproved=True,
                provenance_label="internal_release_repair",
            )

            self.assertEqual([record.task["id"] for record in records], ["TASK-0001"])

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
                import_planner_tasks(root, config, plan, approved_spec_path=_write_approved_spec(root))

            self.assertFalse((root / "harness" / "tasks" / "ready" / "TASK-0001.json").exists())

    def test_cli_task_import_requires_approved_spec(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan_path = root / "plan.json"
            plan_path.write_text('{"schema_version": 1, "tasks": [{"title": "Unsafe"}]}\n', encoding="utf-8")
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                error = io.StringIO()
                with redirect_stderr(error):
                    exit_code = cli.main(["task", "import", "--from-json", str(plan_path)])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 1)
            self.assertIn("task import requires --from-spec SPEC-####/spec.md --approve", error.getvalue())

    def test_cli_task_import_rejects_approved_spec_outside_specs_dir(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan_path = root / "plan.json"
            plan_path.write_text('{"schema_version": 1, "tasks": [{"title": "Unsafe"}]}\n', encoding="utf-8")
            spec_path = root / "external" / "SPEC-0001" / "spec.md"
            spec_path.parent.mkdir(parents=True)
            spec_path.write_text(
                "# SPEC-0001: Login\n\n## Goal\nShip login.\n\n## Acceptance Criteria\n- Login works.\n\n## Open Questions\n- None\n",
                encoding="utf-8",
            )
            dump_data(
                {
                    "schema_version": 1,
                    "spec_id": "SPEC-0001",
                    "status": "approved",
                    "approved_by": "alice",
                    "approved_at": "2026-06-10T00:00:00+00:00",
                },
                spec_path.parent / "approval.json",
            )
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                error = io.StringIO()
                with redirect_stderr(error):
                    exit_code = cli.main(
                        ["task", "import", "--from-json", str(plan_path), "--from-spec", str(spec_path), "--approve"]
                    )
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 1)
            self.assertIn("spec path must be under configured specs directory", error.getvalue())

    def test_cli_task_import_reads_planner_json_file_with_approved_spec(self) -> None:
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
                spec_path = _write_approved_spec(root)
                with redirect_stdout(output):
                    exit_code = cli.main(
                        ["task", "import", "--from-json", str(plan_path), "--from-spec", str(spec_path), "--approve"]
                    )
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 0)
            self.assertIn("imported 1 task(s): TASK-0001", output.getvalue())
            task = load_data(root / "harness" / "tasks" / "ready" / "TASK-0001.json")
            self.assertEqual(task["title"], "Import planner JSON")

    def test_cli_task_import_reports_invalid_planner_json_without_traceback(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan_path = root / "invalid-plan.json"
            plan_path.write_text('{"schema_version": 1, "tasks": []}\n', encoding="utf-8")
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                error = io.StringIO()
                with redirect_stderr(error):
                    exit_code = cli.main(
                        [
                            "task",
                            "import",
                            "--from-json",
                            str(plan_path),
                            "--from-spec",
                            str(_write_approved_spec(root)),
                            "--approve",
                        ]
                    )
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 1)
            self.assertIn("ERROR: planner output must include a non-empty tasks list", error.getvalue())
            self.assertNotIn("Traceback", error.getvalue())
            self.assertEqual(list((root / "harness" / "tasks" / "ready").glob("TASK-*.json")), [])


def _write_approved_spec(root: Path, spec_id: str = "SPEC-0001") -> Path:
    spec = root / "harness/specs" / spec_id / "spec.md"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(
        f"# {spec_id}: Login\n\n## Goal\nShip login.\n\n## Acceptance Criteria\n- Login works.\n\n## Open Questions\n- None\n",
        encoding="utf-8",
    )
    dump_data(
        {
            "schema_version": 1,
            "spec_id": spec_id,
            "status": "approved",
            "approved_by": "alice",
            "approved_at": "2026-06-10T00:00:00+00:00",
        },
        spec.parent / "approval.json",
    )
    return spec


def _valid_plan() -> dict:
    return {
        "schema_version": 1,
        "tasks": [
            {
                "title": "Import planner JSON",
                "priority": 10,
                "type": "feature",
                "purpose": "Expose planner import.",
                "scope": ["planner import"],
                "out_of_scope": ["raw source intake"],
                "requirements": {"confirmed": ["approved spec exists"], "unresolved": [], "assumptions": []},
                "bdd_scenarios": ["CLI imports planner JSON."],
                "unit_tests": ["tests/unit/test_planner_import.py"],
                "acceptance": ["ready task file exists"],
                "files": {"write": ["attestflow/cli.py"]},
            }
        ],
    }


if __name__ == "__main__":
    unittest.main()
