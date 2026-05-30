from pathlib import Path
import json
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from attestflow.io import load_data


class AgentAdapterTests(unittest.TestCase):
    def test_codex_adapter_blocks_when_command_is_missing(self) -> None:
        payload = {
            "schema_version": 1,
            "action": "launch",
            "agent_provider": "codex",
            "root": ".",
            "session": {"session_id": "session-1", "task_id": "TASK-0001"},
            "prompt_packet": {"content": "prompt"},
            "provider_options": {"command": "definitely-missing-attestflow-codex"},
        }

        completed = subprocess.run(
            [sys.executable, "-m", "attestflow.agent_adapters"],
            text=True,
            input=json.dumps(payload),
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0)
        output = json.loads(completed.stdout)
        self.assertEqual(output["status"], "blocked")
        self.assertIn("command not found", output["summary"])

    def test_opencode_adapter_parses_session_id_from_json_output(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_opencode = root / "fake-opencode"
            fake_opencode.write_text(
                """
#!/usr/bin/env python3
import json
print(json.dumps({"sessionID": "opencode-session-123", "message": "ok"}))
""".lstrip(),
                encoding="utf-8",
            )
            fake_opencode.chmod(0o755)
            payload = {
                "schema_version": 1,
                "action": "launch",
                "agent_provider": "opencode",
                "root": str(root),
                "session": {"session_id": "session-1", "task_id": "TASK-0001"},
                "prompt_packet": {"content": "prompt"},
                "provider_options": {"command": str(fake_opencode), "launch_args": ["run", "--format", "json"]},
            }

            completed = subprocess.run(
                [sys.executable, "-m", "attestflow.agent_adapters"],
                text=True,
                input=json.dumps(payload),
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0)
            output_path = root / "adapter-output.json"
            output_path.write_text(completed.stdout, encoding="utf-8")
            output = load_data(output_path)
            self.assertEqual(output["status"], "launched")
            self.assertEqual(output["external_session_id"], "opencode-session-123")

    def test_adapter_blocks_on_invalid_argument_template(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_codex = root / "fake-codex"
            fake_codex.write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
            fake_codex.chmod(0o755)
            payload = {
                "schema_version": 1,
                "action": "launch",
                "agent_provider": "codex",
                "root": str(root),
                "session": {"session_id": "session-1", "task_id": "TASK-0001"},
                "prompt_packet": {"content": "prompt"},
                "provider_options": {"command": str(fake_codex), "launch_args": ["exec", "{missing_key}"]},
            }

            completed = subprocess.run(
                [sys.executable, "-m", "attestflow.agent_adapters"],
                text=True,
                input=json.dumps(payload),
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0)
            output = json.loads(completed.stdout)
            self.assertEqual(output["status"], "blocked")
            self.assertIn("could not run", output["summary"])

    def test_resume_without_external_session_id_uses_provider_continue_default(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_opencode = root / "fake-opencode"
            argv_path = root / "argv.json"
            fake_opencode.write_text(
                f"""
#!/usr/bin/env python3
import json
import sys

open({str(argv_path)!r}, "w", encoding="utf-8").write(json.dumps(sys.argv[1:]))
print(json.dumps({{"sessionID": "opencode-session-continued"}}))
""".lstrip(),
                encoding="utf-8",
            )
            fake_opencode.chmod(0o755)
            payload = {
                "schema_version": 1,
                "action": "resume",
                "agent_provider": "opencode",
                "root": str(root),
                "session": {"session_id": "session-1", "task_id": "TASK-0001"},
                "prompt_packet": {"content": "prompt"},
                "provider_options": {"command": str(fake_opencode)},
            }

            completed = subprocess.run(
                [sys.executable, "-m", "attestflow.agent_adapters"],
                text=True,
                input=json.dumps(payload),
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0)
            output = json.loads(completed.stdout)
            self.assertEqual(output["status"], "resumed")
            self.assertEqual(output["external_session_id"], "opencode-session-continued")
            argv = json.loads(argv_path.read_text(encoding="utf-8"))
            self.assertEqual(argv[:3], ["run", "--format", "json"])
            self.assertIn("--continue", argv)


if __name__ == "__main__":
    unittest.main()
