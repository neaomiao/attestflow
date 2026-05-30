from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import attestflow.cli as cli
from attestflow.ci import run_ci_status
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
