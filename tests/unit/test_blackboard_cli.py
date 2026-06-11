from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from attestflow import cli
from attestflow.io import dump_data


class BlackboardCliTests(unittest.TestCase):
    def test_cli_post_list_show_and_resolve_json(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_task(root, "TASK-0001")

            post_exit, post_stdout, _ = _run_cli(
                root,
                [
                    "blackboard",
                    "post",
                    "--from-role",
                    "reviewer",
                    "--to-role",
                    "implementer",
                    "--type",
                    "finding",
                    "--task",
                    "TASK-0001",
                    "--requires-response",
                    "--body",
                    "Missing retry boundary.",
                    "--json",
                ],
            )

            self.assertEqual(post_exit, 0)
            posted = json.loads(post_stdout)
            self.assertEqual(posted["message_id"], "MSG-0001")
            self.assertEqual(posted["status"], "open")
            self.assertEqual(posted["task_id"], "TASK-0001")

            list_exit, list_stdout, _ = _run_cli(root, ["blackboard", "list", "--status", "open", "--json"])
            self.assertEqual(list_exit, 0)
            listed = json.loads(list_stdout)
            self.assertEqual([item["message_id"] for item in listed], ["MSG-0001"])

            resolve_exit, resolve_stdout, _ = _run_cli(
                root,
                [
                    "blackboard",
                    "resolve",
                    "MSG-0001",
                    "--from-role",
                    "implementer",
                    "--body",
                    "Added retry boundary.",
                    "--json",
                ],
            )
            self.assertEqual(resolve_exit, 0)
            resolved = json.loads(resolve_stdout)
            self.assertEqual(resolved["status"], "resolved")
            self.assertEqual(resolved["body"], "Added retry boundary.")

            show_exit, show_stdout, _ = _run_cli(root, ["blackboard", "show", "MSG-0001", "--events", "--json"])
            self.assertEqual(show_exit, 0)
            shown = json.loads(show_stdout)
            self.assertEqual(shown["status"], "resolved")
            self.assertEqual([event["event_type"] for event in shown["events"]], ["post", "resolve"])

    def test_cli_list_outputs_readable_table(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _run_cli(
                root,
                [
                    "blackboard",
                    "post",
                    "--from-role",
                    "planner",
                    "--type",
                    "decision",
                    "--body",
                    "Use append-only JSONL.",
                ],
            )

            exit_code, stdout, _ = _run_cli(root, ["blackboard", "list"])

            self.assertEqual(exit_code, 0)
            self.assertIn("MSG-0001\topen\tTHREAD-0001\tplanner\tdecision\tUse append-only JSONL.", stdout)

    def test_cli_invalid_input_returns_error_without_traceback(self) -> None:
        with TemporaryDirectory() as tmp:
            exit_code, _, stderr = _run_cli(
                Path(tmp),
                ["blackboard", "post", "--from-role", "reviewer", "--task", "TASK-9999", "--body", "Bad task."],
            )

            self.assertEqual(exit_code, 1)
            self.assertIn("ERROR: unknown task id: TASK-9999", stderr)
            self.assertNotIn("Traceback", stderr)


def _run_cli(root: Path, argv: list[str]) -> tuple[int, str, str]:
    original_root = cli.ROOT
    cli.ROOT = root
    stdout = StringIO()
    stderr = StringIO()
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = cli.main(argv)
    finally:
        cli.ROOT = original_root
    return exit_code, stdout.getvalue(), stderr.getvalue()


def _write_task(root: Path, task_id: str) -> None:
    dump_data(
        {
            "schema_version": 1,
            "id": task_id,
            "title": "Task",
            "state": "ready",
            "priority": 1,
            "type": "feature",
            "purpose": "Exercise blackboard CLI task validation.",
            "context": [],
            "scope": ["blackboard"],
            "out_of_scope": [],
            "requirements": {"confirmed": ["task exists"], "unresolved": [], "assumptions": []},
            "bdd_scenarios": ["Message can reference a task."],
            "unit_tests": ["tests/unit/test_blackboard_cli.py"],
            "acceptance": ["task-scoped message is accepted"],
            "dependencies": [],
            "blocks": [],
            "blockers": [],
            "files": {"read": [], "write": ["attestflow/cli.py"]},
            "agents": {"owner": "orchestrator", "allowed_roles": []},
            "external_inputs": {"credentials": [], "services": [], "user_decisions": []},
            "evidence": {"session": None, "run_id": None, "red": None, "green": None, "verify": None, "packet": None},
            "links": {"issues": [], "prs": [], "docs": []},
            "risks": [],
            "notes": [],
            "created_at": "2026-06-11T00:00:00+00:00",
            "updated_at": "2026-06-11T00:00:00+00:00",
        },
        root / "harness/tasks/ready" / f"{task_id}.json",
    )


if __name__ == "__main__":
    unittest.main()
