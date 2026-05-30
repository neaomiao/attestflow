from contextlib import redirect_stdout
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import attestflow.cli as cli
from attestflow.config import DEFAULT_CONFIG
from attestflow.io import load_data
from attestflow.tasks import start_task
from tests.unit.test_task_lifecycle import ready_task, write_task


class AgentSessionTests(unittest.TestCase):
    def test_start_task_creates_independent_agent_session_packet(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            config["root"] = root
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001"))

            run = start_task(root, config, "TASK-0001", actor_role="orchestrator")

            session = load_data(run.path / "session.yml")
            self.assertEqual(session["task_id"], "TASK-0001")
            self.assertEqual(session["run_id"], run.run_id)
            self.assertEqual(session["status"], "prepared")
            self.assertEqual(session["prompt_packet"], "prompt.md")
            self.assertTrue(str(session["session_id"]).startswith("session-"))
            self.assertTrue((run.path / "prompt.md").exists())

            metadata = load_data(run.path / "metadata.yml")
            self.assertEqual(metadata["agent_session"]["session_id"], session["session_id"])
            self.assertEqual(metadata["agent_session"]["prompt_packet"], "prompt.md")

            active = load_data(root / "harness" / "tasks" / "in_progress" / "TASK-0001.json")
            self.assertEqual(active["evidence"]["session"], str((run.path / "session.yml").relative_to(root)))
            ledger = (run.path / "ledger.jsonl").read_text(encoding="utf-8")
            self.assertIn('"event": "session_created"', ledger)

    def test_start_task_runs_configured_session_launch_command(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            config["root"] = root
            config["sessions"] = {
                "provider": "test-agent",
                "role": "worker_agent",
                "launch_command": "python3 -c 'print(\"launched {session_id}\")'",
            }
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001"))

            run = start_task(root, config, "TASK-0001", actor_role="orchestrator")

            session = load_data(run.path / "session.yml")
            self.assertEqual(session["provider"], "test-agent")
            self.assertEqual(session["role"], "worker_agent")
            self.assertEqual(session["status"], "launched")
            self.assertEqual(session["launch_exit_code"], 0)
            self.assertIn("launched", (run.path / "session-launch.log").read_text(encoding="utf-8"))

    def test_cli_dispatch_starts_task_and_reports_session_id(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            config["root"] = root
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001"))
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(["dispatch", "TASK-0001"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 0)
            self.assertIn("dispatched TASK-0001", output.getvalue())
            active = load_data(root / "harness" / "tasks" / "in_progress" / "TASK-0001.json")
            self.assertTrue(active["evidence"]["session"].endswith("session.yml"))


if __name__ == "__main__":
    unittest.main()
