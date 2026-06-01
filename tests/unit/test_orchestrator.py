from copy import deepcopy
from contextlib import redirect_stderr, redirect_stdout
import json
import io
from pathlib import Path
import shlex
import subprocess
import sys
import threading
import time
from tempfile import TemporaryDirectory
import unittest

import attestflow.cli as cli
from attestflow.config import DEFAULT_CONFIG
from attestflow.io import dump_data, load_data
from attestflow.locks import file_lock_path, task_lock_path
from attestflow.orchestrator import build_execution_plan, run_autopilot


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
        "title": f"Task {task_id}",
        "state": "ready",
        "priority": priority,
        "type": "feature",
        "purpose": "Exercise deterministic task ordering.",
        "context": [],
        "scope": ["orchestrator dry run"],
        "out_of_scope": ["runtime execution"],
        "requirements": {"confirmed": ["needs ordered batches"], "unresolved": [], "assumptions": []},
        "bdd_scenarios": ["Dry run reports executable batches."],
        "unit_tests": ["tests/unit/test_orchestrator.py"],
        "acceptance": ["dry run is deterministic"],
        "dependencies": [],
        "blocks": [],
        "blockers": [],
        "files": {"read": [], "write": [f"src/{task_id}.py"]},
        "agents": {"owner": "orchestrator", "allowed_roles": ["worker_agent"]},
        "external_inputs": {"credentials": [], "services": [], "user_decisions": []},
        "evidence": {"session": None, "run_id": None, "red": None, "green": None, "verify": None, "packet": None},
        "links": {"issues": [], "prs": [], "docs": []},
        "risks": [],
        "notes": [],
        "created_at": "2026-05-30T00:00:00Z",
        "updated_at": "2026-05-30T00:00:00Z",
    }


def completed_task(task: dict) -> dict:
    updated = deepcopy(task)
    updated["state"] = "done"
    evidence = dict(updated.get("evidence", {}))
    evidence["run_id"] = f"run-{updated['id']}"
    evidence["packet"] = f"harness/runs/run-{updated['id']}/evidence.md"
    updated["evidence"] = evidence
    return updated


def active_task(task: dict, state: str = "in_progress") -> dict:
    updated = deepcopy(task)
    updated["state"] = state
    run_id = f"run-{updated['id']}"
    evidence = dict(updated.get("evidence", {}))
    if not evidence.get("run_id"):
        evidence["run_id"] = run_id
    if not evidence.get("session"):
        evidence["session"] = f"harness/runs/{run_id}/session.yml"
    if not evidence.get("packet"):
        evidence["packet"] = f"harness/runs/{run_id}/evidence.md"
    updated["evidence"] = evidence
    return updated


