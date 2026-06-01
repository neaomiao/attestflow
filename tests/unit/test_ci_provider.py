from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

import attestflow.cli as cli
from attestflow.ci import run_ci_action, run_ci_status
from attestflow.io import load_data


class CiProviderTests(unittest.TestCase):
    def test_command_ci_provider_records_contract_evidence(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = root / "ci-provider.py"
            provider.write_text(
                """
import json
import sys

payload = json.load(sys.stdin)
assert payload["schema_version"] == 1
assert payload["provider"] == "command"
assert payload["root"]
json.dump(
    {
        "schema_version": 1,
        "provider": "local-ci",
        "status": "passed",
        "summary": "CI passed",
        "external_id": "run-123",
        "url": "https://ci.example/runs/123",
        "commit": "abc123",
        "branch": "main",
        "checks": [{"name": "unit", "status": "passed"}],
    },
    sys.stdout,
)
""".lstrip(),
                encoding="utf-8",
            )
            config = {
                "project": {"name": "demo"},
                "paths": {"ci_runs": "harness/ci-runs"},
                "integrations": {"ci_provider": {"provider": "command", "command": f"python3 {provider}"}},
            }

            result = run_ci_status(root, config)

            self.assertEqual(result.status, "passed")
            self.assertEqual(result.output["external_id"], "run-123")
            self.assertTrue((result.run_path / "input.json").exists())
            self.assertTrue((result.run_path / "stdout.log").exists())
            self.assertTrue((result.run_path / "stderr.log").exists())
            self.assertEqual(load_data(result.run_path / "output.json")["status"], "passed")

    def test_ci_status_cli_runs_configured_provider(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = root / "ci-provider.py"
            provider.write_text(
                """
import json
import sys

json.load(sys.stdin)
json.dump({"schema_version": 1, "provider": "local-ci", "status": "running", "summary": "CI still running"}, sys.stdout)
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
  ci_runs: harness/ci-runs
commands: {{}}
policies: {{}}
integrations:
  ci_provider:
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
                    exit_code = cli.main(["ci", "status"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 0)
            self.assertIn("ci running:", output.getvalue())
            self.assertTrue(any((root / "harness" / "ci-runs").glob("ci-*")))

    def test_ci_status_rejects_invalid_contract(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = root / "ci-provider.py"
            provider.write_text("print('{\"schema_version\": 1, \"status\": \"maybe\"}')\n", encoding="utf-8")
            config = {"integrations": {"ci_provider": {"provider": "command", "command": f"python3 {provider}"}}}

            with self.assertRaisesRegex(ValueError, "CI output status"):
                run_ci_status(root, config)

    def test_ci_provider_rejects_missing_python_module_before_creating_run(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = {
                "paths": {"ci_runs": "harness/ci-runs"},
                "integrations": {
                    "ci_provider": {
                        "provider": "command",
                        "command": f"{sys.executable} -m definitely_missing_attestflow_ci_provider",
                    }
                },
            }

            with self.assertRaisesRegex(ValueError, "CI provider command not found"):
                run_ci_status(root, config)

            self.assertFalse((root / "harness" / "ci-runs").exists())

    def test_ci_provider_command_timeout_writes_evidence_logs(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = root / "ci-provider.py"
            provider.write_text(
                """
import json
import time

time.sleep(0.3)
print(json.dumps({"schema_version": 1, "status": "passed", "summary": "too late"}))
""".lstrip(),
                encoding="utf-8",
            )
            config = {
                "paths": {"ci_runs": "harness/ci-runs"},
                "integrations": {
                    "ci_provider": {
                        "provider": "command",
                        "command": f"python3 {provider}",
                        "timeout_seconds": 0.05,
                    }
                },
            }

            with self.assertRaisesRegex(ValueError, "timed out"):
                run_ci_status(root, config)

            run_dirs = sorted((root / "harness" / "ci-runs").glob("ci-*"))
            self.assertEqual(len(run_dirs), 1)
            self.assertIn("timed out", (run_dirs[0] / "stderr.log").read_text(encoding="utf-8"))

    def test_github_actions_ci_provider_uses_builtin_adapter(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_gh = root / "fake-gh"
            fake_gh.write_text(
                """
#!/usr/bin/env python3
import json
import sys

json.dump(
    [{"databaseId": 321, "status": "completed", "conclusion": "failure", "workflowName": "CI"}],
    sys.stdout,
)
""".lstrip(),
                encoding="utf-8",
            )
            fake_gh.chmod(0o755)
            config = {
                "paths": {"ci_runs": "harness/ci-runs"},
                "integrations": {
                    "ci_provider": {
                        "provider": "github-actions",
                        "provider_options": {"command": str(fake_gh)},
                    }
                },
            }

            result = run_ci_status(root, config)

            self.assertEqual(result.status, "failed")
            self.assertEqual(load_data(result.run_path / "input.json")["provider"], "github-actions")
            self.assertEqual(load_data(result.run_path / "output.json")["external_id"], "321")

    def test_ci_logs_cli_runs_github_actions_action_and_records_task_evidence(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_gh = root / "fake-gh"
            fake_gh.write_text(
                """
#!/usr/bin/env python3
import json
import sys

args = sys.argv[1:]
if args[:3] == ["run", "view", "456"] and "--json" in args:
    json.dump(
        {
            "databaseId": 456,
            "status": "completed",
            "conclusion": "failure",
            "workflowName": "CI",
            "headSha": "abc123",
            "jobs": [{"name": "unit", "conclusion": "failure"}],
        },
        sys.stdout,
    )
elif args[:3] == ["run", "view", "456"] and "--log-failed" in args:
    sys.stdout.write("unit failure line\\n")
else:
    raise SystemExit(f"unexpected args: {args}")
""".lstrip(),
                encoding="utf-8",
            )
            fake_gh.chmod(0o755)
            (root / "harness" / "tasks" / "proposed").mkdir(parents=True)
            (root / "harness" / "tasks" / "proposed" / "TASK-0001.json").write_text(
                """
{
  "schema_version": 1,
  "id": "TASK-0001",
  "title": "Fix CI",
  "state": "proposed",
  "priority": 5,
  "type": "bug",
  "evidence": {}
}
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
  ci_runs: harness/ci-runs
commands: {{}}
policies: {{}}
integrations:
  ci_provider:
    provider: github-actions
    provider_options:
      command: {fake_gh}
""".strip()
                + "\n",
                encoding="utf-8",
            )
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(["ci", "logs", "--run-id", "456", "--task", "TASK-0001"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 0)
            self.assertIn("ci failed:", output.getvalue())
            task = load_data(root / "harness" / "tasks" / "proposed" / "TASK-0001.json")
            ci_ref = task["evidence"]["ci"]
            ci_output = load_data(root / ci_ref)
            self.assertEqual(ci_output["action"], "logs")
            self.assertIn("unit failure line", ci_output["logs"]["failed"])

    def test_run_ci_action_dispatch_passes_action_to_builtin_adapter(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_gh = root / "fake-gh"
            fake_gh.write_text(
                """
#!/usr/bin/env python3
import sys

args = sys.argv[1:]
assert args == ["workflow", "run", "ci.yml", "--ref", "feature/actions", "-f", "task=TASK-0001"]
""".lstrip(),
                encoding="utf-8",
            )
            fake_gh.chmod(0o755)
            config = {
                "paths": {"ci_runs": "harness/ci-runs"},
                "integrations": {
                    "ci_provider": {
                        "provider": "github-actions",
                        "provider_options": {
                            "command": str(fake_gh),
                            "workflow": "ci.yml",
                            "ref": "feature/actions",
                            "inputs": {"task": "TASK-0001"},
                        },
                    }
                },
            }

            result = run_ci_action(root, config, action="dispatch")

            self.assertEqual(result.status, "queued")
            self.assertEqual(load_data(result.run_path / "input.json")["action"], "dispatch")
            self.assertEqual(load_data(result.run_path / "output.json")["action"], "dispatch")

    def test_builtin_delivery_ci_providers_use_provider_specific_adapters(self) -> None:
        fixtures = {
            "gitlab-ci": {
                "raw": {"id": 42, "status": "success", "web_url": "https://gitlab.example/pipelines/42", "ref": "main", "sha": "abc"},
                "expected_status": "passed",
                "expected_id": "42",
            },
            "buildkite": {
                "raw": {"id": "bk-1", "state": "running", "web_url": "https://buildkite.example/builds/1", "branch": "main", "commit": "def"},
                "expected_status": "running",
                "expected_id": "bk-1",
            },
            "circleci": {
                "raw": {"id": "cc-1", "status": "failed", "web_url": "https://circleci.example/workflow/1", "vcs": {"branch": "main", "revision": "ghi"}},
                "expected_status": "failed",
                "expected_id": "cc-1",
            },
        }
        for provider_name, fixture in fixtures.items():
            with self.subTest(provider=provider_name), TemporaryDirectory() as tmp:
                root = Path(tmp)
                fake_cli = root / "fake-ci"
                fake_cli.write_text(
                    "#!/usr/bin/env python3\n"
                    "import json, sys\n"
                    f"json.dump({fixture['raw']!r}, sys.stdout)\n",
                    encoding="utf-8",
                )
                fake_cli.chmod(0o755)
                config = {
                    "paths": {"ci_runs": "harness/ci-runs"},
                    "integrations": {
                        "ci_provider": {
                            "provider": provider_name,
                            "provider_options": {"command": str(fake_cli), "status_args": ["status", "--json"]},
                        }
                    },
                }

                result = run_ci_status(root, config)

                self.assertEqual(result.status, fixture["expected_status"])
                self.assertEqual(load_data(result.run_path / "input.json")["provider"], provider_name)
                output = load_data(result.run_path / "output.json")
                self.assertEqual(output["provider"], provider_name)
                self.assertEqual(output["external_id"], fixture["expected_id"])
                self.assertTrue(output["summary"].startswith(provider_name))

    def test_cli_ci_status_reports_missing_provider(self) -> None:
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
  ci_provider:
    provider: command
    command: missing-attestflow-ci-provider
""".strip()
                + "\n",
                encoding="utf-8",
            )
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                error = io.StringIO()
                with redirect_stderr(error):
                    exit_code = cli.main(["ci", "status"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 1)
            self.assertIn("CI provider command not found", error.getvalue())


if __name__ == "__main__":
    unittest.main()
