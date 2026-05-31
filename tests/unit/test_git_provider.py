from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import shlex
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

import attestflow.cli as cli
from attestflow.git import run_git_publish
from attestflow.io import dump_data, load_data


class GitProviderTests(unittest.TestCase):
    def test_command_git_provider_records_publish_evidence(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = root / "git-provider.py"
            provider.write_text(
                """
import json
import sys

payload = json.load(sys.stdin)
assert payload["schema_version"] == 1
assert payload["action"] == "publish"
assert payload["provider"] == "command"
assert payload["task_id"] == "TASK-0001"
assert payload["task"]["title"] == "Publish changes"
json.dump(
    {
        "schema_version": 1,
        "provider": "local-git",
        "status": "published",
        "summary": "committed and pushed",
        "branch": "codex/publish",
        "remote": "origin",
        "commit_before": "abc",
        "commit_after": "def",
        "pushed": True,
        "changes": ["README.md"],
    },
    sys.stdout,
)
""".lstrip(),
                encoding="utf-8",
            )
            config = {
                "paths": {"tasks": "harness/tasks", "git_runs": "harness/git-runs"},
                "integrations": {"git_provider": {"provider": "command", "command": f"{sys.executable} {provider}"}},
            }
            _write_active_task(root, "TASK-0001")

            result = run_git_publish(root, config, task_id="TASK-0001")

            self.assertEqual(result.status, "published")
            self.assertEqual(result.output["commit_after"], "def")
            self.assertTrue((result.run_path / "input.json").exists())
            self.assertEqual(load_data(result.run_path / "input.json")["action"], "publish")
            self.assertEqual(load_data(result.run_path / "output.json")["status"], "published")

    def test_builtin_git_provider_commits_and_pushes_current_branch(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            remote = Path(tmp) / "remote.git"
            root.mkdir()
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, stdout=subprocess.DEVNULL)
            _init_repo(root)
            subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=root, check=True)
            subprocess.run(["git", "switch", "-c", "codex/publish"], cwd=root, check=True, stdout=subprocess.DEVNULL)
            (root / "README.md").write_text("changed\n", encoding="utf-8")
            config = {
                "project": {"default_branch": "main"},
                "paths": {"tasks": "harness/tasks", "git_runs": "harness/git-runs"},
                "integrations": {
                    "git_provider": {
                        "provider": "git",
                        "provider_options": {"commit_message": "Publish test changes"},
                    }
                },
            }

            result = run_git_publish(root, config)

            self.assertEqual(result.status, "published")
            self.assertEqual(result.output["branch"], "codex/publish")
            self.assertNotEqual(result.output["commit_before"], result.output["commit_after"])
            self.assertTrue(result.output["pushed"])
            remote_head = subprocess.check_output(
                ["git", "rev-parse", "refs/heads/codex/publish"],
                cwd=remote,
                text=True,
            ).strip()
            self.assertEqual(remote_head, result.output["commit_after"])

    def test_publish_cli_runs_configured_provider_and_writes_task_evidence(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = root / "git-provider.py"
            provider.write_text(
                """
import json
import sys

json.load(sys.stdin)
json.dump(
    {
        "schema_version": 1,
        "provider": "local-git",
        "status": "published",
        "summary": "published",
        "branch": "codex/publish",
        "remote": "origin",
        "commit_before": "abc",
        "commit_after": "def",
        "pushed": True,
        "changes": [],
    },
    sys.stdout,
)
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
  git_runs: harness/git-runs
commands: {{}}
policies: {{}}
integrations:
  git_provider:
    provider: command
    command: {shlex.quote(sys.executable)} {shlex.quote(str(provider))}
""".strip()
                + "\n",
                encoding="utf-8",
            )
            _write_active_task(root, "TASK-0001")
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(["publish", "--task", "TASK-0001"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 0)
            self.assertIn("publish published:", output.getvalue())
            task = load_data(root / "harness" / "tasks" / "accepted" / "TASK-0001.json")
            self.assertTrue((root / task["evidence"]["git"]).exists())

    def test_publish_cli_reports_missing_provider(self) -> None:
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
  git_runs: harness/git-runs
commands: {}
policies: {}
integrations:
  git_provider:
    provider: command
    command: missing-attestflow-git-provider
""".strip()
                + "\n",
                encoding="utf-8",
            )
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                error = io.StringIO()
                with redirect_stderr(error):
                    exit_code = cli.main(["publish"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 1)
            self.assertIn("Git provider command not found", error.getvalue())


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "attestflow@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Attestflow Tests"], cwd=root, check=True)
    (root / "README.md").write_text("initial\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=root, check=True, stdout=subprocess.DEVNULL)


def _write_active_task(root: Path, task_id: str) -> None:
    task_dir = root / "harness" / "tasks" / "accepted"
    task_dir.mkdir(parents=True, exist_ok=True)
    run_dir = root / "harness" / "runs" / "run-1"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "evidence.md").write_text("# Evidence\n", encoding="utf-8")
    dump_data(
        {
            "schema_version": 1,
            "id": task_id,
            "title": "Publish changes",
            "state": "accepted",
            "priority": 10,
            "type": "feature",
            "purpose": "Exercise git publish evidence.",
            "context": [],
            "scope": ["publish"],
            "out_of_scope": ["manual git commands"],
            "requirements": {"confirmed": ["publish is configured"], "unresolved": [], "assumptions": []},
            "bdd_scenarios": ["Publish evidence is saved."],
            "unit_tests": ["tests/unit/test_git_provider.py"],
            "acceptance": ["task evidence references git publish output"],
            "dependencies": [],
            "blocks": [],
            "blockers": [],
            "files": {"read": [], "write": ["README.md"]},
            "agents": {"owner": "orchestrator", "allowed_roles": []},
            "external_inputs": {"credentials": [], "services": [], "user_decisions": []},
            "evidence": {
                "session": "harness/runs/run-1/session.yml",
                "run_id": "run-1",
                "packet": "harness/runs/run-1/evidence.md",
            },
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
