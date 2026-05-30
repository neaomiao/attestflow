from contextlib import redirect_stdout
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import attestflow.cli as cli
from attestflow.config import DEFAULT_CONFIG
from attestflow.evidence import record_verification_results
from attestflow.io import dump_data, load_data
from attestflow.runner import CommandResult, VerificationResult
from attestflow.resume import resume_summary
from attestflow.tasks import (
    block_task,
    close_task,
    select_dispatchable_tasks,
    select_next_task,
    start_task,
    transition_task,
    unblock_task,
    validate_task,
    verify_task,
)


def write_task(root: Path, state: str, task_id: str, data: dict) -> Path:
    task_dir = root / "harness" / "tasks" / state
    task_dir.mkdir(parents=True, exist_ok=True)
    path = task_dir / f"{task_id}.json"
    dump_data(data, path)
    return path


def ready_task(task_id: str, priority: int = 10) -> dict:
    return {
        "schema_version": 1,
        "id": task_id,
        "title": "Add validator",
        "state": "ready",
        "priority": priority,
        "type": "feature",
        "purpose": "Validate tasks before execution.",
        "context": [],
        "scope": ["task validation"],
        "out_of_scope": ["business code"],
        "requirements": {"confirmed": ["needs tests"], "unresolved": [], "assumptions": []},
        "bdd_scenarios": ["Ready task without BDD is rejected."],
        "unit_tests": ["tests/unit/test_task_lifecycle.py"],
        "acceptance": ["validator rejects incomplete ready tasks"],
        "dependencies": [],
        "blocks": [],
        "blockers": [],
        "files": {"read": [], "write": ["attestflow/tasks.py"]},
        "agents": {"owner": "orchestrator", "allowed_roles": ["worker_agent"]},
        "external_inputs": {"credentials": [], "services": [], "user_decisions": []},
        "evidence": {"session": None, "run_id": None, "red": None, "green": None, "verify": None, "packet": None},
        "links": {"issues": [], "prs": [], "docs": []},
        "risks": [],
        "notes": [],
        "created_at": "2026-05-29T00:00:00Z",
        "updated_at": "2026-05-29T00:00:00Z",
    }


