from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

import attestflow.cli as cli
from attestflow.io import load_data
from attestflow.release import run_release_status


class ReleaseProviderTests(unittest.TestCase):
    def test_command_release_provider_records_contract_evidence(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "harness" / "tasks" / "done").mkdir(parents=True)
            (root / "harness" / "tasks" / "done" / "TASK-0001.json").write_text(
                """
{
  "schema_version": 1,
  "id": "TASK-0001",
  "title": "Ship login",
  "state": "done",
  "priority": 1,
  "type": "feature",
  "purpose": "Release this task.",
  "scope": ["login"],
  "out_of_scope": ["billing"],
  "requirements": {"confirmed": ["login works"], "unresolved": [], "assumptions": []},
  "bdd_scenarios": ["User can log in."],
  "unit_tests": ["tests/unit/test_login.py"],
  "acceptance": ["login shipped"],
  "dependencies": [],
  "blocks": [],
  "blockers": [],
  "files": {"read": [], "write": ["src/login.py"]},
  "agents": {"owner": "orchestrator", "allowed_roles": ["worker_agent"]},
  "external_inputs": {"credentials": [], "services": [], "user_decisions": []},
  "evidence": {"run_id": "run-1", "packet": "harness/runs/run-1/evidence.md"}
}
""".strip()
                + "\n",
                encoding="utf-8",
            )
            provider = root / "release-provider.py"
            provider.write_text(
                """
import json
import sys

payload = json.load(sys.stdin)
assert payload["schema_version"] == 1
assert payload["provider"] == "command"
assert payload["root"]
assert payload["done_tasks"] == ["TASK-0001"]
json.dump(
    {
        "schema_version": 1,
        "provider": "local-release",
        "status": "released",
        "summary": "Release completed",
        "external_id": "rel-123",
        "url": "https://release.example/rel-123",
        "artifacts": [{"name": "package", "url": "https://release.example/pkg"}],
    },
    sys.stdout,
)
""".lstrip(),
                encoding="utf-8",
            )
            config = {
                "project": {"name": "demo"},
                "paths": {"release_runs": "harness/release-runs"},
                "integrations": {"release_provider": {"provider": "command", "command": f"python3 {provider}"}},
            }

            result = run_release_status(root, config, done_tasks=["TASK-0001"])

            self.assertEqual(result.status, "released")
            self.assertEqual(result.output["external_id"], "rel-123")
            self.assertTrue((result.run_path / "input.json").exists())
            self.assertTrue((result.run_path / "stdout.log").exists())
            self.assertTrue((result.run_path / "stderr.log").exists())
            self.assertEqual(load_data(result.run_path / "output.json")["status"], "released")

    def test_release_provider_input_includes_done_task_delivery_evidence(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "harness" / "tasks" / "done").mkdir(parents=True)
            (root / "harness" / "ci-runs" / "ci-1").mkdir(parents=True)
            (root / "harness" / "pr-runs" / "pr-ensure-1").mkdir(parents=True)
            (root / "harness" / "pr-runs" / "pr-status-1").mkdir(parents=True)
            (root / "harness" / "ci-runs" / "ci-1" / "output.json").write_text(
                '{"schema_version": 1, "status": "passed", "summary": "ci passed"}\n',
                encoding="utf-8",
            )
            (root / "harness" / "pr-runs" / "pr-ensure-1" / "output.json").write_text(
                '{"schema_version": 1, "status": "open", "summary": "pr open", "external_id": "42"}\n',
                encoding="utf-8",
            )
            (root / "harness" / "pr-runs" / "pr-status-1" / "output.json").write_text(
                '{"schema_version": 1, "status": "merged", "summary": "pr merged", "external_id": "42"}\n',
                encoding="utf-8",
            )
            (root / "harness" / "tasks" / "done" / "TASK-0001.json").write_text(
                """
{
  "schema_version": 1,
  "id": "TASK-0001",
  "title": "Ship login",
  "state": "done",
  "priority": 1,
  "type": "feature",
  "purpose": "Release notes need task context.",
  "scope": ["login flow"],
  "out_of_scope": ["billing"],
  "requirements": {"confirmed": ["login works"], "unresolved": [], "assumptions": []},
  "bdd_scenarios": ["User can log in."],
  "unit_tests": ["tests/unit/test_login.py"],
  "acceptance": ["login released"],
  "dependencies": [],
  "blocks": [],
  "blockers": [],
  "files": {"read": [], "write": ["src/login.py"]},
  "agents": {"owner": "orchestrator", "allowed_roles": ["worker_agent"]},
  "external_inputs": {"credentials": [], "services": [], "user_decisions": []},
  "evidence": {
    "run_id": "run-1",
    "packet": "harness/runs/run-1/evidence.md",
    "ci": "harness/ci-runs/ci-1/output.json",
    "pr_request": "harness/pr-runs/pr-ensure-1/output.json",
    "pr": "harness/pr-runs/pr-status-1/output.json"
  },
  "links": {"issues": [], "prs": [], "docs": []},
  "risks": [],
  "notes": [],
  "created_at": "2026-05-30T00:00:00Z",
  "updated_at": "2026-05-30T00:00:00Z"
}
""".strip()
                + "\n",
                encoding="utf-8",
            )
            provider = root / "release-provider.py"
            provider.write_text(
                """
import json
import sys

payload = json.load(sys.stdin)
task = payload["tasks"][0]
assert task["id"] == "TASK-0001"
assert task["title"] == "Ship login"
assert task["evidence"]["ci"]["output"]["status"] == "passed"
assert task["evidence"]["pr_request"]["output"]["status"] == "open"
assert task["evidence"]["pr"]["output"]["status"] == "merged"
json.dump({"schema_version": 1, "provider": "local-release", "status": "released", "summary": "Release completed"}, sys.stdout)
""".lstrip(),
                encoding="utf-8",
            )
            config = {
                "project": {"name": "demo"},
                "paths": {"tasks": "harness/tasks", "release_runs": "harness/release-runs"},
                "integrations": {"release_provider": {"provider": "command", "command": f"python3 {provider}"}},
            }

            result = run_release_status(root, config, done_tasks=["TASK-0001"])

            self.assertEqual(result.status, "released")
            release_input = load_data(result.run_path / "input.json")
            self.assertEqual(release_input["tasks"][0]["evidence"]["pr"]["output"]["external_id"], "42")

    def test_release_status_cli_runs_configured_provider(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = root / "release-provider.py"
            provider.write_text(
                """
import json
import sys

json.load(sys.stdin)
json.dump({"schema_version": 1, "provider": "local-release", "status": "skipped", "summary": "No release needed"}, sys.stdout)
""".lstrip(),
                encoding="utf-8",
            )
            (root / "harness.yml").write_text(
                f"""
schema_version: 1
project:
  name: demo
paths:
  tasks: harness/tasks
  runs: harness/runs
  release_runs: harness/release-runs
commands: {{}}
policies: {{}}
integrations:
  release_provider:
    provider: command
    command: python3 {provider}
""".strip()
                + "\n",
                encoding="utf-8",
            )
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(["release", "status"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 0)
            self.assertIn("release skipped:", output.getvalue())
            self.assertTrue(any((root / "harness" / "release-runs").glob("release-*")))

    def test_release_status_rejects_invalid_contract(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = root / "release-provider.py"
            provider.write_text("print('{\"schema_version\": 1, \"status\": \"maybe\"}')\n", encoding="utf-8")
            config = {"integrations": {"release_provider": {"provider": "command", "command": f"python3 {provider}"}}}

            with self.assertRaisesRegex(ValueError, "Release output status"):
                run_release_status(root, config)

    def test_release_provider_rejects_missing_python_module_before_creating_run(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = {
                "paths": {"release_runs": "harness/release-runs"},
                "integrations": {
                    "release_provider": {
                        "provider": "command",
                        "command": f"{sys.executable} -m definitely_missing_attestflow_release_provider",
                    }
                },
            }

            with self.assertRaisesRegex(ValueError, "Release provider command not found"):
                run_release_status(root, config)

            self.assertFalse((root / "harness" / "release-runs").exists())

    def test_release_provider_rejects_malformed_completed_task_before_creating_run(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = root / "release-provider.py"
            provider.write_text(
                "raise SystemExit('provider should not run when completed task state is malformed')\n",
                encoding="utf-8",
            )
            task_dir = root / "harness" / "tasks" / "done"
            task_dir.mkdir(parents=True)
            (task_dir / "TASK-bad.json").write_text("{not json\n", encoding="utf-8")
            config = {
                "paths": {"tasks": "harness/tasks", "release_runs": "harness/release-runs"},
                "integrations": {"release_provider": {"provider": "command", "command": f"python3 {provider}"}},
            }

            with self.assertRaisesRegex(ValueError, "failed to load completed task"):
                run_release_status(root, config)

            self.assertFalse((root / "harness" / "release-runs").exists())

    def test_release_provider_rejects_completed_task_in_wrong_state_directory_before_creating_run(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = root / "release-provider.py"
            provider.write_text(
                "raise SystemExit('provider should not run when completed task is in the wrong directory')\n",
                encoding="utf-8",
            )
            (root / "harness" / "tasks" / "ready").mkdir(parents=True)
            (root / "harness" / "tasks" / "ready" / "TASK-0001.json").write_text(
                """
{
  "schema_version": 1,
  "id": "TASK-0001",
  "title": "Ship login",
  "state": "done",
  "priority": 1,
  "type": "feature",
  "purpose": "Release this task.",
  "scope": ["login"],
  "out_of_scope": ["billing"],
  "requirements": {"confirmed": ["login works"], "unresolved": [], "assumptions": []},
  "bdd_scenarios": ["User can log in."],
  "unit_tests": ["tests/unit/test_login.py"],
  "acceptance": ["login shipped"],
  "dependencies": [],
  "blocks": [],
  "blockers": [],
  "files": {"read": [], "write": ["src/login.py"]},
  "agents": {"owner": "orchestrator", "allowed_roles": ["worker_agent"]},
  "external_inputs": {"credentials": [], "services": [], "user_decisions": []},
  "evidence": {"run_id": "run-1", "packet": "harness/runs/run-1/evidence.md"}
}
""".strip()
                + "\n",
                encoding="utf-8",
            )
            config = {
                "paths": {"tasks": "harness/tasks", "release_runs": "harness/release-runs"},
                "integrations": {"release_provider": {"provider": "command", "command": f"python3 {provider}"}},
            }

            with self.assertRaisesRegex(ValueError, "directory state 'ready' does not match task state 'done'"):
                run_release_status(root, config)

            self.assertFalse((root / "harness" / "release-runs").exists())

    def test_release_provider_rejects_duplicate_completed_task_ids_before_creating_run(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = root / "release-provider.py"
            provider.write_text(
                "raise SystemExit('provider should not run when completed task ids are duplicated')\n",
                encoding="utf-8",
            )
            task = """
{
  "schema_version": 1,
  "id": "TASK-0001",
  "title": "Ship login",
  "state": "done",
  "priority": 1,
  "type": "feature",
  "purpose": "Release this task.",
  "scope": ["login"],
  "out_of_scope": ["billing"],
  "requirements": {"confirmed": ["login works"], "unresolved": [], "assumptions": []},
  "bdd_scenarios": ["User can log in."],
  "unit_tests": ["tests/unit/test_login.py"],
  "acceptance": ["login shipped"],
  "dependencies": [],
  "blocks": [],
  "blockers": [],
  "files": {"read": [], "write": ["src/login.py"]},
  "agents": {"owner": "orchestrator", "allowed_roles": ["worker_agent"]},
  "external_inputs": {"credentials": [], "services": [], "user_decisions": []},
  "evidence": {"run_id": "run-1", "packet": "harness/runs/run-1/evidence.md"}
}
""".strip()
            (root / "harness" / "tasks" / "done").mkdir(parents=True)
            (root / "harness" / "tasks" / "archived").mkdir(parents=True)
            (root / "harness" / "tasks" / "done" / "TASK-0001.json").write_text(task + "\n", encoding="utf-8")
            archived = task.replace('"state": "done"', '"state": "archived"')
            (root / "harness" / "tasks" / "archived" / "TASK-0001.json").write_text(archived + "\n", encoding="utf-8")
            config = {
                "paths": {"tasks": "harness/tasks", "release_runs": "harness/release-runs"},
                "integrations": {"release_provider": {"provider": "command", "command": f"python3 {provider}"}},
            }

            with self.assertRaisesRegex(ValueError, "duplicate task id: TASK-0001"):
                run_release_status(root, config)

            self.assertFalse((root / "harness" / "release-runs").exists())

    def test_release_provider_rejects_missing_requested_done_task_before_creating_run(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = root / "release-provider.py"
            provider.write_text(
                "raise SystemExit('provider should not run when requested task summary is missing')\n",
                encoding="utf-8",
            )
            config = {
                "paths": {"tasks": "harness/tasks", "release_runs": "harness/release-runs"},
                "integrations": {"release_provider": {"provider": "command", "command": f"python3 {provider}"}},
            }

            with self.assertRaisesRegex(ValueError, "release task not found: TASK-4040"):
                run_release_status(root, config, done_tasks=["TASK-4040"])

            self.assertFalse((root / "harness" / "release-runs").exists())

    def test_release_provider_rejects_malformed_task_evidence_before_creating_run(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = root / "release-provider.py"
            provider.write_text(
                "raise SystemExit('provider should not run when task evidence is malformed')\n",
                encoding="utf-8",
            )
            (root / "harness" / "tasks" / "done").mkdir(parents=True)
            (root / "harness" / "ci-runs" / "ci-1").mkdir(parents=True)
            (root / "harness" / "ci-runs" / "ci-1" / "output.json").write_text("{not json\n", encoding="utf-8")
            (root / "harness" / "tasks" / "done" / "TASK-0001.json").write_text(
                """
{
  "schema_version": 1,
  "id": "TASK-0001",
  "title": "Ship login",
  "state": "done",
  "priority": 1,
  "type": "feature",
  "purpose": "Release this task.",
  "scope": ["login"],
  "out_of_scope": ["billing"],
  "requirements": {"confirmed": ["login works"], "unresolved": [], "assumptions": []},
  "bdd_scenarios": ["User can log in."],
  "unit_tests": ["tests/unit/test_login.py"],
  "acceptance": ["login shipped"],
  "dependencies": [],
  "blocks": [],
  "blockers": [],
  "files": {"read": [], "write": ["src/login.py"]},
  "agents": {"owner": "orchestrator", "allowed_roles": ["worker_agent"]},
  "external_inputs": {"credentials": [], "services": [], "user_decisions": []},
  "evidence": {
    "run_id": "run-1",
    "packet": "harness/runs/run-1/evidence.md",
    "ci": "harness/ci-runs/ci-1/output.json"
  }
}
""".strip()
                + "\n",
                encoding="utf-8",
            )
            config = {
                "paths": {"tasks": "harness/tasks", "release_runs": "harness/release-runs"},
                "integrations": {"release_provider": {"provider": "command", "command": f"python3 {provider}"}},
            }

            with self.assertRaisesRegex(ValueError, "failed to load evidence"):
                run_release_status(root, config)

            self.assertFalse((root / "harness" / "release-runs").exists())

    def test_release_provider_command_timeout_writes_evidence_logs(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = root / "release-provider.py"
            provider.write_text(
                """
import json
import time

time.sleep(0.3)
print(json.dumps({"schema_version": 1, "status": "released", "summary": "too late"}))
""".lstrip(),
                encoding="utf-8",
            )
            config = {
                "paths": {"release_runs": "harness/release-runs"},
                "integrations": {
                    "release_provider": {
                        "provider": "command",
                        "command": f"python3 {provider}",
                        "timeout_seconds": 0.05,
                    }
                },
            }

            with self.assertRaisesRegex(ValueError, "timed out"):
                run_release_status(root, config)

            run_dirs = sorted((root / "harness" / "release-runs").glob("release-*"))
            self.assertEqual(len(run_dirs), 1)
            self.assertIn("timed out", (run_dirs[0] / "stderr.log").read_text(encoding="utf-8"))

    def test_cli_release_status_reports_missing_provider(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "harness.yml").write_text(
                """
schema_version: 1
project:
  name: demo
paths:
  tasks: harness/tasks
  runs: harness/runs
commands: {}
policies: {}
integrations:
  release_provider:
    provider: command
    command: missing-attestflow-release-provider
""".strip()
                + "\n",
                encoding="utf-8",
            )
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                error = io.StringIO()
                with redirect_stderr(error):
                    exit_code = cli.main(["release", "status"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 1)
            self.assertIn("Release provider command not found", error.getvalue())


if __name__ == "__main__":
    unittest.main()
