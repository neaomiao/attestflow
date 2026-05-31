from copy import deepcopy
from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import subprocess
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

    def test_start_task_with_worktree_runs_session_adapter_in_task_worktree(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            _init_git_repo(root)
            provider = root / "session_provider.py"
            cwd_file = root.parent / "adapter-cwd.txt"
            provider.write_text(
                f"""
import json
import pathlib
import sys

payload = json.load(sys.stdin)
cwd = pathlib.Path.cwd()
pathlib.Path({str(cwd_file)!r}).write_text(str(cwd), encoding="utf-8")
assert payload["root"] == str(cwd)
assert payload["control_root"] == {str(root)!r}
assert payload["workspace"]["worktree"] == str(cwd)
json.dump(
    {{
        "schema_version": 1,
        "status": "launched",
        "external_session_id": "codex-session-123",
        "summary": "started in worktree",
    }},
    sys.stdout,
)
""".lstrip(),
                encoding="utf-8",
            )
            worktree_template = str(root.parent / "worktrees" / "{task_id}-{run_id}")
            config = deepcopy(DEFAULT_CONFIG)
            config["root"] = root
            config["sessions"]["agent_provider"] = "codex"
            config["sessions"]["launch_command"] = f"python3 {provider}"
            config["sessions"]["worktree"] = {"enabled": True, "path_template": worktree_template}
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001"))

            run = start_task(root, config, "TASK-0001", actor_role="orchestrator")

            metadata = load_data(run.path / "metadata.yml")
            worktree = Path(metadata["workspace"]["worktree"])
            self.assertTrue(worktree.exists())
            self.assertEqual(cwd_file.read_text(encoding="utf-8"), str(worktree))
            session = load_data(run.path / "session.yml")
            self.assertEqual(session["status"], "launched")
            self.assertEqual(session["workspace_root"], str(worktree))
            active = load_data(root / "harness" / "tasks" / "in_progress" / "TASK-0001.json")
            self.assertEqual(active["evidence"]["worktree"], str(worktree))
            adapter_input = load_data(run.path / "session-adapter-input.json")
            self.assertEqual(adapter_input["root"], str(worktree))
            self.assertEqual(adapter_input["control_root"], str(root))

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
	        "usage": {"provider": "codex", "model": "gpt-5", "input_tokens": 42, "output_tokens": 7, "total_tokens": 49},
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
            self.assertEqual(session["launch_usage"], "session-launch-usage.json")
            self.assertTrue((run.path / "session-adapter-input.json").exists())
            self.assertTrue((run.path / "session-adapter-output.json").exists())
            self.assertEqual(load_data(run.path / "session-launch-usage.json")["total_tokens"], 49)
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

    def test_session_launch_timeout_records_failed_session_and_logs(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = root / "slow_session_provider.py"
            provider.write_text(
                """
import json
import time

time.sleep(0.3)
print(json.dumps({"schema_version": 1, "status": "launched", "summary": "too late"}))
""".lstrip(),
                encoding="utf-8",
            )
            config = DEFAULT_CONFIG.copy()
            config["root"] = root
            config["sessions"] = {
                "agent_provider": "codex",
                "role": "worker_agent",
                "launch_command": f"python3 {provider}",
                "provider_options": {"timeout_seconds": 0.05},
            }
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001"))

            run = start_task(root, config, "TASK-0001", actor_role="orchestrator")

            session = load_data(run.path / "session.yml")
            self.assertEqual(session["status"], "launch_failed")
            self.assertIn("timed out", session["failure"])
            self.assertEqual(session["launch_exit_code"], -1)
            self.assertIn("timed out", (run.path / "session-launch.stderr.log").read_text(encoding="utf-8"))
            self.assertTrue((run.path / "session-adapter-input.json").exists())

    def test_session_launch_rejects_actual_writes_outside_task_scope(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = root / "scope_breaking_session_provider.py"
            provider.write_text(
                """
import json
import pathlib
import sys

json.load(sys.stdin)
pathlib.Path("outside.txt").write_text("not allowed", encoding="utf-8")
json.dump(
    {
        "schema_version": 1,
        "status": "launched",
        "external_session_id": "codex-session-123",
        "summary": "started but wrote outside scope",
    },
    sys.stdout,
)
""".lstrip(),
                encoding="utf-8",
            )
            task = ready_task("TASK-0001")
            task["files"]["write"] = ["allowed.txt"]
            config = DEFAULT_CONFIG.copy()
            config["root"] = root
            config["sessions"] = {
                "agent_provider": "codex",
                "role": "worker_agent",
                "launch_command": f"python3 {provider}",
            }
            write_task(root, "ready", "TASK-0001", task)

            run = start_task(root, config, "TASK-0001", actor_role="orchestrator")

            session = load_data(run.path / "session.yml")
            self.assertEqual(session["status"], "launch_failed")
            self.assertIn("write_scope", session["failure"])
            report = load_data(run.path / "session-launch-write-scope.json")
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["violations"][0]["path"], "outside.txt")
            self.assertEqual(report["violations"][0]["change_type"], "added")

    def test_session_resume_rejects_delete_rename_and_binary_outside_task_scope(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("before\n", encoding="utf-8")
            (root / "old-name.txt").write_text("move me\n", encoding="utf-8")
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
        "external_session_id": "codex-session-123",
        "summary": "started codex session",
    },
    sys.stdout,
)
""".lstrip(),
                encoding="utf-8",
            )
            resume_provider = root / "scope_breaking_resume_provider.py"
            resume_provider.write_text(
                """
import json
import pathlib
import sys

json.load(sys.stdin)
pathlib.Path("README.md").unlink()
pathlib.Path("old-name.txt").rename("new-name.txt")
pathlib.Path("asset.bin").write_bytes(b"\\x00\\x01binary")
json.dump(
    {
        "schema_version": 1,
        "status": "resumed",
        "external_session_id": "codex-session-123",
        "summary": "resumed with bad writes",
    },
    sys.stdout,
)
""".lstrip(),
                encoding="utf-8",
            )
            task = ready_task("TASK-0001")
            task["files"]["write"] = ["allowed.txt"]
            config = DEFAULT_CONFIG.copy()
            config["root"] = root
            config["sessions"] = {
                "agent_provider": "codex",
                "role": "worker_agent",
                "launch_command": f"python3 {launch_provider}",
                "resume_command": f"python3 {resume_provider}",
            }
            write_task(root, "ready", "TASK-0001", task)
            run = start_task(root, config, "TASK-0001", actor_role="orchestrator")

            resumed = resume_agent_session(root, config, run.path)

            self.assertEqual(resumed.status, "resume_failed")
            session = load_data(run.path / "session.yml")
            self.assertIn("write_scope", session["failure"])
            report = load_data(run.path / "session-resume-write-scope.json")
            violations = {(item["path"], item["change_type"]) for item in report["violations"]}
            self.assertIn(("README.md", "deleted"), violations)
            self.assertIn(("old-name.txt", "renamed_from"), violations)
            self.assertIn(("new-name.txt", "renamed_to"), violations)
            self.assertIn(("asset.bin", "added"), violations)
            binary_paths = {item["path"] for item in report["changes"] if item["binary"]}
            self.assertIn("asset.bin", binary_paths)

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

    def test_session_resume_timeout_records_failed_session_and_logs(self) -> None:
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
        "external_session_id": "codex-session-123",
        "summary": "started codex session",
    },
    sys.stdout,
)
""".lstrip(),
                encoding="utf-8",
            )
            resume_provider = root / "slow_resume_provider.py"
            resume_provider.write_text(
                """
import json
import time

time.sleep(0.3)
print(json.dumps({"schema_version": 1, "status": "resumed", "summary": "too late"}))
""".lstrip(),
                encoding="utf-8",
            )
            config = DEFAULT_CONFIG.copy()
            config["root"] = root
            config["sessions"] = {
                "agent_provider": "codex",
                "role": "worker_agent",
                "launch_command": f"python3 {launch_provider}",
                "resume_command": f"python3 {resume_provider}",
                "provider_options": {"timeout_seconds": 0.05},
            }
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001"))
            run = start_task(root, config, "TASK-0001", actor_role="orchestrator")

            resumed = resume_agent_session(root, config, run.path)

            self.assertEqual(resumed.status, "resume_failed")
            session = load_data(run.path / "session.yml")
            self.assertIn("timed out", session["failure"])
            self.assertEqual(session["resume_exit_code"], -1)
            self.assertIn("timed out", (run.path / "session-resume.stderr.log").read_text(encoding="utf-8"))
            self.assertTrue((run.path / "session-resume-adapter-input.json").exists())

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

    def test_cli_dispatch_limit_starts_parallel_safe_ready_tasks(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = ready_task("TASK-0001", priority=1)
            first["files"]["write"] = ["src/a.py"]
            second = ready_task("TASK-0002", priority=2)
            second["files"]["write"] = ["src/b.py"]
            conflicting = ready_task("TASK-0003", priority=3)
            conflicting["files"]["write"] = ["src/a.py"]
            write_task(root, "ready", "TASK-0001", first)
            write_task(root, "ready", "TASK-0002", second)
            write_task(root, "ready", "TASK-0003", conflicting)
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(["dispatch", "--limit", "3"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 0)
            self.assertIn("dispatched 2 task(s): TASK-0001, TASK-0002", output.getvalue())
            self.assertTrue((root / "harness" / "tasks" / "in_progress" / "TASK-0001.json").exists())
            self.assertTrue((root / "harness" / "tasks" / "in_progress" / "TASK-0002.json").exists())
            self.assertTrue((root / "harness" / "tasks" / "ready" / "TASK-0003.json").exists())
            self.assertTrue((root / "harness" / "locks" / "files" / "src.a.py.lock").exists())
            self.assertTrue((root / "harness" / "locks" / "files" / "src.b.py.lock").exists())

    def test_cli_dispatch_without_task_reports_no_dispatchable_tasks(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                error = io.StringIO()
                with redirect_stderr(error):
                    exit_code = cli.main(["dispatch"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 1)
            self.assertIn("no dispatchable tasks", error.getvalue())

    def test_cli_dispatch_rejects_non_positive_batch_limit(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                error = io.StringIO()
                with redirect_stderr(error):
                    exit_code = cli.main(["dispatch", "--limit", "0"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 1)
            self.assertIn("--limit must be at least 1", error.getvalue())

    def test_cli_dispatch_rejects_limit_with_explicit_task(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001"))
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                error = io.StringIO()
                with redirect_stderr(error):
                    exit_code = cli.main(["dispatch", "TASK-0001", "--limit", "2"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 1)
            self.assertIn("--limit can only be used without an explicit task", error.getvalue())
            self.assertTrue((root / "harness" / "tasks" / "ready" / "TASK-0001.json").exists())

    def test_cli_dispatch_missing_task_reports_error_without_traceback(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                error = io.StringIO()
                with redirect_stderr(error):
                    exit_code = cli.main(["dispatch", "TASK-4040"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 1)
            self.assertIn("ERROR: task not found: TASK-4040", error.getvalue())
            self.assertNotIn("Traceback", error.getvalue())

    def test_cli_start_missing_task_reports_error_without_traceback(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                error = io.StringIO()
                with redirect_stderr(error):
                    exit_code = cli.main(["start", "TASK-4040"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 1)
            self.assertIn("ERROR: task not found: TASK-4040", error.getvalue())
            self.assertNotIn("Traceback", error.getvalue())


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "attestflow@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Attestflow Tests"], cwd=root, check=True)
    (root / "README.md").write_text("test repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=root, check=True, stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    unittest.main()
