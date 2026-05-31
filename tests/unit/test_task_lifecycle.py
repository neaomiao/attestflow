from copy import deepcopy
from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import subprocess
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


def completed_task(task: dict) -> dict:
    updated = deepcopy(task)
    updated["state"] = "done"
    evidence = dict(updated.get("evidence", {}))
    evidence["run_id"] = f"run-{updated['id']}"
    evidence["packet"] = f"harness/runs/run-{updated['id']}/evidence.md"
    updated["evidence"] = evidence
    return updated


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

    def test_ready_task_requires_dependency_list_for_ordering(self) -> None:
        task = ready_task("TASK-0001")
        task["dependencies"] = "TASK-0000"

        errors = validate_task(task, directory_state="ready")

        self.assertIn("dependencies must be a list when state is ready", errors)

    def test_ready_task_requires_integer_priority_for_ordering(self) -> None:
        task = ready_task("TASK-0001")
        task["priority"] = "urgent"

        errors = validate_task(task, directory_state="ready")

        self.assertIn("priority must be an integer", errors)

    def test_completed_task_requires_run_and_packet_evidence(self) -> None:
        task = ready_task("TASK-0001")
        task["state"] = "done"

        errors = validate_task(task, directory_state="done")

        self.assertIn("completed task requires evidence.run_id and evidence.packet", errors)

    def test_in_progress_task_requires_run_and_session_evidence(self) -> None:
        task = ready_task("TASK-0001")
        task["state"] = "in_progress"

        errors = validate_task(task, directory_state="in_progress")

        self.assertIn("in_progress task requires evidence.run_id", errors)
        self.assertIn("in_progress task requires evidence.session", errors)

    def test_review_verified_and_accepted_tasks_require_run_session_and_packet_evidence(self) -> None:
        for state in ("review", "verified", "accepted"):
            with self.subTest(state=state):
                task = ready_task("TASK-0001")
                task["state"] = state

                errors = validate_task(task, directory_state=state)

                self.assertIn(f"{state} task requires evidence.run_id", errors)
                self.assertIn(f"{state} task requires evidence.session", errors)
                self.assertIn(f"{state} task requires evidence.packet", errors)

    def test_ready_task_requires_structured_external_inputs_and_blockers(self) -> None:
        task = ready_task("TASK-0001")
        task["external_inputs"] = "API_TOKEN"
        task["blockers"] = "missing API_TOKEN"

        errors = validate_task(task, directory_state="ready")

        self.assertIn("external_inputs must be a mapping when state is ready", errors)
        self.assertIn("blockers must be a list", errors)

    def test_ready_task_rejects_malformed_structural_fields_without_crashing(self) -> None:
        task = ready_task("not-a-task-id")
        task["schema_version"] = 2
        task["requirements"] = "needs work"
        task["files"] = "attestflow/tasks.py"
        task["agents"] = "worker"
        task["links"] = []

        errors = validate_task(task, directory_state="ready")

        self.assertIn("schema_version must be 1", errors)
        self.assertIn("id must match TASK-<number>", errors)
        self.assertIn("requirements must be a mapping", errors)
        self.assertIn("files must be a mapping", errors)
        self.assertIn("agents must be a mapping", errors)
        self.assertIn("links must be a mapping", errors)

    def test_ready_task_rejects_empty_dependency_and_write_scope_entries(self) -> None:
        task = ready_task("TASK-0001")
        task["dependencies"] = ["TASK-0000", ""]
        task["files"]["write"] = ["attestflow/tasks.py", ""]

        errors = validate_task(task, directory_state="ready")

        self.assertIn("dependencies entries must be non-empty strings", errors)
        self.assertIn("files.write entries must be non-empty strings", errors)

    def test_ready_task_rejects_malformed_nested_collection_fields(self) -> None:
        task = ready_task("TASK-0001")
        task["files"]["read"] = "README.md"
        task["agents"]["owner"] = ""
        task["agents"]["allowed_roles"] = "worker_agent"
        task["external_inputs"]["credentials"] = "API_TOKEN"
        task["links"]["docs"] = "docs/contracts/task-schema.md"

        errors = validate_task(task, directory_state="ready")

        self.assertIn("files.read must be a list", errors)
        self.assertIn("agents.owner must be non-empty", errors)
        self.assertIn("agents.allowed_roles must be a list", errors)
        self.assertIn("external_inputs.credentials must be a list", errors)
        self.assertIn("links.docs must be a list", errors)

    def test_ready_task_rejects_empty_required_list_entries(self) -> None:
        task = ready_task("TASK-0001")
        task["scope"] = ["task validation", ""]
        task["out_of_scope"] = [""]
        task["bdd_scenarios"] = [""]
        task["unit_tests"] = ["tests/unit/test_task_lifecycle.py", ""]
        task["acceptance"] = [""]
        task["context"] = ["", "existing harness"]
        task["blocks"] = ["TASK-0002", ""]

        errors = validate_task(task, directory_state="ready")

        self.assertIn("scope entries must be non-empty strings", errors)
        self.assertIn("out_of_scope entries must be non-empty strings", errors)
        self.assertIn("bdd_scenarios entries must be non-empty strings", errors)
        self.assertIn("unit_tests entries must be non-empty strings", errors)
        self.assertIn("acceptance entries must be non-empty strings", errors)
        self.assertIn("context entries must be non-empty strings", errors)
        self.assertIn("blocks entries must be non-empty strings", errors)

    def test_ready_task_rejects_malformed_requirements_lists(self) -> None:
        task = ready_task("TASK-0001")
        task["requirements"]["confirmed"] = "needs validation"
        task["requirements"]["unresolved"] = [""]
        task["requirements"]["assumptions"] = [42]

        errors = validate_task(task, directory_state="ready")

        self.assertIn("requirements.confirmed must be a list", errors)
        self.assertIn("requirements.unresolved entries must be non-empty strings", errors)
        self.assertIn("requirements.assumptions entries must be non-empty strings", errors)

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

    def test_task_file_name_must_match_task_id(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            config["root"] = root
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0002"))

            with self.assertRaisesRegex(ValueError, "task id 'TASK-0002' does not match filename 'TASK-0001'"):
                select_next_task(root, config)

    def test_task_ids_must_be_unique_across_state_directories(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            config["root"] = root
            blocked = ready_task("TASK-0001")
            blocked["state"] = "blocked"
            blocked["blockers"] = [
                {
                    "id": "BLK-0001",
                    "type": "external_input",
                    "reason": "waiting",
                    "unblock_condition": "Resolve waiting input.",
                    "owner": "user",
                    "source": "test",
                    "status": "active",
                    "created_at": "2026-05-30T00:00:00Z",
                    "resolved_at": None,
                }
            ]
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001"))
            write_task(root, "blocked", "TASK-0001", blocked)

            with self.assertRaisesRegex(ValueError, "duplicate task id: TASK-0001"):
                select_next_task(root, config)

    def test_task_state_directory_must_be_known(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            config["root"] = root
            write_task(root, "scratch", "TASK-0001", ready_task("TASK-0001"))

            with self.assertRaisesRegex(ValueError, "unknown task state directory: scratch"):
                select_next_task(root, config)

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

    def test_start_task_rejects_task_in_wrong_state_directory(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            config["root"] = root
            write_task(root, "blocked", "TASK-0001", ready_task("TASK-0001"))

            with self.assertRaisesRegex(ValueError, "directory state 'blocked' does not match task state 'ready'"):
                start_task(root, config, "TASK-0001", actor_role="orchestrator")

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

    def test_resume_uses_latest_ledger_event_for_next_action(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            config["root"] = root
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001"))
            run = start_task(root, config, "TASK-0001", actor_role="orchestrator")
            bdd_log = run.path / "commands" / "bdd.log"
            bdd_log.write_text("scenario boundary mismatch\n", encoding="utf-8")
            record_verification_results(
                run.path,
                VerificationResult(
                    results=[
                        CommandResult(
                            name="bdd",
                            command="pytest tests/bdd",
                            exit_code=1,
                            log=bdd_log,
                        )
                    ],
                    failed=["bdd"],
                ),
            )

            summary = resume_summary(root, config)

            self.assertIn("last event: gate_failed bdd", summary)
            self.assertIn("repair BDD scenario or requirement boundary", summary)

    def test_resume_reports_missing_active_task_lock(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            config["root"] = root
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001"))
            run = start_task(root, config, "TASK-0001", actor_role="orchestrator")
            metadata = load_data(run.path / "metadata.yml")
            (root / metadata["locks"]["task"]).unlink()

            summary = resume_summary(root, config)

            self.assertIn("task lock missing", summary)
            self.assertIn("repair task state or re-acquire lock", summary)

    def test_cli_resume_rejects_multiple_active_task_locks_without_traceback(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            config["root"] = root
            first = ready_task("TASK-0001")
            first["files"]["write"] = ["src/one.py"]
            second = ready_task("TASK-0002")
            second["files"]["write"] = ["src/two.py"]
            write_task(root, "ready", "TASK-0001", first)
            write_task(root, "ready", "TASK-0002", second)
            start_task(root, config, "TASK-0001", actor_role="orchestrator")
            start_task(root, config, "TASK-0002", actor_role="orchestrator")
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                error = io.StringIO()
                with redirect_stderr(error):
                    exit_code = cli.main(["resume"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 1)
            self.assertIn("multiple active task locks: TASK-0001, TASK-0002", error.getvalue())
            self.assertNotIn("Traceback", error.getvalue())

    def test_cli_resume_rejects_multiple_unfinished_runs_without_traceback(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for run_id, task_id in (("RUN-1", "TASK-0001"), ("RUN-2", "TASK-0002")):
                metadata = root / "harness" / "runs" / run_id / "metadata.yml"
                metadata.parent.mkdir(parents=True)
                dump_data(
                    {
                        "schema_version": 1,
                        "run_id": run_id,
                        "task_id": task_id,
                        "ended_at": None,
                        "status": "in_progress",
                    },
                    metadata,
                )
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                error = io.StringIO()
                with redirect_stderr(error):
                    exit_code = cli.main(["resume"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 1)
            self.assertIn("multiple unfinished runs: TASK-0001, TASK-0002", error.getvalue())
            self.assertNotIn("Traceback", error.getvalue())

    def test_cli_resume_reports_malformed_run_metadata_without_traceback(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            metadata = root / "harness" / "runs" / "RUN-bad" / "metadata.yml"
            metadata.parent.mkdir(parents=True)
            metadata.write_text("not yaml\n", encoding="utf-8")
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                error = io.StringIO()
                with redirect_stderr(error):
                    exit_code = cli.main(["resume"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 1)
            self.assertIn("ERROR:", error.getvalue())
            self.assertNotIn("Traceback", error.getvalue())

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

    def test_block_task_rejects_states_without_block_transition(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            config["root"] = root
            write_task(root, "done", "TASK-0001", completed_task(ready_task("TASK-0001")))

            with self.assertRaisesRegex(ValueError, "invalid transition: done -> blocked"):
                block_task(root, config, "TASK-0001", reason="late blocker")

            self.assertTrue((root / "harness" / "tasks" / "done" / "TASK-0001.json").exists())
            self.assertFalse((root / "harness" / "tasks" / "blocked" / "TASK-0001.json").exists())

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

    def test_select_next_does_not_count_completed_task_in_wrong_directory_as_dependency(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            config["root"] = root
            invalid_done = completed_task(ready_task("TASK-0000", priority=1))
            dependent = ready_task("TASK-0001", priority=2)
            dependent["dependencies"] = ["TASK-0000"]
            write_task(root, "ready", "TASK-0000", invalid_done)
            write_task(root, "ready", "TASK-0001", dependent)

            selected = select_next_task(root, config)

            self.assertIsNone(selected)

    def test_select_dispatchable_tasks_skips_dependencies_locks_and_batch_write_conflicts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            config["root"] = root
            done = completed_task(ready_task("TASK-0000", priority=1))
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

    def test_select_dispatchable_tasks_normalizes_write_scope_conflicts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            config["root"] = root
            first = ready_task("TASK-0001", priority=1)
            first["files"]["write"] = ["src/a.py"]
            same_file = ready_task("TASK-0002", priority=2)
            same_file["files"]["write"] = ["./src/a.py"]
            write_task(root, "ready", "TASK-0001", first)
            write_task(root, "ready", "TASK-0002", same_file)

            selected = select_dispatchable_tasks(root, config, limit=10)

            self.assertEqual([record.task["id"] for record in selected], ["TASK-0001"])

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

    def test_unblock_does_not_move_invalid_task_to_ready(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            config["root"] = root
            task = ready_task("TASK-0001")
            task["state"] = "blocked"
            task["files"]["write"] = []
            task["blockers"] = [
                {
                    "id": "BLK-0001",
                    "type": "external_input",
                    "reason": "missing decision",
                    "unblock_condition": "Confirm decision.",
                    "owner": "user",
                    "source": "planner",
                    "status": "active",
                    "created_at": "2026-05-30T00:00:00Z",
                    "resolved_at": None,
                }
            ]
            write_task(root, "blocked", "TASK-0001", task)

            with self.assertRaisesRegex(ValueError, "files.write must be a non-empty list when state is ready"):
                unblock_task(root, config, "TASK-0001", "BLK-0001", resolution="Decision confirmed.")

            self.assertTrue((root / "harness" / "tasks" / "blocked" / "TASK-0001.json").exists())
            self.assertFalse((root / "harness" / "tasks" / "ready" / "TASK-0001.json").exists())

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

    def test_cli_task_state_commands_report_missing_task_without_traceback(self) -> None:
        commands = [
            ["block", "TASK-4040", "--reason", "missing dependency"],
            ["unblock", "TASK-4040", "--blocker", "BLK-4040", "--resolution", "resolved"],
            ["transition", "TASK-4040", "review"],
            ["close", "TASK-4040"],
        ]
        for command in commands:
            with self.subTest(command=command):
                with TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    original_root = cli.ROOT
                    cli.ROOT = root
                    try:
                        error = io.StringIO()
                        with redirect_stderr(error):
                            exit_code = cli.main(command)
                    finally:
                        cli.ROOT = original_root

                    self.assertEqual(exit_code, 1)
                    self.assertIn("ERROR: task not found: TASK-4040", error.getvalue())
                    self.assertNotIn("Traceback", error.getvalue())

    def test_cli_task_listing_commands_report_malformed_task_without_traceback(self) -> None:
        commands = [
            ["tasks"],
            ["next"],
            ["dispatch"],
            ["evidence", "TASK-0001"],
            ["session", "resume", "TASK-0001"],
        ]
        for command in commands:
            with self.subTest(command=command):
                with TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    task_dir = root / "harness" / "tasks" / "ready"
                    task_dir.mkdir(parents=True)
                    (task_dir / "TASK-bad.json").write_text("{not json\n", encoding="utf-8")
                    original_root = cli.ROOT
                    cli.ROOT = root
                    try:
                        error = io.StringIO()
                        with redirect_stderr(error):
                            exit_code = cli.main(command)
                    finally:
                        cli.ROOT = original_root

                    self.assertEqual(exit_code, 1)
                    self.assertIn("ERROR:", error.getvalue())
                    self.assertNotIn("Traceback", error.getvalue())

    def test_close_task_requires_accepted_state_and_releases_locks(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            config["root"] = root
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001"))
            run = start_task(root, config, "TASK-0001", actor_role="orchestrator")
            transition_task(root, config, "TASK-0001", "review")
            record_passing_evidence(config, run.path)
            transition_task(root, config, "TASK-0001", "verified")
            transition_task(root, config, "TASK-0001", "accepted")

            closed = close_task(root, config, "TASK-0001")

            self.assertEqual(closed.task["state"], "done")
            self.assertTrue((root / "harness" / "tasks" / "done" / "TASK-0001.json").exists())
            self.assertFalse((root / "harness" / "locks" / "tasks" / "TASK-0001.lock").exists())
            self.assertFalse((root / "harness" / "locks" / "files" / "attestflow.tasks.py.lock").exists())
            metadata = load_data(run.path / "metadata.yml")
            self.assertIsNotNone(metadata["ended_at"])
            self.assertTrue(metadata["result"]["dod_passed"])

    def test_close_task_rejects_run_metadata_run_id_mismatch(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            config["root"] = root
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001"))
            run = start_task(root, config, "TASK-0001", actor_role="orchestrator")
            transition_task(root, config, "TASK-0001", "review")
            record_passing_evidence(config, run.path)
            transition_task(root, config, "TASK-0001", "verified")
            transition_task(root, config, "TASK-0001", "accepted")
            metadata = load_data(run.path / "metadata.yml")
            metadata["run_id"] = "RUN-other"
            dump_data(metadata, run.path / "metadata.yml")

            with self.assertRaisesRegex(ValueError, "run metadata run_id does not match evidence reference"):
                close_task(root, config, "TASK-0001")

            self.assertTrue((root / "harness" / "tasks" / "accepted" / "TASK-0001.json").exists())
            self.assertFalse((root / "harness" / "tasks" / "done" / "TASK-0001.json").exists())

    def test_close_task_rejects_evidence_packet_task_or_run_mismatch(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            config["root"] = root
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001"))
            run = start_task(root, config, "TASK-0001", actor_role="orchestrator")
            transition_task(root, config, "TASK-0001", "review")
            record_passing_evidence(config, run.path)
            transition_task(root, config, "TASK-0001", "verified")
            transition_task(root, config, "TASK-0001", "accepted")
            (run.path / "evidence.md").write_text(
                "\n".join(
                    [
                        "# Evidence Packet",
                        "",
                        "## Task",
                        "",
                        "- ID: TASK-9999",
                        "- Title: Wrong task",
                        "- Run: RUN-other",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "evidence packet task_id does not match task"):
                close_task(root, config, "TASK-0001")

            self.assertTrue((root / "harness" / "tasks" / "accepted" / "TASK-0001.json").exists())
            self.assertFalse((root / "harness" / "tasks" / "done" / "TASK-0001.json").exists())

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

    def test_cli_verify_missing_task_reports_error_without_traceback(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                error = io.StringIO()
                with redirect_stderr(error):
                    exit_code = cli.main(["verify", "--task", "TASK-4040"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 1)
            self.assertIn("ERROR: task not found: TASK-4040", error.getvalue())
            self.assertNotIn("Traceback", error.getvalue())

    def test_verify_task_runs_configured_commands_inside_task_worktree(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            init_git_repo(root)
            config = deepcopy(DEFAULT_CONFIG)
            config["root"] = root
            config["commands"] = {
                "bdd": "python3 -c 'from pathlib import Path; Path(\"verify-cwd.txt\").write_text(str(Path.cwd()), encoding=\"utf-8\")'",
                "unit": None,
                "lint": None,
                "typecheck": None,
                "secret_scan": None,
                "project_verify": None,
            }
            config["sessions"]["worktree"] = {
                "enabled": True,
                "path_template": str(root.parent / "worktrees" / "{task_id}-{run_id}"),
            }
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001"))
            run = start_task(root, config, "TASK-0001", actor_role="orchestrator")
            metadata = load_data(run.path / "metadata.yml")
            worktree = Path(metadata["workspace"]["worktree"])

            result = verify_task(root, config, "TASK-0001")

            self.assertEqual(result.failed, [])
            self.assertEqual((worktree / "verify-cwd.txt").read_text(encoding="utf-8"), str(worktree))
            self.assertFalse((root / "verify-cwd.txt").exists())

    def test_close_task_records_worktree_commit_after(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            init_git_repo(root)
            config = deepcopy(DEFAULT_CONFIG)
            config["root"] = root
            for command_name in config["commands"]:
                config["commands"][command_name] = None
            config["policies"]["require_fresh_verify_for_done"] = False
            config["sessions"]["worktree"] = {
                "enabled": True,
                "path_template": str(root.parent / "worktrees" / "{task_id}-{run_id}"),
            }
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001"))
            run = start_task(root, config, "TASK-0001", actor_role="orchestrator")
            transition_task(root, config, "TASK-0001", "review")
            transition_task(root, config, "TASK-0001", "verified")
            transition_task(root, config, "TASK-0001", "accepted")
            metadata = load_data(run.path / "metadata.yml")
            worktree = Path(metadata["workspace"]["worktree"])
            expected_head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=worktree,
                check=True,
                text=True,
                capture_output=True,
            ).stdout.strip()

            close_task(root, config, "TASK-0001")

            metadata = load_data(run.path / "metadata.yml")
            self.assertEqual(metadata["workspace"]["commit_after"], expected_head)

    def test_close_task_merges_worktree_changes_back_to_control_repo(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            init_git_repo(root)
            config = deepcopy(DEFAULT_CONFIG)
            config["root"] = root
            for command_name in config["commands"]:
                config["commands"][command_name] = None
            config["policies"]["require_fresh_verify_for_done"] = False
            config["sessions"]["worktree"] = {
                "enabled": True,
                "path_template": str(root.parent / "worktrees" / "{task_id}-{run_id}"),
            }
            task = ready_task("TASK-0001")
            task["files"]["write"] = ["src/feature.py"]
            write_task(root, "ready", "TASK-0001", task)
            run = start_task(root, config, "TASK-0001", actor_role="orchestrator")
            transition_task(root, config, "TASK-0001", "review")
            transition_task(root, config, "TASK-0001", "verified")
            transition_task(root, config, "TASK-0001", "accepted")
            metadata = load_data(run.path / "metadata.yml")
            worktree = Path(metadata["workspace"]["worktree"])
            (worktree / "src").mkdir()
            (worktree / "src" / "feature.py").write_text("VALUE = 42\n", encoding="utf-8")

            close_task(root, config, "TASK-0001")

            self.assertEqual((root / "src" / "feature.py").read_text(encoding="utf-8"), "VALUE = 42\n")
            metadata = load_data(run.path / "metadata.yml")
            self.assertTrue(metadata["workspace"]["applied_to_control"])
            self.assertNotEqual(metadata["workspace"]["commit_before"], metadata["workspace"]["commit_after"])
            ledger = (run.path / "ledger.jsonl").read_text(encoding="utf-8")
            self.assertIn('"event": "worktree_applied"', ledger)

    def test_close_task_refuses_to_merge_worktree_when_control_repo_advanced(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            init_git_repo(root)
            config = deepcopy(DEFAULT_CONFIG)
            config["root"] = root
            for command_name in config["commands"]:
                config["commands"][command_name] = None
            config["policies"]["require_fresh_verify_for_done"] = False
            config["sessions"]["worktree"] = {
                "enabled": True,
                "path_template": str(root.parent / "worktrees" / "{task_id}-{run_id}"),
            }
            task = ready_task("TASK-0001")
            task["files"]["write"] = ["src/feature.py"]
            write_task(root, "ready", "TASK-0001", task)
            run = start_task(root, config, "TASK-0001", actor_role="orchestrator")
            transition_task(root, config, "TASK-0001", "review")
            transition_task(root, config, "TASK-0001", "verified")
            transition_task(root, config, "TASK-0001", "accepted")
            worktree = Path(load_data(run.path / "metadata.yml")["workspace"]["worktree"])
            (worktree / "src").mkdir()
            (worktree / "src" / "feature.py").write_text("VALUE = 42\n", encoding="utf-8")
            (root / "README.md").write_text("control moved\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "control moved"], cwd=root, check=True, stdout=subprocess.DEVNULL)

            with self.assertRaisesRegex(ValueError, "git merge --ff-only"):
                close_task(root, config, "TASK-0001")

            self.assertFalse((root / "src" / "feature.py").exists())
            self.assertTrue((root / "harness" / "tasks" / "accepted" / "TASK-0001.json").exists())


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


def init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "attestflow@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Attestflow Tests"], cwd=root, check=True)
    (root / "README.md").write_text("test repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=root, check=True, stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    unittest.main()