class TaskLifecycleTests(unittest.TestCase):
    def test_ready_task_requires_bdd_unit_acceptance_and_write_scope(self) -> None:
        task = ready_task("TASK-0001")
        task["bdd_scenarios"] = []
        task["unit_tests"] = []
        task["acceptance"] = []
        task["files"] = {"read": [], "write": []}

        errors = validate_task(task, directory_state="ready")

        self.assertIn("bdd_scenarios must be a non-empty list when state is ready", errors)
        self.assertIn("unit_tests must be a non-empty list when state is ready", errors)
        self.assertIn("acceptance must be a non-empty list when state is ready", errors)
        self.assertIn("files.write must be a non-empty list when state is ready", errors)

    def test_select_next_uses_priority_and_dependency_completion(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            config["root"] = root
            write_task(root, "ready", "TASK-0002", ready_task("TASK-0002", priority=20))
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001", priority=5))

            selected = select_next_task(root, config)

            self.assertIsNotNone(selected)
            self.assertEqual(selected.task["id"], "TASK-0001")

    def test_start_task_moves_file_creates_locks_and_run_metadata(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            config["root"] = root
            source = write_task(root, "ready", "TASK-0001", ready_task("TASK-0001"))

            run = start_task(root, config, "TASK-0001", actor_role="orchestrator")

            self.assertFalse(source.exists())
            active = root / "harness" / "tasks" / "in_progress" / "TASK-0001.json"
            self.assertTrue(active.exists())
            active_task = load_data(active)
            self.assertEqual(active_task["state"], "in_progress")
            self.assertEqual(active_task["evidence"]["run_id"], run.run_id)
            self.assertTrue((root / "harness" / "locks" / "tasks" / "TASK-0001.lock").exists())
            self.assertTrue((run.path / "metadata.yml").exists())
            self.assertTrue((run.path / "ledger.jsonl").exists())

    def test_resume_reports_active_run_next_action(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            config["root"] = root
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001"))
            start_task(root, config, "TASK-0001", actor_role="orchestrator")

            summary = resume_summary(root, config)

            self.assertIn("TASK-0001", summary)
            self.assertIn("run BDD", summary)

    def test_block_task_moves_to_blocked_and_records_reason(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            config["root"] = root
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001"))

            blocked = block_task(
                root,
                config,
                "TASK-0001",
                reason="missing API credentials",
                unblock_condition="Set API_TOKEN in the target environment.",
                owner="user",
                blocker_type="credential",
                source="cli",
            )

            self.assertEqual(blocked.task["state"], "blocked")
            self.assertEqual(blocked.task["blockers"][0]["id"], "BLK-0001")
            self.assertEqual(blocked.task["blockers"][0]["status"], "active")
            self.assertEqual(blocked.task["blockers"][0]["reason"], "missing API credentials")
            self.assertEqual(blocked.task["blockers"][0]["unblock_condition"], "Set API_TOKEN in the target environment.")
            self.assertEqual(blocked.task["blockers"][0]["owner"], "user")
            self.assertEqual(blocked.task["blockers"][0]["type"], "credential")
            self.assertEqual(blocked.task["blockers"][0]["source"], "cli")
            self.assertTrue((root / "harness" / "tasks" / "blocked" / "TASK-0001.json").exists())

    def test_blocked_task_requires_active_structured_blocker(self) -> None:
        task = ready_task("TASK-0001")
        task["state"] = "blocked"

        errors = validate_task(task, directory_state="blocked")

        self.assertIn("blocked task must have at least one active blocker", errors)

    def test_ready_task_with_external_inputs_is_not_executable(self) -> None:
        task = ready_task("TASK-0001")
        task["external_inputs"]["credentials"] = ["API_TOKEN"]

        errors = validate_task(task, directory_state="ready")

        self.assertIn("external_inputs must be empty when state is ready; move task to blocked until inputs exist", errors)

    def test_select_next_skips_ready_tasks_with_active_blockers_or_external_inputs(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            config["root"] = root
            blocked_ready = ready_task("TASK-0001", priority=1)
            blocked_ready["blockers"] = [
                {
                    "id": "BLK-0001",
                    "type": "credential",
                    "reason": "missing API token",
                    "unblock_condition": "Set API_TOKEN.",
                    "owner": "user",
                    "source": "planner",
                    "status": "active",
                    "created_at": "2026-05-30T00:00:00Z",
                    "resolved_at": None,
                }
            ]
            external_input_ready = ready_task("TASK-0002", priority=2)
            external_input_ready["external_inputs"]["services"] = ["staging database"]
            write_task(root, "ready", "TASK-0001", blocked_ready)
            write_task(root, "ready", "TASK-0002", external_input_ready)
            write_task(root, "ready", "TASK-0003", ready_task("TASK-0003", priority=3))

            selected = select_next_task(root, config)

            self.assertIsNotNone(selected)
            self.assertEqual(selected.task["id"], "TASK-0003")

    def test_select_dispatchable_tasks_skips_dependencies_locks_and_batch_write_conflicts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            config["root"] = root
            done = ready_task("TASK-0000", priority=1)
            done["state"] = "done"
            write_task(root, "done", "TASK-0000", done)
            first = ready_task("TASK-0001", priority=1)
            first["files"]["write"] = ["src/a.py"]
            second = ready_task("TASK-0002", priority=2)
            second["dependencies"] = ["TASK-0000"]
            second["files"]["write"] = ["src/b.py"]
            same_write_scope = ready_task("TASK-0003", priority=3)
            same_write_scope["files"]["write"] = ["src/a.py"]
            missing_dependency = ready_task("TASK-0004", priority=4)
            missing_dependency["dependencies"] = ["TASK-9999"]
            locked = ready_task("TASK-0005", priority=5)
            locked["files"]["write"] = ["src/locked.py"]
            write_task(root, "ready", "TASK-0001", first)
            write_task(root, "ready", "TASK-0002", second)
            write_task(root, "ready", "TASK-0003", same_write_scope)
            write_task(root, "ready", "TASK-0004", missing_dependency)
            write_task(root, "ready", "TASK-0005", locked)
            (root / "harness" / "locks" / "files").mkdir(parents=True)
            (root / "harness" / "locks" / "files" / "src.locked.py.lock").write_text("TASK-9998\n", encoding="utf-8")

            selected = select_dispatchable_tasks(root, config, limit=10)

            self.assertEqual([record.task["id"] for record in selected], ["TASK-0001", "TASK-0002"])

    def test_unblock_resolves_blocker_and_returns_task_to_ready(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            config["root"] = root
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001"))
            block_task(
                root,
                config,
                "TASK-0001",
                reason="missing API credentials",
                unblock_condition="Set API_TOKEN in the target environment.",
                owner="user",
                blocker_type="credential",
                source="cli",
            )

            unblocked = unblock_task(
                root,
                config,
                "TASK-0001",
                "BLK-0001",
                resolution="API_TOKEN configured in CI.",
            )

            self.assertEqual(unblocked.task["state"], "ready")
            self.assertEqual(unblocked.task["blockers"][0]["status"], "resolved")
            self.assertEqual(unblocked.task["blockers"][0]["resolution"], "API_TOKEN configured in CI.")
            self.assertIsNotNone(unblocked.task["blockers"][0]["resolved_at"])
            self.assertTrue((root / "harness" / "tasks" / "ready" / "TASK-0001.json").exists())

    def test_unblock_clears_resolved_external_inputs_before_ready(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            config["root"] = root
            task = ready_task("TASK-0001")
            task["state"] = "blocked"
            task["external_inputs"]["credentials"] = ["API_TOKEN"]
            task["blockers"] = [
                {
                    "id": "BLK-0001",
                    "type": "credential",
                    "reason": "missing API_TOKEN",
                    "unblock_condition": "Set API_TOKEN.",
                    "owner": "user",
                    "source": "planner",
                    "status": "active",
                    "created_at": "2026-05-30T00:00:00Z",
                    "resolved_at": None,
                }
            ]
            write_task(root, "blocked", "TASK-0001", task)

            unblocked = unblock_task(root, config, "TASK-0001", "BLK-0001", resolution="API_TOKEN configured.")

            self.assertEqual(unblocked.task["state"], "ready")
            self.assertEqual(unblocked.task["external_inputs"], {"credentials": [], "services": [], "user_decisions": []})
            self.assertEqual(validate_task(unblocked.task, directory_state="ready"), [])

    def test_transition_cannot_move_blocked_task_to_ready_with_active_blocker(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            config["root"] = root
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001"))
            block_task(
                root,
                config,
                "TASK-0001",
                reason="missing API credentials",
                unblock_condition="Set API_TOKEN in the target environment.",
                owner="user",
                blocker_type="credential",
                source="cli",
            )

            with self.assertRaisesRegex(ValueError, "active blockers require state blocked"):
                transition_task(root, config, "TASK-0001", "ready")

    def test_cli_unblock_resolves_structured_blocker(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            config["root"] = root
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001"))
            block_task(
                root,
                config,
                "TASK-0001",
                reason="missing API credentials",
                unblock_condition="Set API_TOKEN in the target environment.",
                owner="user",
                blocker_type="credential",
                source="cli",
            )
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(
                        [
                            "unblock",
                            "TASK-0001",
                            "--blocker",
                            "BLK-0001",
                            "--resolution",
                            "API_TOKEN configured.",
                        ]
                    )
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 0)
            self.assertIn("unblocked TASK-0001", output.getvalue())
            task = load_data(root / "harness" / "tasks" / "ready" / "TASK-0001.json")
            self.assertEqual(task["blockers"][0]["status"], "resolved")

    def test_close_task_requires_accepted_state_and_releases_locks(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            config["root"] = root
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001"))
            run = start_task(root, config, "TASK-0001", actor_role="orchestrator")
            transition_task(root, config, "TASK-0001", "review")
            transition_task(root, config, "TASK-0001", "verified")
            transition_task(root, config, "TASK-0001", "accepted")

            with self.assertRaisesRegex(ValueError, "missing passing evidence for bdd"):
                close_task(root, config, "TASK-0001")

            self.assertTrue((root / "harness" / "tasks" / "accepted" / "TASK-0001.json").exists())
            self.assertTrue((root / "harness" / "locks" / "tasks" / "TASK-0001.lock").exists())
            record_passing_evidence(config, run.path)

            closed = close_task(root, config, "TASK-0001")

            self.assertEqual(closed.task["state"], "done")
            self.assertTrue((root / "harness" / "tasks" / "done" / "TASK-0001.json").exists())
            self.assertFalse((root / "harness" / "locks" / "tasks" / "TASK-0001.lock").exists())
            self.assertFalse((root / "harness" / "locks" / "files" / "attestflow.tasks.py.lock").exists())
            metadata = load_data(run.path / "metadata.yml")
            self.assertIsNotNone(metadata["ended_at"])
            self.assertTrue(metadata["result"]["dod_passed"])

    def test_verify_task_records_current_run_command_evidence(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            config["root"] = root
            config["commands"] = {
                "bdd": "python3 -c 'print(\"bdd ok\")'",
                "unit": "python3 -c 'print(\"unit ok\")'",
                "lint": None,
                "typecheck": None,
                "secret_scan": "python3 -c 'print(\"scan ok\")'",
                "project_verify": None,
            }
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001"))
            run = start_task(root, config, "TASK-0001", actor_role="orchestrator")

            result = verify_task(root, config, "TASK-0001")

            self.assertEqual(result.failed, [])
            metadata = load_data(run.path / "metadata.yml")
            self.assertEqual(metadata["commands"]["bdd"]["exit_code"], 0)
            self.assertEqual(metadata["commands"]["bdd"]["log"], "commands/bdd.log")
            self.assertTrue(metadata["commands"]["bdd"]["fresh"])
            self.assertEqual(metadata["commands"]["secret_scan"]["exit_code"], 0)
            active = load_data(root / "harness" / "tasks" / "in_progress" / "TASK-0001.json")
            self.assertEqual(active["evidence"]["verify"], str((run.path / "metadata.yml").relative_to(root)))


def record_passing_evidence(config: dict, run_path: Path) -> None:
    results = []
    for name in ("bdd", "unit", "secret_scan"):
        log = run_path / "commands" / f"{name}.log"
        log.write_text(f"{name} ok\n", encoding="utf-8")
        results.append(
            CommandResult(
                name=name,
                command=str(config["commands"][name]),
                exit_code=0,
                log=log,
            )
        )
    record_verification_results(run_path, VerificationResult(results=results, failed=[]))


if __name__ == "__main__":
    unittest.main()
