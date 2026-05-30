from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import attestflow.cli as cli
from attestflow.config import DEFAULT_CONFIG
from attestflow.io import load_data
from attestflow.sessions import resume_agent_session
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
            provider = root / "session_provider.py"
            provider.write_text(
                """
import json
import sys

payload = json.load(sys.stdin)
assert payload["schema_version"] == 1
assert payload["action"] == "launch"
assert payload["agent_provider"] == "codex"
assert payload["session"]["task_id"] == "TASK-0001"
assert payload["prompt_packet"]["path"] == "prompt.md"
assert "Attestflow Agent Session Packet" in payload["prompt_packet"]["content"]
json.dump(
    {
        "schema_version": 1,
        "status": "launched",
        "external_session_id": "codex-session-123",
        "resume_command": "codex resume codex-session-123",
        "summary": "started codex session",
    },
    sys.stdout,
)
""".lstrip(),
                encoding="utf-8",
            )
            config = DEFAULT_CONFIG.copy()
            config["root"] = root
            config["sessions"] = {
                "agent_provider": "codex",
                "role": "worker_agent",
                "launch_command": f"python3 {provider}",
            }
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001"))

            run = start_task(root, config, "TASK-0001", actor_role="orchestrator")

            session = load_data(run.path / "session.yml")
            self.assertEqual(session["agent_provider"], "codex")
            self.assertEqual(session["role"], "worker_agent")
            self.assertEqual(session["status"], "launched")
            self.assertEqual(session["external_session_id"], "codex-session-123")
            self.assertEqual(session["resume_command"], "codex resume codex-session-123")
            self.assertEqual(session["launch_exit_code"], 0)
            self.assertTrue((run.path / "session-adapter-input.json").exists())
            self.assertTrue((run.path / "session-adapter-output.json").exists())
            adapter_input = load_data(run.path / "session-adapter-input.json")
            self.assertEqual(adapter_input["action"], "launch")
            metadata = load_data(run.path / "metadata.yml")
            self.assertEqual(metadata["agent_session"]["agent_provider"], "codex")
            self.assertEqual(metadata["agent_session"]["external_session_id"], "codex-session-123")
            ledger = (run.path / "ledger.jsonl").read_text(encoding="utf-8")
            self.assertIn('"event": "session_launched"', ledger)

    def test_builtin_provider_preset_launches_without_custom_adapter_script(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            fake_codex = bin_dir / "fake-codex"
            fake_codex.write_text(
                """
#!/usr/bin/env python3
import json
import sys

prompt = sys.argv[-1]
assert "Attestflow Agent Session Packet" in prompt
print(json.dumps({"type": "thread.started", "thread_id": "codex-thread-123"}))
print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}}))
""".lstrip(),
                encoding="utf-8",
            )
            fake_codex.chmod(0o755)
            config = DEFAULT_CONFIG.copy()
            config["root"] = root
            config["sessions"] = {
                "agent_provider": "codex",
                "role": "worker_agent",
                "provider_options": {"command": str(fake_codex), "launch_args": ["exec", "--json"]},
            }
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001"))

            run = start_task(root, config, "TASK-0001", actor_role="orchestrator")

            session = load_data(run.path / "session.yml")
            self.assertEqual(session["status"], "launched")
            self.assertEqual(session["external_session_id"], "codex-thread-123")
            self.assertIn("agent_adapters.py", session["launch_command"])
            adapter_input = load_data(run.path / "session-adapter-input.json")
            self.assertEqual(adapter_input["provider_options"]["command"], str(fake_codex))
            adapter_output = load_data(run.path / "session-adapter-output.json")
            self.assertIn("agent_adapters.py", adapter_output["resume_command"])

    def test_cli_provider_list_exposes_builtin_session_presets(self) -> None:
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = cli.main(["provider", "list"])

        self.assertEqual(exit_code, 0)
        self.assertIn("codex", output.getvalue())
        self.assertIn("claude-code", output.getvalue())
        self.assertIn("opencode", output.getvalue())

    def test_session_launch_invalid_output_records_failed_session(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = root / "invalid_session_provider.py"
            provider.write_text("print('not json')\n", encoding="utf-8")
            config = DEFAULT_CONFIG.copy()
            config["root"] = root
            config["sessions"] = {
                "agent_provider": "opencode",
                "role": "worker_agent",
                "launch_command": f"python3 {provider}",
            }
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001"))

            run = start_task(root, config, "TASK-0001", actor_role="orchestrator")

            session = load_data(run.path / "session.yml")
            self.assertEqual(session["agent_provider"], "opencode")
            self.assertEqual(session["status"], "launch_failed")
            self.assertIn("valid JSON", session["failure"])
            self.assertEqual(session["launch_exit_code"], 0)
            self.assertTrue((run.path / "session-launch.stdout.log").exists())

    def test_session_launch_blocked_moves_task_to_blocked_with_structured_blocker(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = root / "blocked_session_provider.py"
            provider.write_text(
                """
import json
import sys

json.load(sys.stdin)
json.dump(
    {
        "schema_version": 1,
        "status": "blocked",
        "summary": "codex command not authenticated",
    },
    sys.stdout,
)
""".lstrip(),
                encoding="utf-8",
            )
            config = DEFAULT_CONFIG.copy()
            config["root"] = root
            config["sessions"] = {
                "agent_provider": "codex",
                "role": "worker_agent",
                "launch_command": f"python3 {provider}",
            }
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001"))

            run = start_task(root, config, "TASK-0001", actor_role="orchestrator")

            self.assertFalse((root / "harness" / "tasks" / "in_progress" / "TASK-0001.json").exists())
            blocked = load_data(root / "harness" / "tasks" / "blocked" / "TASK-0001.json")
            self.assertEqual(blocked["state"], "blocked")
            self.assertEqual(blocked["evidence"]["run_id"], run.run_id)
            self.assertTrue(blocked["evidence"]["session"].endswith("session.yml"))
            self.assertEqual(blocked["blockers"][0]["type"], "agent_session")
            self.assertEqual(blocked["blockers"][0]["source"], "session:launch")
            self.assertEqual(blocked["blockers"][0]["reason"], "codex command not authenticated")
            self.assertEqual(blocked["blockers"][0]["status"], "active")
            self.assertFalse((root / "harness" / "locks" / "tasks" / "TASK-0001.lock").exists())
            session = load_data(run.path / "session.yml")
            self.assertEqual(session["status"], "blocked")

    def test_session_resume_runs_configured_adapter_command(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = root / "session_provider.py"
            provider.write_text(
                """
import json
import sys

payload = json.load(sys.stdin)
assert payload["action"] == "launch"
json.dump(
    {
        "schema_version": 1,
        "status": "launched",
        "external_session_id": "claude-session-123",
        "resume_command": "python3 resume_provider.py",
        "summary": "started claude session",
    },
    sys.stdout,
)
""".lstrip(),
                encoding="utf-8",
            )
            resume_provider = root / "resume_provider.py"
            resume_provider.write_text(
                """
import json
import sys

payload = json.load(sys.stdin)
assert payload["action"] == "resume"
assert payload["session"]["external_session_id"] == "claude-session-123"
json.dump(
    {
        "schema_version": 1,
        "status": "resumed",
        "external_session_id": "claude-session-123",
        "resume_command": "python3 resume_provider.py",
        "summary": "resumed claude session",
    },
    sys.stdout,
)
""".lstrip(),
                encoding="utf-8",
            )
            config = DEFAULT_CONFIG.copy()
            config["root"] = root
            config["sessions"] = {
                "agent_provider": "claude-code",
                "role": "worker_agent",
                "launch_command": f"python3 {provider}",
                "resume_command": f"python3 {resume_provider}",
            }
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001"))
            run = start_task(root, config, "TASK-0001", actor_role="orchestrator")

            resumed = resume_agent_session(root, config, run.path)

            self.assertEqual(resumed.status, "resumed")
            session = load_data(run.path / "session.yml")
            self.assertEqual(session["status"], "resumed")
            self.assertEqual(session["resumed_at"], session["updated_at"])
            self.assertEqual(session["launch_adapter_output"], "session-adapter-output.json")
            self.assertEqual(session["resume_adapter_output"], "session-resume-adapter-output.json")
            self.assertTrue((run.path / "session-resume-adapter-input.json").exists())
            resume_input = load_data(run.path / "session-resume-adapter-input.json")
            self.assertEqual(resume_input["action"], "resume")
            ledger = (run.path / "ledger.jsonl").read_text(encoding="utf-8")
            self.assertIn('"event": "session_resumed"', ledger)

    def test_cli_session_resume_uses_task_run_evidence(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            launch_provider = root / "launch_provider.py"
            launch_provider.write_text(
                """
import json
import sys

json.load(sys.stdin)
json.dump(
    {
        "schema_version": 1,
        "status": "launched",
        "external_session_id": "opencode-session-123",
        "resume_command": "python3 resume_provider.py",
        "summary": "started opencode session",
    },
    sys.stdout,
)
""".lstrip(),
                encoding="utf-8",
            )
            resume_provider = root / "resume_provider.py"
            resume_provider.write_text(
                """
import json
import sys

payload = json.load(sys.stdin)
assert payload["action"] == "resume"
json.dump(
    {
        "schema_version": 1,
        "status": "resumed",
        "external_session_id": "opencode-session-123",
        "resume_command": "python3 resume_provider.py",
        "summary": "resumed opencode session",
    },
    sys.stdout,
)
""".lstrip(),
                encoding="utf-8",
            )
            config = DEFAULT_CONFIG.copy()
            config["root"] = root
            config["sessions"] = {
                "agent_provider": "opencode",
                "role": "worker_agent",
                "launch_command": f"python3 {launch_provider}",
                "resume_command": f"python3 {resume_provider}",
            }
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001"))
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                start_task(root, config, "TASK-0001", actor_role="orchestrator")
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(["session", "resume", "TASK-0001"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 0)
            self.assertIn("resumed TASK-0001", output.getvalue())
            active = load_data(root / "harness" / "tasks" / "in_progress" / "TASK-0001.json")
            session = load_data(root / active["evidence"]["session"])
            self.assertEqual(session["status"], "resumed")

    def test_cli_session_resume_returns_nonzero_when_adapter_fails(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            launch_provider = root / "launch_provider.py"
            launch_provider.write_text(
                """
import json
import sys

json.load(sys.stdin)
json.dump(
    {
        "schema_version": 1,
        "status": "launched",
        "external_session_id": "opencode-session-123",
        "resume_command": "python3 missing_resume_provider.py",
        "summary": "started opencode session",
    },
    sys.stdout,
)
""".lstrip(),
                encoding="utf-8",
            )
            config = DEFAULT_CONFIG.copy()
            config["root"] = root
            config["sessions"] = {
                "agent_provider": "opencode",
                "role": "worker_agent",
                "launch_command": f"python3 {launch_provider}",
            }
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001"))
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                start_task(root, config, "TASK-0001", actor_role="orchestrator")
                output = io.StringIO()
                error = io.StringIO()
                with redirect_stdout(output), redirect_stderr(error):
                    exit_code = cli.main(["session", "resume", "TASK-0001"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 1)
            self.assertIn("resume_failed", error.getvalue())

    def test_cli_dispatch_returns_nonzero_when_session_launch_fails(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = root / "bad_launch_provider.py"
            provider.write_text("print('not json')\n", encoding="utf-8")
            (root / "harness.yml").write_text(
                "\n".join(
                    [
                        "sessions:",
                        "  agent_provider: codex",
                        f"  launch_command: python3 {provider}",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001"))
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                error = io.StringIO()
                with redirect_stdout(output), redirect_stderr(error):
                    exit_code = cli.main(["dispatch", "TASK-0001"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 1)
            self.assertIn("launch_failed", error.getvalue())
            active = load_data(root / "harness" / "tasks" / "in_progress" / "TASK-0001.json")
            session = load_data(root / active["evidence"]["session"])
            self.assertEqual(session["status"], "launch_failed")

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