def load_json_or_yaml(path: Path) -> dict:
    return load_data(path)


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "attestflow@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Attestflow Tests"], cwd=root, check=True)
    (root / "README.md").write_text("test repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=root, check=True, stdout=subprocess.DEVNULL)


CAPABILITY_STUB_SCRIPT = """
import json
import sys

payload = json.load(sys.stdin)
capability = payload.get("capability", {}).get("name", "reviewer")
task = payload.get("task", {})
files = task.get("files", {}) if isinstance(task.get("files"), dict) else {}
write_files = files.get("write") if isinstance(files.get("write"), list) else []
if not write_files:
    write_files = ["README.md"]
output = {
    "schema_version": 1,
    "status": "passed",
    "summary": f"{capability} passed",
    "findings": [],
    "evidence": [f"{capability} evidence"],
}
if capability == "bdd":
    output["artifacts"] = {
        "scenarios": [
            {
                "name": "Configured behavior passes",
                "given": "a ready task",
                "when": "the requested behavior is exercised",
                "then": "the task acceptance criteria are met",
            }
        ],
        "updated_files": write_files,
        "requirements_mapping": [{"requirement": "needs ordered batches", "scenarios": ["Configured behavior passes"]}],
        "uncovered_behaviors": [],
    }
elif capability == "tdd":
    output["artifacts"] = {
        "red_log": "red test observed",
        "green_log": "green test observed",
        "test_files": write_files,
        "failing_tests": [],
        "coverage": {"scope": write_files},
    }
elif capability == "implementer":
    output["artifacts"] = {
        "diff_summary": "Implemented scoped changes.",
        "written_files": write_files,
        "incomplete": [],
        "risks": [],
        "command_results": [],
    }
elif capability == "verifier":
    output["artifacts"] = {
        "commands": [{"name": "unit", "command": "unit", "status": "passed"}],
        "environment": {"provider": "stub"},
        "duration_seconds": 0,
        "flake": {"detected": False},
        "evidence": ["verification log"],
    }
json.dump(output, sys.stdout)
""".lstrip()


def write_capability_stub(path: Path) -> None:
    path.write_text(CAPABILITY_STUB_SCRIPT, encoding="utf-8")


class OrchestratorTests(unittest.TestCase):
    def test_execution_plan_batches_tasks_by_dependencies_priority_and_write_conflicts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            done = completed_task(ready_task("TASK-0000"))
            first = ready_task("TASK-0001", priority=1)
            first["files"]["write"] = ["src/shared.py"]
            dependent = ready_task("TASK-0002", priority=2)
            dependent["dependencies"] = ["TASK-0001"]
            dependent["files"]["write"] = ["src/dependent.py"]
            same_write_scope = ready_task("TASK-0003", priority=3)
            same_write_scope["files"]["write"] = ["src/shared.py"]
            missing_dependency = ready_task("TASK-0004", priority=4)
            missing_dependency["dependencies"] = ["TASK-9999"]
            locked = ready_task("TASK-0005", priority=5)
            locked["files"]["write"] = ["src/locked.py"]
            write_task(root, "done", "TASK-0000", done)
            write_task(root, "ready", "TASK-0001", first)
            write_task(root, "ready", "TASK-0002", dependent)
            write_task(root, "ready", "TASK-0003", same_write_scope)
            write_task(root, "ready", "TASK-0004", missing_dependency)
            write_task(root, "ready", "TASK-0005", locked)
            (root / "harness" / "locks" / "files").mkdir(parents=True)
            (root / "harness" / "locks" / "files" / "src.locked.py.lock").write_text(
                "TASK-9998\n",
                encoding="utf-8",
            )

            plan = build_execution_plan(root, config, limit=2)

            self.assertEqual([[task.task_id for task in batch.tasks] for batch in plan.batches], [["TASK-0001"], ["TASK-0002", "TASK-0003"]])
            skipped = {task.task_id: task.reasons for task in plan.skipped}
            self.assertEqual(skipped["TASK-0004"], ["waiting for dependencies: TASK-9999"])
            self.assertEqual(skipped["TASK-0005"], ["write scope locked: src/locked.py"])

    def test_execution_plan_normalizes_write_scope_conflicts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            first = ready_task("TASK-0001", priority=1)
            first["files"]["write"] = ["src/shared.py"]
            same_write_scope = ready_task("TASK-0002", priority=2)
            same_write_scope["files"]["write"] = ["./src/shared.py"]
            write_task(root, "ready", "TASK-0001", first)
            write_task(root, "ready", "TASK-0002", same_write_scope)

            plan = build_execution_plan(root, config, limit=2)

            self.assertEqual([[task.task_id for task in batch.tasks] for batch in plan.batches], [["TASK-0001"], ["TASK-0002"]])

    def test_execution_plan_rejects_active_task_in_wrong_state_directory(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            task = active_task(ready_task("TASK-0001"))
            write_task(root, "ready", "TASK-0001", task)

            plan = build_execution_plan(root, config)

            self.assertEqual(plan.actions, [])
            skipped = {task.task_id: task.reasons for task in plan.skipped}
            self.assertIn("directory state 'ready' does not match task state 'in_progress'", skipped["TASK-0001"])

    def test_autopilot_blocks_completed_task_in_wrong_state_directory(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = deepcopy(DEFAULT_CONFIG)
            task = completed_task(ready_task("TASK-0001"))
            write_task(root, "ready", "TASK-0001", task)

            result = run_autopilot(root, config, limit=1, max_steps=1, actor_role="orchestrator")

            self.assertEqual(result.status, "blocked")
            self.assertEqual(result.blocked, ["TASK-0001"])
            metadata = load_json_or_yaml(result.path / "metadata.json")
            self.assertIn("directory state 'ready' does not match task state 'done'", metadata["skipped"][0]["reasons"])

    def test_cli_autopilot_dry_run_prints_batches_and_skipped_reasons(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = ready_task("TASK-0001", priority=1)
            second = ready_task("TASK-0002", priority=2)
            second["dependencies"] = ["TASK-0001"]
            blocked = ready_task("TASK-0003", priority=3)
            blocked["external_inputs"]["credentials"] = ["API_TOKEN"]
            write_task(root, "ready", "TASK-0001", first)
            write_task(root, "ready", "TASK-0002", second)
            write_task(root, "ready", "TASK-0003", blocked)
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(["autopilot", "--dry-run", "--limit", "1"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 0)
            text = output.getvalue()
            self.assertIn("autopilot dry run", text)
            self.assertIn("batch 1", text)
            self.assertIn("TASK-0001", text)
            self.assertIn("batch 2", text)
            self.assertIn("TASK-0002", text)
            self.assertIn("skipped", text)
            self.assertIn("TASK-0003", text)
            self.assertIn("external_inputs must be empty", text)

    def test_cli_autopilot_dry_run_reports_active_task_action_before_new_dispatch(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            active = active_task(ready_task("TASK-0001", priority=1))
            ready = ready_task("TASK-0002", priority=2)
            write_task(root, "in_progress", "TASK-0001", active)
            write_task(root, "ready", "TASK-0002", ready)
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(["autopilot", "--dry-run", "--limit", "1"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 0)
            text = output.getvalue()
            self.assertIn("actions:", text)
            self.assertIn("TASK-0001", text)
            self.assertIn("run_capability", text)
            self.assertIn("bdd", text)
            self.assertNotIn("batch 1", text)

    def test_cli_autopilot_requires_explicit_mode(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                stderr = io.StringIO()
                with redirect_stderr(stderr):
                    exit_code = cli.main(["autopilot"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 1)
            self.assertIn("use --dry-run, --run, --resume, or --status", stderr.getvalue())

    def test_cli_autopilot_goal_requires_run_mode(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                stderr = io.StringIO()
                with redirect_stderr(stderr):
                    exit_code = cli.main(["autopilot", "--dry-run", "--goal", "ship login"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 1)
            self.assertIn("--goal can only be used with --run", stderr.getvalue())

    def test_cli_autopilot_run_with_goal_plans_then_dispatches_task(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            planner_script = root / "planner_stub.py"
            planner_script.write_text(
                "\n".join(
                    [
                        "import json, sys",
                        "payload = json.load(sys.stdin)",
                        "assert payload['goal'] == 'ship login'",
                        "print(json.dumps({",
                        "    'schema_version': 1,",
                        "    'tasks': [{",
                        "        'key': 'login',",
                        "        'title': 'Implement login',",
                        "        'priority': 1,",
                        "        'type': 'feature',",
                        "        'purpose': 'Exercise autonomous planning.',",
                        "        'context': [],",
                        "        'scope': ['Add login flow'],",
                        "        'out_of_scope': ['Billing'],",
                        "        'requirements': {'confirmed': ['login works'], 'unresolved': [], 'assumptions': []},",
                        "        'bdd_scenarios': ['User can log in.'],",
                        "        'unit_tests': ['tests/unit/test_login.py'],",
                        "        'acceptance': ['login task imported'],",
                        "        'dependencies': [],",
                        "        'files': {'read': [], 'write': ['src/login.py']},",
                        "        'external_inputs': {'credentials': [], 'services': [], 'user_decisions': []}",
                        "    }]",
                        "}))",
                    ]
                ),
                encoding="utf-8",
            )
            config = deepcopy(DEFAULT_CONFIG)
            config["capabilities"]["planner"]["command"] = f"{shlex.quote(sys.executable)} {shlex.quote(str(planner_script))}"
            dump_data(
                {
                    "schema_version": 1,
                    "capabilities": config["capabilities"],
                    "sessions": config["sessions"],
                },
                root / "harness.yml",
            )
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(["autopilot", "--run", "--goal", "ship login", "--limit", "1", "--max-steps", "2", "--json"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["planned"], ["TASK-0001"])
            self.assertTrue((root / payload["planner"]).exists())
            self.assertEqual(payload["dispatched"], ["TASK-0001"])
            self.assertTrue((root / "harness" / "tasks" / "in_progress" / "TASK-0001.json").exists())
            metadata = json.loads((Path(payload["path"]) / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["goal"], "ship login")
            self.assertEqual(metadata["planned"], ["TASK-0001"])
            self.assertIn("autopilot:plan", metadata["actions"])

    def test_autopilot_runs_intake_before_planner_when_configured(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            intake_script = root / "intake_stub.py"
            planner_script = root / "planner_stub.py"
            intake_script.write_text(
                "\n".join(
                    [
                        "import json, pathlib, sys",
                        "payload = json.load(sys.stdin)",
                        "assert payload['goal'] == 'ship login'",
                        "pathlib.Path('intake-seen.txt').write_text('yes', encoding='utf-8')",
                        "print(json.dumps({",
                        "    'schema_version': 1,",
                        "    'status': 'passed',",
                        "    'summary': 'requirements are clear',",
                        "    'findings': [],",
                        "    'evidence': ['intake brief'],",
                        "    'artifacts': {'confirmed': ['login works'], 'decision_blockers': []}",
                        "}))",
                    ]
                ),
                encoding="utf-8",
            )
            planner_script.write_text(
                "\n".join(
                    [
                        "import json, pathlib, sys",
                        "payload = json.load(sys.stdin)",
                        "assert payload['goal'] == 'ship login'",
                        "assert pathlib.Path('intake-seen.txt').exists()",
                        "print(json.dumps({",
                        "    'schema_version': 1,",
                        "    'tasks': [{",
                        "        'key': 'login',",
                        "        'title': 'Implement login',",
                        "        'priority': 1,",
                        "        'type': 'feature',",
                        "        'purpose': 'Exercise intake before planning.',",
                        "        'context': [],",
                        "        'scope': ['Add login flow'],",
                        "        'out_of_scope': ['Billing'],",
                        "        'requirements': {'confirmed': ['login works'], 'unresolved': [], 'assumptions': []},",
                        "        'bdd_scenarios': ['User can log in.'],",
                        "        'unit_tests': ['tests/unit/test_login.py'],",
                        "        'acceptance': ['login task imported'],",
                        "        'dependencies': [],",
                        "        'files': {'read': [], 'write': ['src/login.py']},",
                        "        'external_inputs': {'credentials': [], 'services': [], 'user_decisions': []}",
                        "    }]",
                        "}))",
                    ]
                ),
                encoding="utf-8",
            )
            config = deepcopy(DEFAULT_CONFIG)
            config["capabilities"]["intake"]["command"] = f"{shlex.quote(sys.executable)} {shlex.quote(str(intake_script))}"
            config["capabilities"]["planner"]["command"] = f"{shlex.quote(sys.executable)} {shlex.quote(str(planner_script))}"

            result = run_autopilot(root, config, limit=1, max_steps=2, actor_role="orchestrator", goal="ship login")

            self.assertEqual(result.planned, ["TASK-0001"])
            self.assertEqual(result.actions[:2], ["autopilot:intake", "autopilot:plan"])
            metadata = load_data(result.path / "metadata.json")
            self.assertTrue(metadata["intake"].startswith("harness/capability-runs/intake-"))
            self.assertEqual(metadata["intake_status"], "passed")

    def test_autopilot_pauses_after_intake_when_planner_is_still_pending(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            intake_script = root / "intake_stub.py"
            planner_script = root / "planner_stub.py"
            intake_script.write_text(
                "import json, sys\n"
                "json.load(sys.stdin)\n"
                "json.dump({'schema_version': 1, 'status': 'passed', 'summary': 'clear', 'findings': [], 'evidence': ['intake']}, sys.stdout)\n",
                encoding="utf-8",
            )
            planner_script.write_text(
                "\n".join(
                    [
                        "import json, sys",
                        "json.load(sys.stdin)",
                        "print(json.dumps({",
                        "    'schema_version': 1,",
                        "    'tasks': [{",
                        "        'key': 'login',",
                        "        'title': 'Implement login',",
                        "        'priority': 1,",
                        "        'type': 'feature',",
                        "        'purpose': 'Exercise resumed planning after intake.',",
                        "        'context': [],",
                        "        'scope': ['Add login flow'],",
                        "        'out_of_scope': ['Billing'],",
                        "        'requirements': {'confirmed': ['login works'], 'unresolved': [], 'assumptions': []},",
                        "        'bdd_scenarios': ['User can log in.'],",
                        "        'unit_tests': ['tests/unit/test_login.py'],",
                        "        'acceptance': ['login task imported'],",
                        "        'dependencies': [],",
                        "        'files': {'read': [], 'write': ['src/login.py']},",
                        "        'external_inputs': {'credentials': [], 'services': [], 'user_decisions': []}",
                        "    }]",
                        "}))",
                    ]
                ),
                encoding="utf-8",
            )
            config = deepcopy(DEFAULT_CONFIG)
            config["capabilities"]["intake"]["command"] = f"{shlex.quote(sys.executable)} {shlex.quote(str(intake_script))}"
            config["capabilities"]["planner"]["command"] = f"{shlex.quote(sys.executable)} {shlex.quote(str(planner_script))}"

            first = run_autopilot(root, config, limit=1, max_steps=1, actor_role="orchestrator", goal="ship login")
            second = run_autopilot(root, config, limit=1, max_steps=1, actor_role="orchestrator", resume_path=first.path)

            self.assertEqual(first.status, "paused")
            self.assertEqual(first.pause_reason, "max_steps_reached")
            self.assertEqual(first.actions, ["autopilot:intake"])
            self.assertEqual(second.planned, ["TASK-0001"])
            self.assertIn("autopilot:plan", second.actions)

    def test_autopilot_blocks_vague_goal_when_intake_returns_decision_blocker(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            intake_script = root / "intake_blocker.py"
            planner_script = root / "planner_should_not_run.py"
            intake_script.write_text(
                "\n".join(
                    [
                        "import json, sys",
                        "json.load(sys.stdin)",
                        "print(json.dumps({",
                        "    'schema_version': 1,",
                        "    'status': 'blocked',",
                        "    'summary': 'goal needs a product decision',",
                        "    'findings': [{'severity': 'blocker', 'blocking': True, 'summary': 'Choose target user'}],",
                        "    'evidence': ['decision blocker'],",
                        "    'artifacts': {'decision_blockers': [{'id': 'DECISION-1', 'question': 'Who is the target user?'}]}",
                        "}))",
                    ]
                ),
                encoding="utf-8",
            )
            planner_script.write_text(
                "from pathlib import Path\nPath('planner-ran.txt').write_text('bad', encoding='utf-8')\n",
                encoding="utf-8",
            )
            config = deepcopy(DEFAULT_CONFIG)
            config["capabilities"]["intake"]["command"] = f"{shlex.quote(sys.executable)} {shlex.quote(str(intake_script))}"
            config["capabilities"]["planner"]["command"] = f"{shlex.quote(sys.executable)} {shlex.quote(str(planner_script))}"

            result = run_autopilot(root, config, limit=1, max_steps=3, actor_role="orchestrator", goal="make it better")

            self.assertEqual(result.status, "blocked")
            self.assertEqual(result.blocked, ["intake"])
            self.assertEqual(result.planned, [])
            self.assertFalse((root / "planner-ran.txt").exists())
            metadata = load_data(result.path / "metadata.json")
            self.assertEqual(metadata["intake_status"], "blocked")
            self.assertEqual(metadata["planner"], None)

    def test_cli_autopilot_run_dispatches_first_batch_and_writes_ledger(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = ready_task("TASK-0001", priority=1)
            second = ready_task("TASK-0002", priority=2)
            second["dependencies"] = ["TASK-0001"]
            write_task(root, "ready", "TASK-0001", first)
            write_task(root, "ready", "TASK-0002", second)
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(["autopilot", "--run", "--limit", "1", "--max-steps", "1"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 0)
            text = output.getvalue()
            self.assertIn("autopilot run:", text)
            self.assertIn("paused: max_steps_reached", text)
            self.assertIn("dispatched 1 task(s): TASK-0001", text)
            self.assertTrue((root / "harness" / "tasks" / "in_progress" / "TASK-0001.json").exists())
            self.assertTrue((root / "harness" / "tasks" / "ready" / "TASK-0002.json").exists())
            run_dirs = sorted((root / "harness" / "autopilot-runs").glob("*"))
            self.assertEqual(len(run_dirs), 1)
            metadata = json.loads((run_dirs[0] / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["status"], "paused")
            self.assertEqual(metadata["pause_reason"], "max_steps_reached")
            self.assertEqual(metadata["dispatched"], ["TASK-0001"])
            ledger_events = [
                json.loads(line)
                for line in (run_dirs[0] / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(
                [event["event"] for event in ledger_events],
                [
                    "autopilot_started",
                    "batch_planned",
                    "task_started",
                    "task_dispatched",
                    "autopilot_finished",
                ],
            )
            dispatched = [event for event in ledger_events if event["event"] == "task_dispatched"][0]
            self.assertEqual(dispatched["task_id"], "TASK-0001")

    def test_autopilot_dispatches_ready_batch_concurrently(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = root / "slow_session_provider.py"
            starts_log = root.parent / f"{root.name}-starts.jsonl"
            provider.write_text(
                "\n".join(
                    [
                        "import json, pathlib, sys, time",
                        "payload = json.load(sys.stdin)",
                        "task_id = payload['session']['task_id']",
                        f"log = pathlib.Path({str(starts_log)!r})",
                        "with log.open('a', encoding='utf-8') as handle:",
                        "    handle.write(json.dumps({'task_id': task_id, 'started': time.time()}) + '\\n')",
                        "time.sleep(0.45)",
                        "json.dump({",
                        "    'schema_version': 1,",
                        "    'status': 'launched',",
                        "    'external_session_id': f'session-{task_id}',",
                        "    'summary': f'launched {task_id}',",
                        "}, sys.stdout)",
                    ]
                ),
                encoding="utf-8",
            )
            config = deepcopy(DEFAULT_CONFIG)
            config["sessions"]["agent_provider"] = "codex"
            config["sessions"]["launch_command"] = f"{shlex.quote(sys.executable)} {shlex.quote(str(provider))}"
            first = ready_task("TASK-0001", priority=1)
            first["files"]["write"] = ["src/a.py"]
            second = ready_task("TASK-0002", priority=2)
            second["files"]["write"] = ["src/b.py"]
            write_task(root, "ready", "TASK-0001", first)
            write_task(root, "ready", "TASK-0002", second)

            started_at = time.perf_counter()
            result = run_autopilot(root, config, limit=2, max_steps=1)
            elapsed = time.perf_counter() - started_at

            self.assertLess(elapsed, 0.8)
            self.assertEqual(result.dispatched, ["TASK-0001", "TASK-0002"])
            starts = [json.loads(line) for line in starts_log.read_text(encoding="utf-8").splitlines()]
            self.assertEqual({item["task_id"] for item in starts}, {"TASK-0001", "TASK-0002"})
            self.assertLess(max(item["started"] for item in starts) - min(item["started"] for item in starts), 0.25)
            metadata = load_data(result.path / "metadata.json")
            batch_execution = metadata["batch_executions"][0]
            self.assertEqual(batch_execution["status"], "passed")
            self.assertEqual(batch_execution["mode"], "concurrent")
            self.assertEqual({item["task_id"] for item in batch_execution["tasks"]}, {"TASK-0001", "TASK-0002"})
            self.assertEqual(batch_execution["merge_queue"], ["TASK-0001", "TASK-0002"])
            batch_log = result.path / batch_execution["log"]
            log_events = [json.loads(line) for line in batch_log.read_text(encoding="utf-8").splitlines()]
            self.assertIn("task_heartbeat", {event["event"] for event in log_events})
            self.assertEqual(batch_execution["resource_budget"]["max_workers"], 2)

    def test_autopilot_batch_dispatch_isolates_failed_task_from_other_tasks(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = root / "mixed_session_provider.py"
            provider.write_text(
                "\n".join(
                    [
                        "import json, sys",
                        "payload = json.load(sys.stdin)",
                        "task_id = payload['session']['task_id']",
                        "if task_id == 'TASK-0001':",
                        "    print('not json')",
                        "else:",
                        "    json.dump({",
                        "        'schema_version': 1,",
                        "        'status': 'launched',",
                        "        'external_session_id': f'session-{task_id}',",
                        "        'summary': f'launched {task_id}',",
                        "    }, sys.stdout)",
                    ]
                ),
                encoding="utf-8",
            )
            config = deepcopy(DEFAULT_CONFIG)
            config["sessions"]["agent_provider"] = "codex"
            config["sessions"]["launch_command"] = f"{shlex.quote(sys.executable)} {shlex.quote(str(provider))}"
            first = ready_task("TASK-0001", priority=1)
            first["files"]["write"] = ["src/a.py"]
            second = ready_task("TASK-0002", priority=2)
            second["files"]["write"] = ["src/b.py"]
            write_task(root, "ready", "TASK-0001", first)
            write_task(root, "ready", "TASK-0002", second)

            result = run_autopilot(root, config, limit=2, max_steps=1)

            self.assertEqual(result.failed, ["TASK-0001"])
            self.assertEqual(result.dispatched, ["TASK-0002"])
            self.assertTrue((root / "harness" / "tasks" / "in_progress" / "TASK-0001.json").exists())
            self.assertTrue((root / "harness" / "tasks" / "in_progress" / "TASK-0002.json").exists())
            metadata = load_data(result.path / "metadata.json")
            batch_execution = metadata["batch_executions"][0]
            self.assertEqual(batch_execution["status"], "failed")
            self.assertEqual(batch_execution["merge_queue"], ["TASK-0002"])
            self.assertEqual(
                {item["task_id"]: item["status"] for item in batch_execution["tasks"]},
                {"TASK-0001": "failed", "TASK-0002": "dispatched"},
            )

    def test_cli_autopilot_cancel_stops_running_session_and_marks_run_cancelled(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = root / "slow_cancel_provider.py"
            started_marker = root.parent / f"{root.name}-started.txt"
            finished_marker = root.parent / f"{root.name}-finished.txt"
            provider.write_text(
                "\n".join(
                    [
                        "import json, pathlib, sys, time",
                        "json.load(sys.stdin)",
                        f"pathlib.Path({str(started_marker)!r}).write_text('started', encoding='utf-8')",
                        "time.sleep(5)",
                        f"pathlib.Path({str(finished_marker)!r}).write_text('finished', encoding='utf-8')",
                        "json.dump({'schema_version': 1, 'status': 'launched', 'summary': 'too late'}, sys.stdout)",
                    ]
                ),
                encoding="utf-8",
            )
            config = deepcopy(DEFAULT_CONFIG)
            config["sessions"]["agent_provider"] = "codex"
            config["sessions"]["launch_command"] = f"{shlex.quote(sys.executable)} {shlex.quote(str(provider))}"
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001", priority=1))
            result_holder: dict[str, object] = {}

            def run() -> None:
                result_holder["result"] = run_autopilot(root, config, limit=1, max_steps=1)

            thread = threading.Thread(target=run)
            thread.start()
            deadline = time.time() + 3
            run_path: Path | None = None
            while time.time() < deadline:
                runs = sorted((root / "harness" / "autopilot-runs").glob("*/metadata.json"))
                if runs and started_marker.exists():
                    run_path = runs[-1].parent
                    break
                time.sleep(0.05)
            self.assertIsNotNone(run_path)

            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(["autopilot", "--cancel", "--run-path", str(run_path), "--reason", "test cancellation"])
            finally:
                cli.ROOT = original_root

            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(exit_code, 0)
            self.assertIn("cancel requested", output.getvalue())
            result = result_holder["result"]
            self.assertEqual(getattr(result, "status"), "cancelled")
            self.assertEqual(getattr(result, "cancelled"), ["TASK-0001"])
            self.assertFalse(finished_marker.exists())
            metadata = load_data(run_path / "metadata.json")
            self.assertEqual(metadata["status"], "cancelled")
            self.assertEqual(metadata["cancelled"], ["TASK-0001"])
            self.assertTrue((run_path / "cancel.json").exists())
            batch_execution = metadata["batch_executions"][0]
            self.assertEqual(batch_execution["status"], "cancelled")
            self.assertEqual(batch_execution["tasks"][0]["status"], "cancelled")

    def test_cli_autopilot_logs_streams_latest_batch_events(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = root / "session_provider.py"
            provider.write_text(
                "import json, sys\n"
                "json.load(sys.stdin)\n"
                "json.dump({'schema_version': 1, 'status': 'launched', 'external_session_id': 's1', 'summary': 'ok'}, sys.stdout)\n",
                encoding="utf-8",
            )
            config = deepcopy(DEFAULT_CONFIG)
            config["sessions"]["agent_provider"] = "codex"
            config["sessions"]["launch_command"] = f"{shlex.quote(sys.executable)} {shlex.quote(str(provider))}"
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001", priority=1))
            result = run_autopilot(root, config, limit=1, max_steps=1)
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(["autopilot", "--logs", "--run-path", str(result.path)])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 0)
            text = output.getvalue()
            self.assertIn("task_heartbeat", text)
            self.assertIn("task_result", text)
            self.assertIn("TASK-0001", text)

    def test_autopilot_marks_run_paused_when_max_steps_reached_with_work_remaining(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001", priority=1))

            result = run_autopilot(root, deepcopy(DEFAULT_CONFIG), limit=1, max_steps=1)

            metadata = json.loads((result.path / "metadata.json").read_text(encoding="utf-8"))
            ledger_events = [
                json.loads(line)
                for line in (result.path / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(getattr(result, "status", None), "paused")
            self.assertEqual(getattr(result, "pause_reason", None), "max_steps_reached")
            self.assertEqual(metadata["status"], "paused")
            self.assertEqual(metadata["pause_reason"], "max_steps_reached")
            self.assertEqual(ledger_events[-1]["data"]["status"], "paused")

    def test_cli_autopilot_status_reports_latest_run(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            older = root / "harness" / "autopilot-runs" / "2026-05-30T00-00-00Z-autopilot"
            newer = root / "harness" / "autopilot-runs" / "2026-05-30T00-01-00Z-autopilot"
            dump_data({"schema_version": 1, "run_id": older.name, "status": "failed", "steps": 1}, older / "metadata.json")
            dump_data(
                {
                    "schema_version": 1,
                    "run_id": newer.name,
                    "status": "finished",
                    "steps": 3,
                    "planned": ["TASK-0002"],
                    "actions": ["TASK-0001:repair:implementer"],
                    "dispatched": ["TASK-0001"],
                    "failed": [],
                    "blocked": [],
                    "release": "harness/release-runs/release-1/output.json",
                    "release_status": "failed",
                    "release_repair_planner": "harness/capability-runs/planner-1",
                    "releaser": "harness/capability-runs/releaser-1/output.json",
                    "releaser_tasks": ["TASK-0001"],
                },
                newer / "metadata.json",
            )
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(["autopilot", "--status"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 0)
            text = output.getvalue()
            self.assertIn("2026-05-30T00-01-00Z-autopilot", text)
            self.assertIn("status=finished", text)
            self.assertIn("steps=3", text)
            self.assertIn("planned=TASK-0002", text)
            self.assertIn("release_status=failed", text)
            self.assertIn("release=harness/release-runs/release-1/output.json", text)
            self.assertIn("release_repair_planner=harness/capability-runs/planner-1", text)
            self.assertIn("releaser=harness/capability-runs/releaser-1/output.json", text)
            self.assertIn("releaser_tasks=TASK-0001", text)

    def test_cli_autopilot_status_prints_pause_reason(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_path = root / "harness" / "autopilot-runs" / "2026-05-30T00-01-00Z-autopilot"
            dump_data(
                {
                    "schema_version": 1,
                    "run_id": run_path.name,
                    "status": "paused",
                    "pause_reason": "external_status_pending",
                    "steps": 9,
                    "actions": ["TASK-0001:ci_status"],
                    "dispatched": ["TASK-0001"],
                    "failed": [],
                    "blocked": [],
                },
                run_path / "metadata.json",
            )
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(["autopilot", "--status"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 0)
            text = output.getvalue()
            self.assertIn("status=paused", text)
            self.assertIn("pause_reason=external_status_pending", text)

    def test_cli_autopilot_status_json_reports_latest_run_metadata(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_path = root / "harness" / "autopilot-runs" / "2026-05-30T00-01-00Z-autopilot"
            dump_data(
                {"schema_version": 1, "run_id": run_path.name, "status": "blocked", "steps": 2, "blocked": ["TASK-0001"]},
                run_path / "metadata.json",
            )
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(["autopilot", "--status", "--json"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["run_id"], run_path.name)
            self.assertEqual(payload["status"], "blocked")
            self.assertEqual(payload["blocked"], ["TASK-0001"])

    def test_cli_autopilot_resume_reuses_latest_run_and_appends_ledger(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "harness.yml"
            capability_script = root / "capability_stub.py"
            write_capability_stub(capability_script)
            command = f"{shlex.quote(sys.executable)} {shlex.quote(str(capability_script))}"
            config = deepcopy(DEFAULT_CONFIG)
            for command_name in config["commands"]:
                config["commands"][command_name] = None
            for capability in ("bdd", "tdd", "implementer", "reviewer"):
                config["capabilities"][capability]["command"] = command
            dump_data(
                {
                    "schema_version": 1,
                    "paths": config["paths"],
                    "commands": config["commands"],
                    "sessions": config["sessions"],
                    "autopilot": config["autopilot"],
                    "capabilities": config["capabilities"],
                },
                config_path,
            )
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001", priority=1))
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                with redirect_stdout(io.StringIO()):
                    first_exit = cli.main(["autopilot", "--run", "--limit", "1", "--max-steps", "1"])
                run_dirs = sorted((root / "harness" / "autopilot-runs").glob("*"))
                self.assertEqual(len(run_dirs), 1)
                first_ledger_lines = (run_dirs[0] / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
                output = io.StringIO()
                with redirect_stdout(output):
                    second_exit = cli.main(["autopilot", "--resume", "--max-steps", "8"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(first_exit, 0)
            self.assertEqual(second_exit, 0)
            self.assertIn("autopilot run:", output.getvalue())
            self.assertEqual(len(sorted((root / "harness" / "autopilot-runs").glob("*"))), 1)
            self.assertTrue((root / "harness" / "tasks" / "done" / "TASK-0001.json").exists())
            metadata = json.loads((run_dirs[0] / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["status"], "finished")
            self.assertEqual(metadata["dispatched"], ["TASK-0001"])
            self.assertGreater(metadata["steps"], 1)
            ledger_lines = (run_dirs[0] / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertGreater(len(ledger_lines), len(first_ledger_lines))
            self.assertEqual(json.loads(ledger_lines[-1])["event"], "autopilot_finished")

    def test_autopilot_repeated_resume_pressure_records_resume_count_and_finishes_same_run(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            capability_script = root / "capability_stub.py"
            write_capability_stub(capability_script)
            command = f"{shlex.quote(sys.executable)} {shlex.quote(str(capability_script))}"
            config = deepcopy(DEFAULT_CONFIG)
            for command_name in config["commands"]:
                config["commands"][command_name] = None
            for capability in ("bdd", "tdd", "implementer", "reviewer", "verifier"):
                config["capabilities"][capability]["command"] = command
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001", priority=1))

            result = run_autopilot(root, config, limit=1, max_steps=1)
            resume_count = 0
            while result.status == "paused" and resume_count < 20:
                resume_count += 1
                result = run_autopilot(root, config, limit=1, max_steps=1, resume_path=result.path)

            metadata = load_data(result.path / "metadata.json")
            ledger_events = [
                json.loads(line)["event"]
                for line in (result.path / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(result.status, "finished")
            self.assertEqual(metadata["status"], "finished")
            self.assertEqual(metadata["resume_count"], resume_count)
            self.assertGreaterEqual(metadata["resume_count"], 8)
            self.assertEqual(ledger_events.count("autopilot_resumed"), metadata["resume_count"])
            self.assertEqual(len(sorted((root / "harness" / "autopilot-runs").glob("*"))), 1)

    def test_autopilot_resume_migrates_legacy_metadata_defaults(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_path = root / "harness" / "autopilot-runs" / "legacy-run"
            dump_data(
                {
                    "schema_version": 1,
                    "run_id": "legacy-run",
                    "status": "paused",
                    "steps": 0,
                },
                run_path / "metadata.json",
            )

            result = run_autopilot(root, deepcopy(DEFAULT_CONFIG), limit=1, max_steps=1, resume_path=run_path)

            self.assertEqual(result.failed, [])
            metadata = load_data(run_path / "metadata.json")
            self.assertEqual(metadata["schema_version"], 1)
            self.assertEqual(metadata["state_machine"]["version"], 1)
            self.assertEqual(metadata["actions"], [])
            self.assertEqual(metadata["dispatched"], [])
            self.assertEqual(metadata["releaser_tasks"], [])

    def test_cli_autopilot_loop_resumes_until_finished_with_cycle_limit(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "harness.yml"
            capability_script = root / "capability_stub.py"
            write_capability_stub(capability_script)
            command = f"{shlex.quote(sys.executable)} {shlex.quote(str(capability_script))}"
            config = deepcopy(DEFAULT_CONFIG)
            for command_name in config["commands"]:
                config["commands"][command_name] = None
            for capability in ("bdd", "tdd", "implementer", "reviewer"):
                config["capabilities"][capability]["command"] = command
            dump_data(
                {
                    "schema_version": 1,
                    "paths": config["paths"],
                    "commands": config["commands"],
                    "sessions": config["sessions"],
                    "autopilot": config["autopilot"],
                    "capabilities": config["capabilities"],
                },
                config_path,
            )
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001", priority=1))
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(
                        [
                            "autopilot",
                            "--run",
                            "--loop",
                            "--limit",
                            "1",
                            "--max-steps",
                            "1",
                            "--max-cycles",
                            "9",
                        ]
                    )
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 0)
            self.assertTrue((root / "harness" / "tasks" / "done" / "TASK-0001.json").exists())
            run_dirs = sorted((root / "harness" / "autopilot-runs").glob("*"))
            self.assertEqual(len(run_dirs), 1)
            metadata = json.loads((run_dirs[0] / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["status"], "finished")
            self.assertEqual(metadata["loop_cycles"], 9)
            text = output.getvalue()
            self.assertIn("loop cycles=9", text)
            self.assertIn("status=finished", text)

    def test_cli_autopilot_loop_records_stop_reason_when_cycle_limit_is_reached(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001", priority=1))
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(
                        [
                            "autopilot",
                            "--run",
                            "--loop",
                            "--limit",
                            "1",
                            "--max-steps",
                            "1",
                            "--max-cycles",
                            "1",
                        ]
                    )
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 0)
            run_dirs = sorted((root / "harness" / "autopilot-runs").glob("*"))
            self.assertEqual(len(run_dirs), 1)
            metadata = json.loads((run_dirs[0] / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["status"], "paused")
            self.assertEqual(metadata["loop_cycles"], 1)
            self.assertEqual(metadata["loop_stop_reason"], "max_cycles_reached")
            text = output.getvalue()
            self.assertIn("loop cycles=1", text)
            self.assertIn("loop_stop_reason=max_cycles_reached", text)

    def test_cli_autopilot_loop_uses_configured_cycle_limit_by_default(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            capability_script = root / "capability_stub.py"
            write_capability_stub(capability_script)
            command = f"{shlex.quote(sys.executable)} {shlex.quote(str(capability_script))}"
            config = deepcopy(DEFAULT_CONFIG)
            for command_name in config["commands"]:
                config["commands"][command_name] = None
            for capability in ("bdd", "tdd", "implementer", "reviewer"):
                config["capabilities"][capability]["command"] = command
            config["autopilot"]["max_loop_cycles"] = 3
            dump_data(
                {
                    "schema_version": 1,
                    "commands": config["commands"],
                    "autopilot": config["autopilot"],
                    "capabilities": config["capabilities"],
                },
                root / "harness.yml",
            )
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001", priority=1))
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(
                        [
                            "autopilot",
                            "--run",
                            "--loop",
                            "--limit",
                            "1",
                            "--max-steps",
                            "1",
                        ]
                    )
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 0)
            run_dirs = sorted((root / "harness" / "autopilot-runs").glob("*"))
            self.assertEqual(len(run_dirs), 1)
            metadata = json.loads((run_dirs[0] / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["status"], "paused")
            self.assertEqual(metadata["loop_cycles"], 3)
            self.assertEqual(metadata["loop_stop_reason"], "max_cycles_reached")
            self.assertIn("loop cycles=3", output.getvalue())

    def test_cli_autopilot_dry_run_uses_configured_limit_by_default(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            dump_data({"schema_version": 1, "autopilot": {"default_limit": 2}}, root / "harness.yml")
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001", priority=1))
            write_task(root, "ready", "TASK-0002", ready_task("TASK-0002", priority=2))
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(["autopilot", "--dry-run", "--json"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["limit"], 2)
            self.assertEqual([task["id"] for task in payload["batches"][0]["tasks"]], ["TASK-0001", "TASK-0002"])

    def test_cli_autopilot_run_uses_configured_max_steps_by_default(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            capability_script = root / "capability_stub.py"
            write_capability_stub(capability_script)
            command = f"{shlex.quote(sys.executable)} {shlex.quote(str(capability_script))}"
            config = deepcopy(DEFAULT_CONFIG)
            for command_name in config["commands"]:
                config["commands"][command_name] = None
            for capability in ("bdd", "tdd", "implementer", "reviewer"):
                config["capabilities"][capability]["command"] = command
            config["autopilot"]["max_steps"] = 9
            dump_data(
                {
                    "schema_version": 1,
                    "commands": config["commands"],
                    "autopilot": config["autopilot"],
                    "capabilities": config["capabilities"],
                },
                root / "harness.yml",
            )
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001", priority=1))
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(["autopilot", "--run", "--limit", "1"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 0)
            self.assertIn("status=finished", output.getvalue())
            self.assertTrue((root / "harness" / "tasks" / "done" / "TASK-0001.json").exists())

    def test_autopilot_resource_budget_caps_ready_batch_by_model_and_test_cost(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = deepcopy(DEFAULT_CONFIG)
            config["autopilot"]["resources"] = {"model_concurrency": 4, "max_test_cost": 3}
            first = ready_task("TASK-0001", priority=1)
            first["files"]["write"] = ["src/one.py"]
            first["estimate"] = {"test_cost": 2}
            second = ready_task("TASK-0002", priority=2)
            second["files"]["write"] = ["src/two.py"]
            second["estimate"] = {"test_cost": 2}
            write_task(root, "ready", "TASK-0001", first)
            write_task(root, "ready", "TASK-0002", second)

            plan = build_execution_plan(root, config, limit=2)

            self.assertEqual([[task.task_id for task in batch.tasks] for batch in plan.batches], [["TASK-0001"], ["TASK-0002"]])

    def test_autopilot_recovers_stale_file_lock_before_dispatch(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = deepcopy(DEFAULT_CONFIG)
            for command_name in config["commands"]:
                config["commands"][command_name] = None
            task = ready_task("TASK-0001", priority=1)
            write_task(root, "ready", "TASK-0001", task)
            stale_lock = file_lock_path(root, config, "src/TASK-0001.py")
            stale_lock.parent.mkdir(parents=True, exist_ok=True)
            stale_lock.write_text("TASK-9999\n", encoding="utf-8")

            result = run_autopilot(root, config, limit=1, max_steps=1)

            self.assertEqual(result.failed, [])
            self.assertEqual(result.dispatched, ["TASK-0001"])
            self.assertEqual(stale_lock.read_text(encoding="utf-8").strip(), "TASK-0001")
            ledger_events = [
                json.loads(line)["event"]
                for line in (result.path / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertIn("recovery_stale_lock_released", ledger_events)

    def test_autopilot_recovers_orphan_in_progress_run_by_requeueing_task(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = deepcopy(DEFAULT_CONFIG)
            for command_name in config["commands"]:
                config["commands"][command_name] = None
            task = active_task(ready_task("TASK-0001", priority=1))
            task["evidence"]["run_id"] = "missing-run"
            task["evidence"]["session"] = "harness/runs/missing-run/session.yml"
            task["evidence"]["packet"] = "harness/runs/missing-run/evidence.md"
            write_task(root, "in_progress", "TASK-0001", task)
            task_lock = task_lock_path(root, config, "TASK-0001")
            task_lock.parent.mkdir(parents=True, exist_ok=True)
            task_lock.write_text("missing-run\n", encoding="utf-8")
            stale_file_lock = file_lock_path(root, config, "src/TASK-0001.py")
            stale_file_lock.parent.mkdir(parents=True, exist_ok=True)
            stale_file_lock.write_text("TASK-0001\n", encoding="utf-8")

            result = run_autopilot(root, config, limit=1, max_steps=1)

            self.assertEqual(result.failed, [])
            self.assertEqual(result.dispatched, ["TASK-0001"])
            updated = load_data(root / "harness" / "tasks" / "in_progress" / "TASK-0001.json")
            self.assertNotEqual(updated["evidence"]["run_id"], "missing-run")
            ledger_events = [
                json.loads(line)["event"]
                for line in (result.path / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertIn("recovery_orphan_run_requeued", ledger_events)

    def test_autopilot_recovers_missing_task_worktree_to_control_root(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = deepcopy(DEFAULT_CONFIG)
            for command_name in config["commands"]:
                config["commands"][command_name] = None
            capability_script = root / "capability_stub.py"
            write_capability_stub(capability_script)
            config["capabilities"]["bdd"]["command"] = f"{shlex.quote(sys.executable)} {shlex.quote(str(capability_script))}"
            task = active_task(ready_task("TASK-0001", priority=1))
            task["evidence"]["run_id"] = "RUN-0001"
            task["evidence"]["session"] = "harness/runs/RUN-0001/session.yml"
            task["evidence"]["worktree"] = str(root / "missing-worktree")
            write_task(root, "in_progress", "TASK-0001", task)
            dump_data(
                {
                    "schema_version": 1,
                    "run_id": "RUN-0001",
                    "task_id": "TASK-0001",
                    "workspace": {"root": str(root / "missing-worktree"), "worktree": str(root / "missing-worktree")},
                },
                root / "harness" / "runs" / "RUN-0001" / "metadata.yml",
            )

            result = run_autopilot(root, config, limit=1, max_steps=1)

            self.assertEqual(result.failed, [])
            updated = load_data(root / "harness" / "tasks" / "in_progress" / "TASK-0001.json")
            self.assertIn("bdd", updated["evidence"]["capabilities"])
            metadata = load_data(root / "harness" / "runs" / "RUN-0001" / "metadata.yml")
            self.assertEqual(metadata["workspace"]["root"], str(root))
            self.assertIsNone(metadata["workspace"]["worktree"])

    def test_autopilot_run_prioritizes_in_progress_capability_before_new_dispatch(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = deepcopy(DEFAULT_CONFIG)
            capability_script = root / "capability_stub.py"
            write_capability_stub(capability_script)
            config["capabilities"]["bdd"]["command"] = f"{shlex.quote(sys.executable)} {shlex.quote(str(capability_script))}"
            active = active_task(ready_task("TASK-0001", priority=1))
            ready = ready_task("TASK-0002", priority=2)
            write_task(root, "in_progress", "TASK-0001", active)
            write_task(root, "ready", "TASK-0002", ready)

            result = run_autopilot(root, config, limit=1, max_steps=1)

            self.assertEqual(result.dispatched, [])
            self.assertTrue((root / "harness" / "tasks" / "ready" / "TASK-0002.json").exists())
            updated = json.loads((root / "harness" / "tasks" / "in_progress" / "TASK-0001.json").read_text(encoding="utf-8"))
            self.assertIn("bdd", updated["evidence"]["capabilities"])
            ledger_events = [
                json.loads(line)
                for line in (result.path / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertIn("task_action_planned", [event["event"] for event in ledger_events])
            self.assertIn("capability_finished", [event["event"] for event in ledger_events])

    def test_autopilot_runs_independent_active_task_actions_as_one_batch(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = deepcopy(DEFAULT_CONFIG)
            for command_name in config["commands"]:
                config["commands"][command_name] = None
            capability_script = root / "capability_stub.py"
            write_capability_stub(capability_script)
            config["capabilities"]["bdd"]["command"] = f"{shlex.quote(sys.executable)} {shlex.quote(str(capability_script))}"
            first = active_task(ready_task("TASK-0001", priority=1))
            first["files"]["write"] = ["src/one.py"]
            second = active_task(ready_task("TASK-0002", priority=2))
            second["files"]["write"] = ["src/two.py"]
            write_task(root, "in_progress", "TASK-0001", first)
            write_task(root, "in_progress", "TASK-0002", second)

            result = run_autopilot(root, config, limit=2, max_steps=1)

            self.assertEqual(result.failed, [])
            self.assertEqual(result.steps, 1)
            self.assertEqual(
                result.actions,
                [
                    "TASK-0001:run_capability:bdd",
                    "TASK-0002:run_capability:bdd",
                ],
            )
            for task_id in ("TASK-0001", "TASK-0002"):
                task = json.loads((root / "harness" / "tasks" / "in_progress" / f"{task_id}.json").read_text(encoding="utf-8"))
                self.assertIn("bdd", task["evidence"]["capabilities"])

    def test_dry_run_does_not_treat_failed_capability_evidence_as_complete(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = active_task(ready_task("TASK-0001", priority=1))
            failed_output = root / "harness" / "capability-runs" / "bdd-TASK-0001" / "output.json"
            dump_data(
                {
                    "schema_version": 1,
                    "status": "failed",
                    "summary": "bdd scenarios were not adequate",
                    "findings": [],
                    "evidence": [],
                },
                failed_output,
            )
            task["evidence"]["capabilities"] = {
                "bdd": str(failed_output.relative_to(root)),
            }
            write_task(root, "in_progress", "TASK-0001", task)

            plan = build_execution_plan(root, DEFAULT_CONFIG, limit=1)

            self.assertEqual(len(plan.actions), 1)
            self.assertEqual(plan.actions[0].capability, "bdd")

    def test_autopilot_repairs_after_verify_failure_then_retries_verify(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = deepcopy(DEFAULT_CONFIG)
            for command_name in config["commands"]:
                config["commands"][command_name] = None
            capability_script = root / "capability_stub.py"
            write_capability_stub(capability_script)
            verify_script = root / "verify_once_then_pass.py"
            verify_script.write_text(
                "\n".join(
                    [
                        "from pathlib import Path",
                        "path = Path('unit-attempts.txt')",
                        "attempts = int(path.read_text()) if path.exists() else 0",
                        "path.write_text(str(attempts + 1))",
                        "raise SystemExit(1 if attempts == 0 else 0)",
                    ]
                ),
                encoding="utf-8",
            )
            capability_command = f"{shlex.quote(sys.executable)} {shlex.quote(str(capability_script))}"
            for capability in ("bdd", "tdd", "implementer", "reviewer"):
                config["capabilities"][capability]["command"] = capability_command
            config["commands"]["unit"] = f"{shlex.quote(sys.executable)} {shlex.quote(str(verify_script))}"
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001", priority=1))

            result = run_autopilot(root, config, limit=1, max_steps=14)

            self.assertEqual(result.failed, [])
            self.assertTrue((root / "harness" / "tasks" / "done" / "TASK-0001.json").exists())
            self.assertEqual((root / "unit-attempts.txt").read_text(encoding="utf-8"), "2")
            metadata = json.loads((result.path / "metadata.json").read_text(encoding="utf-8"))
            self.assertIn("TASK-0001:repair:tdd", metadata["actions"])
            ledger_events = [
                json.loads(line)["event"]
                for line in (result.path / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertIn("repair_requested", ledger_events)
            self.assertIn("repair_finished", ledger_events)

    def test_autopilot_repairs_failed_bdd_with_bdd_capability(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = deepcopy(DEFAULT_CONFIG)
            for command_name in config["commands"]:
                config["commands"][command_name] = None
            attempts_path = root / "bdd-attempts.txt"
            bdd_script = root / "bdd_stub.py"
            bdd_script.write_text(
                "\n".join(
                    [
                        "import json, sys",
                        "from pathlib import Path",
                        "payload = json.load(sys.stdin)",
                        f"attempts_path = Path({str(attempts_path)!r})",
                        "attempts = int(attempts_path.read_text(encoding='utf-8')) if attempts_path.exists() else 0",
                        "attempts_path.write_text(str(attempts + 1), encoding='utf-8')",
                        "status = 'failed' if attempts == 0 else 'passed'",
                        "json.dump({",
                        "    'schema_version': 1,",
                        "    'status': status,",
                        "    'summary': f'bdd {status}',",
                        "    'findings': [],",
                        "    'evidence': ['bdd evidence'],",
                        "    'artifacts': {",
                        "        'scenarios': [{'name': 'Retry BDD', 'given': 'a task', 'when': 'bdd runs', 'then': 'scenarios exist'}],",
                        "        'updated_files': payload['task']['files']['write'],",
                        "        'requirements_mapping': [{'requirement': 'needs ordered batches', 'scenarios': ['Retry BDD']}],",
                        "        'uncovered_behaviors': []",
                        "    }",
                        "}, sys.stdout)",
                    ]
                ),
                encoding="utf-8",
            )
            config["capabilities"]["bdd"]["command"] = f"{shlex.quote(sys.executable)} {shlex.quote(str(bdd_script))}"
            write_task(root, "in_progress", "TASK-0001", active_task(ready_task("TASK-0001", priority=1)))

            result = run_autopilot(root, config, limit=1, max_steps=2)

            self.assertEqual(result.failed, [])
            metadata = json.loads((result.path / "metadata.json").read_text(encoding="utf-8"))
            self.assertIn("TASK-0001:repair:bdd", metadata["actions"])
            updated = json.loads((root / "harness" / "tasks" / "in_progress" / "TASK-0001.json").read_text(encoding="utf-8"))
            self.assertEqual(updated["evidence"]["autopilot"]["repair"]["target_capability"], "bdd")

    def test_autopilot_run_can_drive_ready_task_to_done_with_configured_capabilities(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = deepcopy(DEFAULT_CONFIG)
            for command_name in config["commands"]:
                config["commands"][command_name] = None
            capability_script = root / "capability_stub.py"
            write_capability_stub(capability_script)
            command = f"{shlex.quote(sys.executable)} {shlex.quote(str(capability_script))}"
            for capability in ("bdd", "tdd", "implementer", "reviewer"):
                config["capabilities"][capability]["command"] = command
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001", priority=1))

            result = run_autopilot(root, config, limit=1, max_steps=9)

            self.assertEqual(result.failed, [])
            self.assertEqual(result.blocked, [])
            self.assertEqual(result.dispatched, ["TASK-0001"])
            self.assertTrue((root / "harness" / "tasks" / "done" / "TASK-0001.json").exists())
            metadata = json.loads((result.path / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["status"], "finished")
            self.assertIn("TASK-0001:close_task:done", metadata["actions"])

    def test_autopilot_runs_configured_verifier_before_local_verify(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = deepcopy(DEFAULT_CONFIG)
            for command_name in config["commands"]:
                config["commands"][command_name] = None
            capability_script = root / "capability_stub.py"
            write_capability_stub(capability_script)
            command = f"{shlex.quote(sys.executable)} {shlex.quote(str(capability_script))}"
            for capability in ("bdd", "tdd", "implementer", "reviewer", "verifier"):
                config["capabilities"][capability]["command"] = command
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001", priority=1))

            result = run_autopilot(root, config, limit=1, max_steps=10)

            self.assertEqual(result.failed, [])
            done_path = root / "harness" / "tasks" / "done" / "TASK-0001.json"
            self.assertTrue(done_path.exists())
            task = json.loads(done_path.read_text(encoding="utf-8"))
            self.assertIn("verifier", task["evidence"]["capabilities"])
            metadata = json.loads((result.path / "metadata.json").read_text(encoding="utf-8"))
            actions = metadata["actions"]
            self.assertLess(
                actions.index("TASK-0001:run_capability:verifier"),
                actions.index("TASK-0001:verify_task:verified"),
            )

    def test_autopilot_collects_ci_evidence_before_closing_accepted_task(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = deepcopy(DEFAULT_CONFIG)
            for command_name in config["commands"]:
                config["commands"][command_name] = None
            capability_script = root / "capability_stub.py"
            write_capability_stub(capability_script)
            ci_script = root / "ci_stub.py"
            ci_script.write_text(
                "\n".join(
                    [
                        "import json",
                        "print(json.dumps({",
                        "    'schema_version': 1,",
                        "    'status': 'passed',",
                        "    'summary': 'ci passed',",
                        "    'checks': []",
                        "}))",
                    ]
                ),
                encoding="utf-8",
            )
            command = f"{shlex.quote(sys.executable)} {shlex.quote(str(capability_script))}"
            for capability in ("bdd", "tdd", "implementer", "reviewer"):
                config["capabilities"][capability]["command"] = command
            config["integrations"]["ci_provider"] = {
                "provider": "command",
                "command": f"{shlex.quote(sys.executable)} {shlex.quote(str(ci_script))}",
            }
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001", priority=1))

            result = run_autopilot(root, config, limit=1, max_steps=11)

            self.assertEqual(result.failed, [])
            done_path = root / "harness" / "tasks" / "done" / "TASK-0001.json"
            self.assertTrue(done_path.exists())
            task = json.loads(done_path.read_text(encoding="utf-8"))
            self.assertIn("ci", task["evidence"])
            self.assertTrue((root / task["evidence"]["ci"]).exists())
            metadata = json.loads((result.path / "metadata.json").read_text(encoding="utf-8"))
            self.assertIn("TASK-0001:ci_status", metadata["actions"])

    def test_autopilot_pauses_pending_ci_status_then_resumes_until_passed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = deepcopy(DEFAULT_CONFIG)
            for command_name in config["commands"]:
                config["commands"][command_name] = None
            capability_script = root / "capability_stub.py"
            write_capability_stub(capability_script)
            attempts_path = root / "ci-attempts.txt"
            ci_script = root / "ci_stub.py"
            ci_script.write_text(
                "\n".join(
                    [
                        "import json",
                        "from pathlib import Path",
                        f"attempts_path = Path({str(attempts_path)!r})",
                        "attempts = int(attempts_path.read_text(encoding='utf-8')) if attempts_path.exists() else 0",
                        "attempts_path.write_text(str(attempts + 1), encoding='utf-8')",
                        "status = 'running' if attempts == 0 else 'passed'",
                        "print(json.dumps({",
                        "    'schema_version': 1,",
                        "    'status': status,",
                        "    'summary': f'ci {status}',",
                        "    'checks': []",
                        "}))",
                    ]
                ),
                encoding="utf-8",
            )
            command = f"{shlex.quote(sys.executable)} {shlex.quote(str(capability_script))}"
            for capability in ("bdd", "tdd", "implementer", "reviewer"):
                config["capabilities"][capability]["command"] = command
            config["integrations"]["ci_provider"] = {
                "provider": "command",
                "command": f"{shlex.quote(sys.executable)} {shlex.quote(str(ci_script))}",
            }
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001", priority=1))

            first = run_autopilot(root, config, limit=1, max_steps=9)
            first_metadata = json.loads((first.path / "metadata.json").read_text(encoding="utf-8"))
            second = run_autopilot(root, config, limit=1, max_steps=2, resume_path=first.path)

            self.assertEqual(first.status, "paused")
            self.assertEqual(first.pause_reason, "external_status_pending")
            self.assertEqual(first_metadata["status"], "paused")
            self.assertEqual(first_metadata["pause_reason"], "external_status_pending")
            self.assertEqual(second.failed, [])
            self.assertEqual(second.blocked, [])
            self.assertEqual(second.status, "finished")
            self.assertEqual(attempts_path.read_text(encoding="utf-8"), "2")
            self.assertTrue((root / "harness" / "tasks" / "done" / "TASK-0001.json").exists())

    def test_autopilot_requests_repair_after_failed_ci_status(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = deepcopy(DEFAULT_CONFIG)
            ci_script = root / "ci_stub.py"
            ci_script.write_text(
                "\n".join(
                    [
                        "import json",
                        "print(json.dumps({",
                        "    'schema_version': 1,",
                        "    'status': 'failed',",
                        "    'summary': 'ci failed',",
                        "    'checks': []",
                        "}))",
                    ]
                ),
                encoding="utf-8",
            )
            config["integrations"]["ci_provider"] = {
                "provider": "command",
                "command": f"{shlex.quote(sys.executable)} {shlex.quote(str(ci_script))}",
            }
            task = active_task(ready_task("TASK-0001", priority=1), state="accepted")
            task["evidence"]["run_id"] = "RUN-0001"
            task["evidence"]["session"] = "harness/runs/RUN-0001/session.yml"
            task["evidence"]["packet"] = "harness/runs/RUN-0001/evidence.md"
            write_task(root, "accepted", "TASK-0001", task)

            result = run_autopilot(root, config, limit=1, max_steps=1)

            self.assertEqual(result.failed, [])
            self.assertEqual(result.status, "paused")
            self.assertEqual(result.pause_reason, "max_steps_reached")
            in_progress_path = root / "harness" / "tasks" / "in_progress" / "TASK-0001.json"
            self.assertTrue(in_progress_path.exists())
            updated = json.loads(in_progress_path.read_text(encoding="utf-8"))
            repair = updated["evidence"]["autopilot"]["repair"]
            self.assertEqual(repair["status"], "pending")
            self.assertEqual(repair["source_action"], "ci_status")
            metadata = json.loads((result.path / "metadata.json").read_text(encoding="utf-8"))
            self.assertIn("TASK-0001:ci_status", metadata["actions"])
            ledger_events = [
                json.loads(line)["event"]
                for line in (result.path / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertIn("repair_requested", ledger_events)

    def test_autopilot_collects_pr_evidence_before_closing_accepted_task(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = deepcopy(DEFAULT_CONFIG)
            for command_name in config["commands"]:
                config["commands"][command_name] = None
            capability_script = root / "capability_stub.py"
            write_capability_stub(capability_script)
            pr_script = root / "pr_stub.py"
            pr_script.write_text(
                "\n".join(
                    [
                        "import json",
                        "print(json.dumps({",
                        "    'schema_version': 1,",
                        "    'status': 'merged',",
                        "    'summary': 'pr merged',",
                        "    'checks': []",
                        "}))",
                    ]
                ),
                encoding="utf-8",
            )
            command = f"{shlex.quote(sys.executable)} {shlex.quote(str(capability_script))}"
            for capability in ("bdd", "tdd", "implementer", "reviewer"):
                config["capabilities"][capability]["command"] = command
            config["integrations"]["pr_provider"] = {
                "provider": "command",
                "command": f"{shlex.quote(sys.executable)} {shlex.quote(str(pr_script))}",
            }
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001", priority=1))

            result = run_autopilot(root, config, limit=1, max_steps=11)

            self.assertEqual(result.failed, [])
            done_path = root / "harness" / "tasks" / "done" / "TASK-0001.json"
            self.assertTrue(done_path.exists())
            task = json.loads(done_path.read_text(encoding="utf-8"))
            self.assertIn("pr", task["evidence"])
            self.assertIn("pr_request", task["evidence"])
            self.assertTrue((root / task["evidence"]["pr"]).exists())
            self.assertTrue((root / task["evidence"]["pr_request"]).exists())
            metadata = json.loads((result.path / "metadata.json").read_text(encoding="utf-8"))
            self.assertIn("TASK-0001:pr_ensure", metadata["actions"])
            self.assertIn("TASK-0001:pr_status", metadata["actions"])

    def test_autopilot_pauses_unknown_pr_status_instead_of_failing(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = deepcopy(DEFAULT_CONFIG)
            pr_script = root / "pr_stub.py"
            pr_script.write_text(
                "\n".join(
                    [
                        "import json, sys",
                        "payload = json.load(sys.stdin)",
                        "status = 'open' if payload['action'] == 'ensure' else 'unknown'",
                        "print(json.dumps({",
                        "    'schema_version': 1,",
                        "    'status': status,",
                        "    'summary': f'pr {status}',",
                        "    'checks': []",
                        "}))",
                    ]
                ),
                encoding="utf-8",
            )
            config["integrations"]["pr_provider"] = {
                "provider": "command",
                "command": f"{shlex.quote(sys.executable)} {shlex.quote(str(pr_script))}",
            }
            task = active_task(ready_task("TASK-0001", priority=1), state="accepted")
            write_task(root, "accepted", "TASK-0001", task)

            result = run_autopilot(root, config, limit=1, max_steps=2)

            self.assertEqual(result.failed, [])
            self.assertEqual(result.blocked, [])
            self.assertEqual(result.status, "paused")
            self.assertEqual(result.pause_reason, "external_status_pending")
            accepted_path = root / "harness" / "tasks" / "accepted" / "TASK-0001.json"
            updated = json.loads(accepted_path.read_text(encoding="utf-8"))
            self.assertIn("pr_request", updated["evidence"])
            self.assertIn("pr", updated["evidence"])

    def test_autopilot_runs_pr_ensure_before_pr_status(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = deepcopy(DEFAULT_CONFIG)
            for command_name in config["commands"]:
                config["commands"][command_name] = None
            capability_script = root / "capability_stub.py"
            write_capability_stub(capability_script)
            pr_script = root / "pr_stub.py"
            log_path = root / "pr-actions.jsonl"
            pr_script.write_text(
                "\n".join(
                    [
                        "import json, sys",
                        "from pathlib import Path",
                        "payload = json.load(sys.stdin)",
                        f"Path({str(log_path)!r}).open('a', encoding='utf-8').write(json.dumps(payload) + '\\n')",
                        "status = 'open' if payload.get('action') == 'ensure' else 'merged'",
                        "print(json.dumps({",
                        "    'schema_version': 1,",
                        "    'status': status,",
                        "    'summary': f'pr {status}',",
                        "    'checks': []",
                        "}))",
                    ]
                ),
                encoding="utf-8",
            )
            config["integrations"]["pr_provider"] = {
                "provider": "command",
                "command": f"{shlex.quote(sys.executable)} {shlex.quote(str(pr_script))}",
            }
            command = f"{shlex.quote(sys.executable)} {shlex.quote(str(capability_script))}"
            for capability in ("bdd", "tdd", "implementer", "reviewer"):
                config["capabilities"][capability]["command"] = command
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001", priority=1))

            result = run_autopilot(root, config, limit=1, max_steps=11)

            self.assertEqual(result.failed, [])
            self.assertEqual(result.blocked, [])
            calls = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([call["action"] for call in calls], ["ensure", "status"])
            self.assertEqual([call["task_id"] for call in calls], ["TASK-0001", "TASK-0001"])
            done = json.loads((root / "harness" / "tasks" / "done" / "TASK-0001.json").read_text(encoding="utf-8"))
            self.assertIn("pr_request", done["evidence"])
            self.assertIn("pr", done["evidence"])

    def test_autopilot_auto_merges_pr_after_passing_ci(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = deepcopy(DEFAULT_CONFIG)
            config["policies"]["require_fresh_verify_for_done"] = False
            config["policies"]["require_agent_session_for_task"] = False
            pr_script = root / "pr_stub.py"
            ci_script = root / "ci_stub.py"
            action_log = root / "delivery-actions.jsonl"
            pr_script.write_text(
                "\n".join(
                    [
                        "import json, sys",
                        "from pathlib import Path",
                        "payload = json.load(sys.stdin)",
                        f"Path({str(action_log)!r}).open('a', encoding='utf-8').write(json.dumps({{'action': payload['action'], 'task_id': payload['task_id']}}) + '\\n')",
                        "status = {'ensure': 'open', 'merge': 'merged', 'status': 'merged'}[payload['action']]",
                        "print(json.dumps({",
                        "    'schema_version': 1,",
                        "    'status': status,",
                        "    'summary': f'pr {status}',",
                        "    'checks': []",
                        "}))",
                    ]
                ),
                encoding="utf-8",
            )
            ci_script.write_text(
                "\n".join(
                    [
                        "import json",
                        "from pathlib import Path",
                        f"Path({str(action_log)!r}).open('a', encoding='utf-8').write(json.dumps({{'action': 'ci_status', 'task_id': 'TASK-0001'}}) + '\\n')",
                        "print(json.dumps({",
                        "    'schema_version': 1,",
                        "    'provider': 'local-ci',",
                        "    'status': 'passed',",
                        "    'summary': 'ci passed',",
                        "    'checks': []",
                        "}))",
                    ]
                ),
                encoding="utf-8",
            )
            config["integrations"]["ci_provider"] = {
                "provider": "command",
                "command": f"{shlex.quote(sys.executable)} {shlex.quote(str(ci_script))}",
            }
            config["integrations"]["pr_provider"] = {
                "provider": "command",
                "command": f"{shlex.quote(sys.executable)} {shlex.quote(str(pr_script))}",
                "auto_merge": True,
            }
            task = active_task(ready_task("TASK-0001", priority=1), state="accepted")
            packet = root / "harness" / "runs" / "run-TASK-0001" / "evidence.md"
            packet.parent.mkdir(parents=True)
            packet.write_text("- ID: TASK-0001\n- Run: run-TASK-0001\n", encoding="utf-8")
            dump_data(
                {"schema_version": 1, "run_id": "run-TASK-0001", "task_id": "TASK-0001"},
                root / "harness" / "runs" / "run-TASK-0001" / "metadata.yml",
            )
            write_task(root, "accepted", "TASK-0001", task)

            result = run_autopilot(root, config, limit=1, max_steps=5)

            self.assertEqual(result.failed, [])
            self.assertEqual(result.blocked, [])
            calls = [json.loads(line) for line in action_log.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([call["action"] for call in calls], ["ensure", "ci_status", "merge", "status"])
            done = json.loads((root / "harness" / "tasks" / "done" / "TASK-0001.json").read_text(encoding="utf-8"))
            self.assertIn("pr_merge", done["evidence"])
            self.assertIn("pr", done["evidence"])
            metadata = json.loads((result.path / "metadata.json").read_text(encoding="utf-8"))
            self.assertLess(
                metadata["actions"].index("TASK-0001:ci_status"),
                metadata["actions"].index("TASK-0001:pr_merge"),
            )
            self.assertLess(
                metadata["actions"].index("TASK-0001:pr_merge"),
                metadata["actions"].index("TASK-0001:pr_status"),
            )

    def test_autopilot_publishes_git_changes_before_pr_ensure(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = deepcopy(DEFAULT_CONFIG)
            for command_name in config["commands"]:
                config["commands"][command_name] = None
            capability_script = root / "capability_stub.py"
            write_capability_stub(capability_script)
            git_script = root / "git_stub.py"
            pr_script = root / "pr_stub.py"
            action_log = root / "delivery-actions.jsonl"
            git_script.write_text(
                "\n".join(
                    [
                        "import json, sys",
                        "from pathlib import Path",
                        "payload = json.load(sys.stdin)",
                        "assert payload['action'] == 'publish'",
                        f"Path({str(action_log)!r}).open('a', encoding='utf-8').write(json.dumps({{'action': 'publish', 'task_id': payload['task_id']}}) + '\\n')",
                        "print(json.dumps({",
                        "    'schema_version': 1,",
                        "    'provider': 'local-git',",
                        "    'status': 'published',",
                        "    'summary': 'published',",
                        "    'branch': 'codex/publish',",
                        "    'remote': 'origin',",
                        "    'commit_before': 'abc',",
                        "    'commit_after': 'def',",
                        "    'pushed': True,",
                        "    'changes': ['README.md'],",
                        "}))",
                    ]
                ),
                encoding="utf-8",
            )
            pr_script.write_text(
                "\n".join(
                    [
                        "import json, sys",
                        "from pathlib import Path",
                        "payload = json.load(sys.stdin)",
                        f"Path({str(action_log)!r}).open('a', encoding='utf-8').write(json.dumps({{'action': payload['action'], 'task_id': payload['task_id']}}) + '\\n')",
                        "status = 'open' if payload.get('action') == 'ensure' else 'merged'",
                        "print(json.dumps({",
                        "    'schema_version': 1,",
                        "    'status': status,",
                        "    'summary': f'pr {status}',",
                        "    'checks': []",
                        "}))",
                    ]
                ),
                encoding="utf-8",
            )
            config["integrations"]["git_provider"] = {
                "provider": "command",
                "command": f"{shlex.quote(sys.executable)} {shlex.quote(str(git_script))}",
            }
            config["integrations"]["pr_provider"] = {
                "provider": "command",
                "command": f"{shlex.quote(sys.executable)} {shlex.quote(str(pr_script))}",
            }
            command = f"{shlex.quote(sys.executable)} {shlex.quote(str(capability_script))}"
            for capability in ("bdd", "tdd", "implementer", "reviewer"):
                config["capabilities"][capability]["command"] = command
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001", priority=1))

            result = run_autopilot(root, config, limit=1, max_steps=12)

            self.assertEqual(result.failed, [])
            calls = [json.loads(line) for line in action_log.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([call["action"] for call in calls], ["publish", "ensure", "status"])
            done = json.loads((root / "harness" / "tasks" / "done" / "TASK-0001.json").read_text(encoding="utf-8"))
            self.assertIn("git", done["evidence"])
            self.assertIn("pr_request", done["evidence"])
            metadata = json.loads((result.path / "metadata.json").read_text(encoding="utf-8"))
            self.assertLess(
                metadata["actions"].index("TASK-0001:publish_changes"),
                metadata["actions"].index("TASK-0001:pr_ensure"),
            )

    def test_autopilot_applies_worktree_before_pr_or_close_actions(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            _init_git_repo(root)
            config = deepcopy(DEFAULT_CONFIG)
            for command_name in config["commands"]:
                config["commands"][command_name] = None
            config["policies"]["require_fresh_verify_for_done"] = False
            config["sessions"]["worktree"] = {
                "enabled": True,
                "path_template": str(root.parent / "worktrees" / "{task_id}-{run_id}"),
            }
            task = ready_task("TASK-0001", priority=1)
            task["files"]["write"] = ["src/feature.py"]
            write_task(root, "ready", "TASK-0001", task)
            import attestflow.tasks as task_module

            run = task_module.start_task(root, config, "TASK-0001", actor_role="orchestrator")
            task_module.transition_task(root, config, "TASK-0001", "review")
            task_module.transition_task(root, config, "TASK-0001", "verified")
            task_module.transition_task(root, config, "TASK-0001", "accepted")
            run_metadata = load_json_or_yaml(run.path / "metadata.yml")
            worktree = Path(run_metadata["workspace"]["worktree"])
            (worktree / "src").mkdir()
            (worktree / "src" / "feature.py").write_text("VALUE = 42\n", encoding="utf-8")

            result = run_autopilot(root, config, limit=1, max_steps=1)

            self.assertEqual(result.failed, [])
            self.assertEqual((root / "src" / "feature.py").read_text(encoding="utf-8"), "VALUE = 42\n")
            task_path = root / "harness" / "tasks" / "accepted" / "TASK-0001.json"
            self.assertTrue(task_path.exists())
            run_metadata = load_json_or_yaml(run.path / "metadata.yml")
            self.assertTrue(run_metadata["workspace"]["worktree_finalized"])
            self.assertTrue(run_metadata["workspace"]["applied_to_control"])
            metadata = json.loads((result.path / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["actions"], ["TASK-0001:apply_worktree"])

    def test_autopilot_runs_release_after_all_tasks_are_done(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = deepcopy(DEFAULT_CONFIG)
            release_script = root / "release_stub.py"
            release_script.write_text(
                "\n".join(
                    [
                        "import json, sys",
                        "payload = json.load(sys.stdin)",
                        "assert payload['done_tasks'] == ['TASK-0001']",
                        "assert payload['tasks'][0]['id'] == 'TASK-0001'",
                        "print(json.dumps({",
                        "    'schema_version': 1,",
                        "    'status': 'released',",
                        "    'summary': 'release complete',",
                        "    'artifacts': []",
                        "}))",
                    ]
                ),
                encoding="utf-8",
            )
            config["integrations"]["release_provider"] = {
                "provider": "command",
                "command": f"{shlex.quote(sys.executable)} {shlex.quote(str(release_script))}",
            }
            done = completed_task(ready_task("TASK-0001", priority=1))
            write_task(root, "done", "TASK-0001", done)

            result = run_autopilot(root, config, limit=1, max_steps=1)

            self.assertEqual(result.failed, [])
            metadata = json.loads((result.path / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["status"], "finished")
            self.assertIn("release", metadata)
            self.assertEqual(metadata["release_status"], "released")
            self.assertTrue((root / metadata["release"]).exists())
            self.assertIn("autopilot:release_status", metadata["actions"])

    def test_autopilot_runs_configured_releaser_before_release_provider(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = deepcopy(DEFAULT_CONFIG)
            releaser_script = root / "releaser_stub.py"
            releaser_script.write_text(
                "\n".join(
                    [
                        "import json, sys",
                        "payload = json.load(sys.stdin)",
                        "assert payload['done_tasks'] == ['TASK-0001']",
                        "assert payload['tasks'][0]['id'] == 'TASK-0001'",
                        "print(json.dumps({",
                        "    'schema_version': 1,",
                        "    'status': 'passed',",
                        "    'summary': 'release handoff ready',",
                        "    'findings': [],",
                        "    'evidence': ['release notes drafted']",
                        "}))",
                    ]
                ),
                encoding="utf-8",
            )
            release_script = root / "release_stub.py"
            release_script.write_text(
                "\n".join(
                    [
                        "import json, sys",
                        "payload = json.load(sys.stdin)",
                        "handoff = payload['release_handoff']",
                        "assert handoff['output']['status'] == 'passed'",
                        "assert handoff['tasks'] == ['TASK-0001']",
                        "print(json.dumps({",
                        "    'schema_version': 1,",
                        "    'status': 'released',",
                        "    'summary': 'release complete',",
                        "    'artifacts': []",
                        "}))",
                    ]
                ),
                encoding="utf-8",
            )
            config["capabilities"]["releaser"]["command"] = f"{shlex.quote(sys.executable)} {shlex.quote(str(releaser_script))}"
            config["integrations"]["release_provider"] = {
                "provider": "command",
                "command": f"{shlex.quote(sys.executable)} {shlex.quote(str(release_script))}",
            }
            done = completed_task(ready_task("TASK-0001", priority=1))
            write_task(root, "done", "TASK-0001", done)

            result = run_autopilot(root, config, limit=1, max_steps=2)

            self.assertEqual(result.failed, [])
            self.assertEqual(result.release_status, "released")
            metadata = json.loads((result.path / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["releaser_tasks"], ["TASK-0001"])
            self.assertTrue((root / metadata["releaser"]).exists())
            self.assertLess(
                metadata["actions"].index("autopilot:releaser"),
                metadata["actions"].index("autopilot:release_status"),
            )

    def test_autopilot_resume_retries_blocked_release_until_released(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            attempts_path = root / "release-attempts.txt"
            release_script = root / "release_stub.py"
            release_script.write_text(
                "\n".join(
                    [
                        "import json",
                        "from pathlib import Path",
                        f"attempts_path = Path({str(attempts_path)!r})",
                        "attempts = int(attempts_path.read_text(encoding='utf-8')) if attempts_path.exists() else 0",
                        "attempts_path.write_text(str(attempts + 1), encoding='utf-8')",
                        "status = 'blocked' if attempts == 0 else 'released'",
                        "print(json.dumps({",
                        "    'schema_version': 1,",
                        "    'status': status,",
                        "    'summary': f'release {status}',",
                        "    'artifacts': []",
                        "}))",
                    ]
                ),
                encoding="utf-8",
            )
            config = deepcopy(DEFAULT_CONFIG)
            config["integrations"]["release_provider"] = {
                "provider": "command",
                "command": f"{shlex.quote(sys.executable)} {shlex.quote(str(release_script))}",
            }
            done = completed_task(ready_task("TASK-0001", priority=1))
            write_task(root, "done", "TASK-0001", done)

            first = run_autopilot(root, config, limit=1, max_steps=1)
            second = run_autopilot(root, config, limit=1, max_steps=1, resume_path=first.path)

            self.assertEqual(first.blocked, ["release"])
            self.assertEqual(second.blocked, [])
            self.assertEqual(second.failed, [])
            self.assertEqual(attempts_path.read_text(encoding="utf-8"), "2")
            metadata = json.loads((second.path / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["release_status"], "released")
            self.assertEqual(metadata["status"], "finished")
            self.assertEqual(metadata["actions"].count("autopilot:release_status"), 2)

    def test_autopilot_pauses_pending_release_then_resumes_until_released(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            attempts_path = root / "release-attempts.txt"
            release_script = root / "release_stub.py"
            release_script.write_text(
                "\n".join(
                    [
                        "import json",
                        "from pathlib import Path",
                        f"attempts_path = Path({str(attempts_path)!r})",
                        "attempts = int(attempts_path.read_text(encoding='utf-8')) if attempts_path.exists() else 0",
                        "attempts_path.write_text(str(attempts + 1), encoding='utf-8')",
                        "status = 'queued' if attempts == 0 else 'released'",
                        "print(json.dumps({",
                        "    'schema_version': 1,",
                        "    'status': status,",
                        "    'summary': f'release {status}',",
                        "    'artifacts': []",
                        "}))",
                    ]
                ),
                encoding="utf-8",
            )
            config = deepcopy(DEFAULT_CONFIG)
            config["integrations"]["release_provider"] = {
                "provider": "command",
                "command": f"{shlex.quote(sys.executable)} {shlex.quote(str(release_script))}",
            }
            done = completed_task(ready_task("TASK-0001", priority=1))
            write_task(root, "done", "TASK-0001", done)

            first = run_autopilot(root, config, limit=1, max_steps=1)
            first_metadata = json.loads((first.path / "metadata.json").read_text(encoding="utf-8"))
            second = run_autopilot(root, config, limit=1, max_steps=1, resume_path=first.path)

            self.assertEqual(first.failed, [])
            self.assertEqual(first.blocked, [])
            self.assertEqual(first.status, "paused")
            self.assertEqual(first.pause_reason, "external_status_pending")
            self.assertEqual(first_metadata["release_status"], "queued")
            self.assertEqual(first_metadata["status"], "paused")
            self.assertEqual(second.status, "finished")
            self.assertEqual(second.release_status, "released")
            self.assertEqual(attempts_path.read_text(encoding="utf-8"), "2")

    def test_autopilot_plans_repair_task_after_failed_release_when_planner_is_configured(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            release_script = root / "release_stub.py"
            release_script.write_text(
                "\n".join(
                    [
                        "import json",
                        "print(json.dumps({",
                        "    'schema_version': 1,",
                        "    'status': 'failed',",
                        "    'summary': 'deploy failed because migration was missing',",
                        "    'artifacts': []",
                        "}))",
                    ]
                ),
                encoding="utf-8",
            )
            planner_script = root / "planner_stub.py"
            planner_script.write_text(
                "\n".join(
                    [
                        "import json, sys",
                        "payload = json.load(sys.stdin)",
                        "assert 'Release provider reported failed' in payload['goal']",
                        "assert 'deploy failed because migration was missing' in payload['goal']",
                        "print(json.dumps({",
                        "    'schema_version': 1,",
                        "    'tasks': [{",
                        "        'key': 'repair_release',",
                        "        'title': 'Repair failed release',",
                        "        'priority': 1,",
                        "        'type': 'bugfix',",
                        "        'purpose': 'Fix the failed release reported by the release provider.',",
                        "        'context': ['Release provider returned failed.'],",
                        "        'scope': ['Diagnose and fix release failure'],",
                        "        'out_of_scope': ['Unrelated feature work'],",
                        "        'requirements': {'confirmed': ['release provider failed'], 'unresolved': [], 'assumptions': []},",
                        "        'bdd_scenarios': ['Release can be retried successfully.'],",
                        "        'unit_tests': ['tests/unit/test_release_provider.py'],",
                        "        'acceptance': ['release failure is fixed and release can pass'],",
                        "        'dependencies': [],",
                        "        'files': {'read': ['harness/release-runs'], 'write': ['migrations/fix_release.sql']},",
                        "        'external_inputs': {'credentials': [], 'services': [], 'user_decisions': []}",
                        "    }]",
                        "}))",
                    ]
                ),
                encoding="utf-8",
            )
            config = deepcopy(DEFAULT_CONFIG)
            config["integrations"]["release_provider"] = {
                "provider": "command",
                "command": f"{shlex.quote(sys.executable)} {shlex.quote(str(release_script))}",
            }
            config["capabilities"]["planner"]["command"] = f"{shlex.quote(sys.executable)} {shlex.quote(str(planner_script))}"
            done = completed_task(ready_task("TASK-0001", priority=1))
            write_task(root, "done", "TASK-0001", done)

            result = run_autopilot(root, config, limit=1, max_steps=1)

            self.assertEqual(result.failed, [])
            self.assertEqual(result.release_status, "failed")
            self.assertEqual(result.planned, ["TASK-0002"])
            self.assertTrue((root / "harness" / "tasks" / "ready" / "TASK-0002.json").exists())
            metadata = json.loads((result.path / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["status"], "paused")
            self.assertEqual(metadata["pause_reason"], "max_steps_reached")
            self.assertEqual(metadata["release_status"], "failed")
            self.assertEqual(metadata["release_repair_planner"], result.release_repair_planner)
            self.assertIn("autopilot:release_repair_plan", metadata["actions"])

    def test_release_repair_planner_receives_releaser_handoff_context(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            releaser_script = root / "releaser_stub.py"
            releaser_script.write_text(
                "\n".join(
                    [
                        "import json",
                        "print(json.dumps({",
                        "    'schema_version': 1,",
                        "    'status': 'passed',",
                        "    'summary': 'release handoff ready with migration checklist',",
                        "    'findings': [],",
                        "    'evidence': ['migration checklist attached']",
                        "}))",
                    ]
                ),
                encoding="utf-8",
            )
            release_script = root / "release_stub.py"
            release_script.write_text(
                "\n".join(
                    [
                        "import json",
                        "print(json.dumps({",
                        "    'schema_version': 1,",
                        "    'status': 'failed',",
                        "    'summary': 'deploy failed because migration was missing',",
                        "    'artifacts': []",
                        "}))",
                    ]
                ),
                encoding="utf-8",
            )
            planner_script = root / "planner_stub.py"
            planner_script.write_text(
                "\n".join(
                    [
                        "import json, sys",
                        "payload = json.load(sys.stdin)",
                        "assert 'Release provider reported failed' in payload['goal']",
                        "assert 'Release handoff:' in payload['goal']",
                        "assert 'release handoff ready with migration checklist' in payload['goal']",
                        "print(json.dumps({",
                        "    'schema_version': 1,",
                        "    'tasks': [{",
                        "        'key': 'repair_release_with_handoff',",
                        "        'title': 'Repair failed release using handoff context',",
                        "        'priority': 1,",
                        "        'type': 'bugfix',",
                        "        'purpose': 'Fix the failed release using release handoff context.',",
                        "        'context': ['Release provider returned failed after releaser handoff.'],",
                        "        'scope': ['Diagnose and fix release failure'],",
                        "        'out_of_scope': ['Unrelated feature work'],",
                        "        'requirements': {'confirmed': ['release provider failed'], 'unresolved': [], 'assumptions': []},",
                        "        'bdd_scenarios': ['Release can be retried successfully.'],",
                        "        'unit_tests': ['tests/unit/test_release_provider.py'],",
                        "        'acceptance': ['release failure is fixed and release can pass'],",
                        "        'dependencies': [],",
                        "        'files': {'read': ['harness/release-runs'], 'write': ['migrations/fix_release.sql']},",
                        "        'external_inputs': {'credentials': [], 'services': [], 'user_decisions': []}",
                        "    }]",
                        "}))",
                    ]
                ),
                encoding="utf-8",
            )
            config = deepcopy(DEFAULT_CONFIG)
            config["capabilities"]["releaser"]["command"] = f"{shlex.quote(sys.executable)} {shlex.quote(str(releaser_script))}"
            config["integrations"]["release_provider"] = {
                "provider": "command",
                "command": f"{shlex.quote(sys.executable)} {shlex.quote(str(release_script))}",
            }
            config["capabilities"]["planner"]["command"] = f"{shlex.quote(sys.executable)} {shlex.quote(str(planner_script))}"
            done = completed_task(ready_task("TASK-0001", priority=1))
            write_task(root, "done", "TASK-0001", done)

            result = run_autopilot(root, config, limit=1, max_steps=2)

            self.assertEqual(result.failed, [])
            self.assertEqual(result.release_status, "failed")
            self.assertEqual(result.planned, ["TASK-0002"])
            metadata = json.loads((result.path / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["releaser_tasks"], ["TASK-0001"])
            self.assertIn("autopilot:release_repair_plan", metadata["actions"])

    def test_autopilot_does_not_release_when_any_task_remains_unfinished(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = deepcopy(DEFAULT_CONFIG)
            marker = root / "release-called.txt"
            release_script = root / "release_stub.py"
            release_script.write_text(
                "\n".join(
                    [
                        "import json",
                        "from pathlib import Path",
                        f"Path({str(marker)!r}).write_text('called', encoding='utf-8')",
                        "print(json.dumps({",
                        "    'schema_version': 1,",
                        "    'status': 'released',",
                        "    'summary': 'release complete',",
                        "    'artifacts': []",
                        "}))",
                    ]
                ),
                encoding="utf-8",
            )
            config["integrations"]["release_provider"] = {
                "provider": "command",
                "command": f"{shlex.quote(sys.executable)} {shlex.quote(str(release_script))}",
            }
            done = completed_task(ready_task("TASK-0001", priority=1))
            blocked = ready_task("TASK-0002", priority=2)
            blocked["state"] = "blocked"
            blocked["blockers"] = [
                {
                    "id": "BLK-0001",
                    "type": "external_input",
                    "reason": "waiting for approval",
                    "unblock_condition": "approval exists",
                    "owner": "user",
                    "source": "test",
                    "status": "active",
                    "created_at": "2026-05-30T00:00:00Z",
                    "resolved_at": None,
                }
            ]
            write_task(root, "done", "TASK-0001", done)
            write_task(root, "blocked", "TASK-0002", blocked)

            result = run_autopilot(root, config, limit=1, max_steps=1)

            self.assertEqual(result.release, None)
            self.assertFalse(marker.exists())
            metadata = json.loads((result.path / "metadata.json").read_text(encoding="utf-8"))
            self.assertIsNone(metadata["release"])
            self.assertNotIn("autopilot:release_status", metadata["actions"])

    def test_autopilot_metadata_records_skipped_tasks_when_no_batch_can_run(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            blocked = ready_task("TASK-0001", priority=1)
            blocked["state"] = "blocked"
            blocked["blockers"] = [
                {
                    "id": "BLK-0001",
                    "type": "external_input",
                    "reason": "waiting for approval",
                    "unblock_condition": "approval exists",
                    "owner": "user",
                    "source": "test",
                    "status": "active",
                    "created_at": "2026-05-30T00:00:00Z",
                    "resolved_at": None,
                }
            ]
            write_task(root, "blocked", "TASK-0001", blocked)

            result = run_autopilot(root, deepcopy(DEFAULT_CONFIG), limit=1, max_steps=1)

            metadata = json.loads((result.path / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(result.blocked, ["TASK-0001"])
            self.assertEqual(metadata["status"], "blocked")
            self.assertEqual(metadata["blocked"], ["TASK-0001"])
            self.assertEqual(metadata["skipped"][0]["id"], "TASK-0001")
            self.assertIn("state is blocked", metadata["skipped"][0]["reasons"][0])

    def test_cli_autopilot_run_returns_nonzero_when_blocked(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = deepcopy(DEFAULT_CONFIG)
            for command_name in config["commands"]:
                config["commands"][command_name] = None
            pr_script = root / "pr_stub.py"
            pr_script.write_text(
                "\n".join(
                    [
                        "import json",
                        "print(json.dumps({",
                        "    'schema_version': 1,",
                        "    'status': 'open',",
                        "    'summary': 'pr still open',",
                        "    'checks': []",
                        "}))",
                    ]
                ),
                encoding="utf-8",
            )
            config["integrations"]["pr_provider"] = {
                "provider": "command",
                "command": f"{shlex.quote(sys.executable)} {shlex.quote(str(pr_script))}",
            }
            dump_data({"schema_version": 1, "commands": config["commands"], "integrations": config["integrations"]}, root / "harness.yml")
            accepted = active_task(ready_task("TASK-0001", priority=1), state="accepted")
            write_task(root, "accepted", "TASK-0001", accepted)
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(["autopilot", "--run", "--limit", "1", "--max-steps", "2"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 1)
            self.assertIn("blocked 1 task(s): TASK-0001", output.getvalue())

    def test_cli_autopilot_run_json_returns_nonzero_when_blocked(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = deepcopy(DEFAULT_CONFIG)
            for command_name in config["commands"]:
                config["commands"][command_name] = None
            pr_script = root / "pr_stub.py"
            pr_script.write_text(
                "\n".join(
                    [
                        "import json",
                        "print(json.dumps({",
                        "    'schema_version': 1,",
                        "    'status': 'open',",
                        "    'summary': 'pr still open',",
                        "    'checks': []",
                        "}))",
                    ]
                ),
                encoding="utf-8",
            )
            config["integrations"]["pr_provider"] = {
                "provider": "command",
                "command": f"{shlex.quote(sys.executable)} {shlex.quote(str(pr_script))}",
            }
            dump_data({"schema_version": 1, "commands": config["commands"], "integrations": config["integrations"]}, root / "harness.yml")
            accepted = active_task(ready_task("TASK-0001", priority=1), state="accepted")
            write_task(root, "accepted", "TASK-0001", accepted)
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(["autopilot", "--run", "--limit", "1", "--max-steps", "2", "--json"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 1)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["blocked"], ["TASK-0001"])

    def test_autopilot_reports_blocked_when_session_launch_blocks_dispatch(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = root / "blocked_session_provider.py"
            provider.write_text(
                "\n".join(
                    [
                        "import json, sys",
                        "json.load(sys.stdin)",
                        "print(json.dumps({",
                        "    'schema_version': 1,",
                        "    'status': 'blocked',",
                        "    'summary': 'codex command not authenticated'",
                        "}))",
                    ]
                ),
                encoding="utf-8",
            )
            config = deepcopy(DEFAULT_CONFIG)
            config["sessions"] = {
                "agent_provider": "codex",
                "role": "worker_agent",
                "launch_command": f"{shlex.quote(sys.executable)} {shlex.quote(str(provider))}",
            }
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001"))

            result = run_autopilot(root, config, limit=1, max_steps=1)

            self.assertEqual(result.failed, [])
            self.assertEqual(result.blocked, ["TASK-0001"])
            self.assertEqual(result.status, "blocked")
            self.assertTrue((root / "harness" / "tasks" / "blocked" / "TASK-0001.json").exists())
            metadata = json.loads((result.path / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["status"], "blocked")
            self.assertEqual(metadata["blocked"], ["TASK-0001"])

    def test_cli_autopilot_run_json_includes_release_evidence(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = deepcopy(DEFAULT_CONFIG)
            release_script = root / "release_stub.py"
            release_script.write_text(
                "\n".join(
                    [
                        "import json",
                        "print(json.dumps({",
                        "    'schema_version': 1,",
                        "    'status': 'released',",
                        "    'summary': 'release complete',",
                        "    'artifacts': []",
                        "}))",
                    ]
                ),
                encoding="utf-8",
            )
            config["integrations"]["release_provider"] = {
                "provider": "command",
                "command": f"{shlex.quote(sys.executable)} {shlex.quote(str(release_script))}",
            }
            dump_data({"schema_version": 1, "integrations": config["integrations"]}, root / "harness.yml")
            done = completed_task(ready_task("TASK-0001", priority=1))
            write_task(root, "done", "TASK-0001", done)
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(["autopilot", "--run", "--limit", "1", "--max-steps", "1", "--json"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 0)
            payload = json.loads(output.getvalue())
            self.assertIsInstance(payload["release"], str)
            self.assertEqual(payload["release_status"], "released")
            self.assertTrue((root / payload["release"]).exists())


if __name__ == "__main__":
    unittest.main()
