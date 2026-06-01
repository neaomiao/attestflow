from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

import attestflow.cli as cli
from attestflow.io import dump_data, load_data
from attestflow.pr import run_pr_ensure, run_pr_merge, run_pr_status


class PrProviderTests(unittest.TestCase):
    def test_command_pr_provider_records_contract_evidence(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = root / "pr-provider.py"
            provider.write_text(
                """
import json
import sys

payload = json.load(sys.stdin)
assert payload["schema_version"] == 1
assert payload["action"] == "status"
assert payload["provider"] == "command"
assert payload["root"]
json.dump(
    {
        "schema_version": 1,
        "provider": "local-pr",
        "status": "merged",
        "summary": "PR merged",
        "external_id": "42",
        "url": "https://git.example/pull/42",
        "branch": "feature",
        "target_branch": "main",
        "checks": [{"name": "review", "status": "passed"}],
    },
    sys.stdout,
)
""".lstrip(),
                encoding="utf-8",
            )
            config = {
                "project": {"name": "demo"},
                "paths": {"pr_runs": "harness/pr-runs"},
                "integrations": {"pr_provider": {"provider": "command", "command": f"python3 {provider}"}},
            }

            result = run_pr_status(root, config)

            self.assertEqual(result.status, "merged")
            self.assertEqual(result.output["external_id"], "42")
            self.assertTrue((result.run_path / "input.json").exists())
            self.assertTrue((result.run_path / "stdout.log").exists())
            self.assertTrue((result.run_path / "stderr.log").exists())
            self.assertEqual(load_data(result.run_path / "input.json")["action"], "status")
            self.assertEqual(load_data(result.run_path / "output.json")["status"], "merged")

    def test_command_pr_ensure_provider_records_create_or_update_evidence(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = root / "pr-provider.py"
            provider.write_text(
                """
import json
import sys

payload = json.load(sys.stdin)
assert payload["schema_version"] == 1
assert payload["action"] == "ensure"
assert payload["task_id"] == "TASK-0001"
json.dump(
    {
        "schema_version": 1,
        "provider": "local-pr",
        "status": "open",
        "summary": "PR #42 open",
        "external_id": "42",
        "url": "https://git.example/pull/42",
        "branch": "feature/TASK-0001",
        "target_branch": "main",
        "checks": [],
    },
    sys.stdout,
)
""".lstrip(),
                encoding="utf-8",
            )
            config = {
                "project": {"name": "demo"},
                "paths": {"pr_runs": "harness/pr-runs"},
                "integrations": {"pr_provider": {"provider": "command", "command": f"python3 {provider}"}},
            }

            result = run_pr_ensure(root, config, task_id="TASK-0001")

            self.assertEqual(result.status, "open")
            self.assertEqual(result.output["external_id"], "42")
            self.assertEqual(load_data(result.run_path / "input.json")["action"], "ensure")
            self.assertEqual(load_data(result.run_path / "output.json")["status"], "open")

    def test_command_pr_merge_provider_records_merge_evidence(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = root / "pr-provider.py"
            provider.write_text(
                """
import json
import sys

payload = json.load(sys.stdin)
assert payload["schema_version"] == 1
assert payload["action"] == "merge"
assert payload["task_id"] == "TASK-0001"
json.dump(
    {
        "schema_version": 1,
        "provider": "local-pr",
        "status": "merged",
        "summary": "PR #42 merged",
        "external_id": "42",
        "url": "https://git.example/pull/42",
        "branch": "feature/TASK-0001",
        "target_branch": "main",
        "checks": [{"name": "merge", "status": "passed"}],
    },
    sys.stdout,
)
""".lstrip(),
                encoding="utf-8",
            )
            config = {
                "project": {"name": "demo"},
                "paths": {"pr_runs": "harness/pr-runs"},
                "integrations": {"pr_provider": {"provider": "command", "command": f"python3 {provider}"}},
            }

            result = run_pr_merge(root, config, task_id="TASK-0001")

            self.assertEqual(result.status, "merged")
            self.assertEqual(result.output["external_id"], "42")
            self.assertEqual(load_data(result.run_path / "input.json")["action"], "merge")
            self.assertEqual(load_data(result.run_path / "output.json")["status"], "merged")

    def test_pr_status_cli_runs_configured_provider(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = root / "pr-provider.py"
            provider.write_text(
                """
import json
import sys

json.load(sys.stdin)
json.dump({"schema_version": 1, "provider": "local-pr", "status": "open", "summary": "PR open"}, sys.stdout)
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
  pr_runs: harness/pr-runs
commands: {{}}
policies: {{}}
integrations:
  pr_provider:
    provider: command
    command: python3 {provider}
""".strip()
                + "\n",
                encoding="utf-8",
            )
            _write_ready_task(root, "TASK-0001")
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(["pr", "status"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 0)
            self.assertIn("pr open:", output.getvalue())
            self.assertTrue(any((root / "harness" / "pr-runs").glob("pr-*")))

    def test_pr_ensure_cli_runs_configured_provider(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = root / "pr-provider.py"
            provider.write_text(
                """
import json
import sys

payload = json.load(sys.stdin)
assert payload["action"] == "ensure"
assert payload["task_id"] == "TASK-0001"
json.dump({"schema_version": 1, "provider": "local-pr", "status": "open", "summary": "PR open"}, sys.stdout)
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
  pr_runs: harness/pr-runs
commands: {{}}
policies: {{}}
integrations:
  pr_provider:
    provider: command
    command: python3 {provider}
""".strip()
                + "\n",
                encoding="utf-8",
            )
            _write_ready_task(root, "TASK-0001")
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(["pr", "ensure", "TASK-0001"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 0)
            self.assertIn("pr ensure open:", output.getvalue())
            self.assertTrue(any((root / "harness" / "pr-runs").glob("pr-*")))

    def test_pr_merge_cli_runs_configured_provider(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = root / "pr-provider.py"
            provider.write_text(
                """
import json
import sys

payload = json.load(sys.stdin)
assert payload["action"] == "merge"
assert payload["task_id"] == "TASK-0001"
json.dump({"schema_version": 1, "provider": "local-pr", "status": "merged", "summary": "PR merged"}, sys.stdout)
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
  pr_runs: harness/pr-runs
commands: {{}}
policies: {{}}
integrations:
  pr_provider:
    provider: command
    command: python3 {provider}
""".strip()
                + "\n",
                encoding="utf-8",
            )
            _write_ready_task(root, "TASK-0001")
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(["pr", "merge", "TASK-0001"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 0)
            self.assertIn("pr merge merged:", output.getvalue())
            task = load_data(root / "harness" / "tasks" / "ready" / "TASK-0001.json")
            self.assertIn("pr_merge", task["evidence"])

    def test_pr_status_rejects_invalid_contract(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = root / "pr-provider.py"
            provider.write_text("print('{\"schema_version\": 1, \"status\": \"maybe\"}')\n", encoding="utf-8")
            config = {"integrations": {"pr_provider": {"provider": "command", "command": f"python3 {provider}"}}}

            with self.assertRaisesRegex(ValueError, "PR output status"):
                run_pr_status(root, config)

    def test_pr_provider_rejects_missing_python_module_before_creating_run(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = {
                "paths": {"pr_runs": "harness/pr-runs"},
                "integrations": {
                    "pr_provider": {
                        "provider": "command",
                        "command": f"{sys.executable} -m definitely_missing_attestflow_pr_provider",
                    }
                },
            }

            with self.assertRaisesRegex(ValueError, "PR provider command not found"):
                run_pr_status(root, config)

            self.assertFalse((root / "harness" / "pr-runs").exists())

    def test_pr_provider_command_timeout_writes_evidence_logs(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = root / "pr-provider.py"
            provider.write_text(
                """
import json
import time

time.sleep(0.3)
print(json.dumps({"schema_version": 1, "status": "merged", "summary": "too late"}))
""".lstrip(),
                encoding="utf-8",
            )
            config = {
                "paths": {"pr_runs": "harness/pr-runs"},
                "integrations": {
                    "pr_provider": {
                        "provider": "command",
                        "command": f"python3 {provider}",
                        "timeout_seconds": 0.05,
                    }
                },
            }

            with self.assertRaisesRegex(ValueError, "timed out"):
                run_pr_status(root, config)

            run_dirs = sorted((root / "harness" / "pr-runs").glob("pr-*"))
            self.assertEqual(len(run_dirs), 1)
            self.assertIn("timed out", (run_dirs[0] / "stderr.log").read_text(encoding="utf-8"))

    def test_builtin_pr_providers_use_provider_specific_adapters(self) -> None:
        fixtures = {
            "github": {
                "raw": {
                    "number": 42,
                    "url": "https://github.example/acme/repo/pull/42",
                    "state": "OPEN",
                    "isDraft": False,
                    "headRefName": "feature/login",
                    "baseRefName": "main",
                },
                "expected_status": "open",
                "expected_id": "42",
            },
            "gitlab": {
                "raw": {
                    "iid": 7,
                    "web_url": "https://gitlab.example/acme/repo/-/merge_requests/7",
                    "state": "merged",
                    "source_branch": "feature/login",
                    "target_branch": "main",
                },
                "expected_status": "merged",
                "expected_id": "7",
            },
        }
        for provider_name, fixture in fixtures.items():
            with self.subTest(provider=provider_name), TemporaryDirectory() as tmp:
                root = Path(tmp)
                fake_cli = root / "fake-pr"
                fake_cli.write_text(
                    "#!/usr/bin/env python3\n"
                    "import json, sys\n"
                    f"json.dump({fixture['raw']!r}, sys.stdout)\n",
                    encoding="utf-8",
                )
                fake_cli.chmod(0o755)
                config = {
                    "paths": {"pr_runs": "harness/pr-runs"},
                    "integrations": {
                        "pr_provider": {
                            "provider": provider_name,
                            "provider_options": {"command": str(fake_cli), "status_args": ["pr", "view", "--json"]},
                        }
                    },
                }

                result = run_pr_status(root, config, task_id="TASK-0001")

                self.assertEqual(result.status, fixture["expected_status"])
                self.assertEqual(load_data(result.run_path / "input.json")["provider"], provider_name)
                output = load_data(result.run_path / "output.json")
                self.assertEqual(output["provider"], provider_name)
                self.assertEqual(output["external_id"], fixture["expected_id"])
                self.assertTrue(output["summary"].startswith(provider_name))

    def test_builtin_github_pr_merge_runs_merge_then_status(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_path = root / "gh-args.jsonl"
            fake_cli = root / "fake-gh"
            fake_cli.write_text(
                "\n".join(
                    [
                        "#!/usr/bin/env python3",
                        "import json, sys",
                        "from pathlib import Path",
                        f"log_path = Path({str(log_path)!r})",
                        "args = sys.argv[1:]",
                        "log_path.open('a', encoding='utf-8').write(json.dumps(args) + '\\n')",
                        "if args[:2] == ['pr', 'view']:",
                        "    json.dump({",
                        "        'number': 42,",
                        "        'url': 'https://github.example/acme/repo/pull/42',",
                        "        'state': 'MERGED',",
                        "        'isDraft': False,",
                        "        'headRefName': 'codex/pr-auto-merge',",
                        "        'baseRefName': 'main',",
                        "    }, sys.stdout)",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            fake_cli.chmod(0o755)
            config = {
                "paths": {"pr_runs": "harness/pr-runs"},
                "integrations": {
                    "pr_provider": {
                        "provider": "github",
                        "provider_options": {
                            "command": str(fake_cli),
                            "merge_args": ["pr", "merge", "--auto", "--squash", "--delete-branch"],
                            "status_args": ["pr", "view", "--json"],
                            "repository": "acme/repo",
                        },
                    }
                },
            }

            result = run_pr_merge(root, config, task_id="TASK-0001")

            self.assertEqual(result.status, "merged")
            calls = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(calls[0], ["pr", "merge", "--auto", "--squash", "--delete-branch", "--repo", "acme/repo"])
            self.assertEqual(calls[1], ["pr", "view", "--json", "--repo", "acme/repo"])

    def test_cli_pr_providers_lists_builtin_adapters(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = cli.main(["pr", "providers"])

        self.assertEqual(exit_code, 0)
        text = output.getvalue()
        self.assertIn("github\tgh", text)
        self.assertIn("gitlab\tglab", text)

    def test_cli_pr_status_reports_missing_provider(self) -> None:
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
  pr_provider:
    provider: command
    command: missing-attestflow-pr-provider
""".strip()
                + "\n",
                encoding="utf-8",
            )
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                error = io.StringIO()
                with redirect_stderr(error):
                    exit_code = cli.main(["pr", "status"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 1)
            self.assertIn("PR provider command not found", error.getvalue())


def _write_ready_task(root: Path, task_id: str) -> None:
    task_dir = root / "harness" / "tasks" / "ready"
    task_dir.mkdir(parents=True, exist_ok=True)
    dump_data(
        {
            "schema_version": 1,
            "id": task_id,
            "title": "Update PR evidence",
            "state": "ready",
            "priority": 10,
            "type": "feature",
            "purpose": "Exercise PR provider evidence recording.",
            "context": [],
            "scope": ["PR provider"],
            "out_of_scope": ["code hosting SDK"],
            "requirements": {"confirmed": ["provider is configured"], "unresolved": [], "assumptions": []},
            "bdd_scenarios": ["PR evidence is saved."],
            "unit_tests": ["tests/unit/test_pr_provider.py"],
            "acceptance": ["task evidence references PR output"],
            "dependencies": [],
            "blocks": [],
            "blockers": [],
            "files": {"read": [], "write": ["attestflow/pr.py"]},
            "agents": {"owner": "orchestrator", "allowed_roles": []},
            "external_inputs": {"credentials": [], "services": [], "user_decisions": []},
            "evidence": {"session": None, "run_id": None, "red": None, "green": None, "verify": None, "packet": None},
            "links": {"issues": [], "prs": [], "docs": []},
            "risks": [],
            "notes": [],
            "created_at": "2026-05-31T00:00:00Z",
            "updated_at": "2026-05-31T00:00:00Z",
        },
        task_dir / f"{task_id}.json",
    )


if __name__ == "__main__":
    unittest.main()
