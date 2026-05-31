from __future__ import annotations

from contextlib import redirect_stdout
from copy import deepcopy
import io
from pathlib import Path
import shlex
import sys
from tempfile import TemporaryDirectory
import unittest

import attestflow.cli as cli
from attestflow.config import DEFAULT_CONFIG, load_config
from attestflow.io import dump_data, load_data
from attestflow.tasks import start_task, transition_task, verify_task


class OpenSourceP2Tests(unittest.TestCase):
    def test_verify_task_must_pass_before_transition_to_verified_and_accepted(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _configured_project(root, unit_command=f"{shlex.quote(sys.executable)} -c 'print(\"unit ok\")'")
            _write_ready_task(root, "TASK-0001")
            run = start_task(root, config, "TASK-0001", actor_role="orchestrator")
            transition_task(root, config, "TASK-0001", "review")

            with self.assertRaisesRegex(ValueError, "verification evidence"):
                transition_task(root, config, "TASK-0001", "verified")

            result = verify_task(root, config, "TASK-0001")
            self.assertEqual(result.failed, [])
            reviewed = load_data(root / "harness" / "tasks" / "review" / "TASK-0001.json")
            self.assertEqual(reviewed["evidence"]["verify"], f"harness/runs/{run.run_id}/metadata.yml")

            verified = transition_task(root, config, "TASK-0001", "verified")
            accepted = transition_task(root, config, "TASK-0001", "accepted")

            self.assertEqual(verified.task["state"], "verified")
            self.assertEqual(accepted.task["state"], "accepted")
            self.assertTrue((root / "harness" / "tasks" / "accepted" / "TASK-0001.json").exists())

    def test_failed_verify_task_blocks_transition_to_verified(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _configured_project(root, unit_command=f"{shlex.quote(sys.executable)} -c 'raise SystemExit(7)'")
            _write_ready_task(root, "TASK-0001")
            start_task(root, config, "TASK-0001", actor_role="orchestrator")
            transition_task(root, config, "TASK-0001", "review")

            result = verify_task(root, config, "TASK-0001")

            self.assertEqual(result.failed, ["unit"])
            with self.assertRaisesRegex(ValueError, "unit exit_code is 7"):
                transition_task(root, config, "TASK-0001", "verified")
            self.assertTrue((root / "harness" / "tasks" / "review" / "TASK-0001.json").exists())

    def test_cli_pr_ci_commands_write_delivery_evidence_back_to_task(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pr_provider = _write_pr_provider(root)
            ci_provider = _write_ci_provider(root)
            config = _configured_project(
                root,
                unit_command=f"{shlex.quote(sys.executable)} -c 'print(\"unit ok\")'",
                pr_command=f"{shlex.quote(sys.executable)} {shlex.quote(str(pr_provider))}",
                ci_command=f"{shlex.quote(sys.executable)} {shlex.quote(str(ci_provider))}",
            )
            _write_ready_task(root, "TASK-0001")
            start_task(root, config, "TASK-0001", actor_role="orchestrator")
            transition_task(root, config, "TASK-0001", "review")
            verify_task(root, config, "TASK-0001")
            transition_task(root, config, "TASK-0001", "verified")
            transition_task(root, config, "TASK-0001", "accepted")

            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    ensure_exit = cli.main(["pr", "ensure", "TASK-0001"])
                    ci_exit = cli.main(["ci", "status", "--task", "TASK-0001"])
                    pr_status_exit = cli.main(["pr", "status", "TASK-0001"])
            finally:
                cli.ROOT = original_root

            self.assertEqual((ensure_exit, ci_exit, pr_status_exit), (0, 0, 0))
            self.assertIn("pr ensure open:", output.getvalue())
            self.assertIn("ci passed:", output.getvalue())
            self.assertIn("pr merged:", output.getvalue())
            task = load_data(root / "harness" / "tasks" / "accepted" / "TASK-0001.json")
            self.assertTrue((root / task["evidence"]["pr_request"]).exists())
            self.assertTrue((root / task["evidence"]["ci"]).exists())
            self.assertTrue((root / task["evidence"]["pr"]).exists())
            self.assertEqual(load_data(root / task["evidence"]["pr_request"])["status"], "open")
            self.assertEqual(load_data(root / task["evidence"]["ci"])["status"], "passed")
            self.assertEqual(load_data(root / task["evidence"]["pr"])["status"], "merged")


def _configured_project(
    root: Path,
    *,
    unit_command: str,
    pr_command: str | None = None,
    ci_command: str | None = None,
) -> dict:
    config = deepcopy(DEFAULT_CONFIG)
    for name in config["commands"]:
        config["commands"][name] = None
    config["commands"]["unit"] = unit_command
    config["integrations"]["pr_provider"] = (
        {"provider": "command", "command": pr_command} if pr_command else "optional"
    )
    config["integrations"]["ci_provider"] = (
        {"provider": "command", "command": ci_command} if ci_command else "optional"
    )
    dump_data(config, root / "harness.yml")
    return load_config(root)


def _write_ready_task(root: Path, task_id: str) -> None:
    task_dir = root / "harness" / "tasks" / "ready"
    task_dir.mkdir(parents=True, exist_ok=True)
    dump_data(
        {
            "schema_version": 1,
            "id": task_id,
            "title": "Add validator",
            "state": "ready",
            "priority": 10,
            "type": "feature",
            "purpose": "Validate tasks before execution.",
            "context": [],
            "scope": ["task validation"],
            "out_of_scope": ["business code"],
            "requirements": {"confirmed": ["needs tests"], "unresolved": [], "assumptions": []},
            "bdd_scenarios": ["Ready task without BDD is rejected."],
            "unit_tests": ["tests/unit/test_task_lifecycle.py"],
            "acceptance": ["validator rejects incomplete ready tasks"],
            "dependencies": [],
            "blocks": [],
            "blockers": [],
            "files": {"read": [], "write": ["attestflow/tasks.py"]},
            "agents": {"owner": "orchestrator", "allowed_roles": ["worker_agent"]},
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


def _write_pr_provider(root: Path) -> Path:
    provider = root / "pr-provider.py"
    provider.write_text(
        """
import json
import sys

payload = json.load(sys.stdin)
status = "open" if payload["action"] == "ensure" else "merged"
json.dump({"schema_version": 1, "provider": "local-pr", "status": status, "summary": f"pr {status}", "checks": []}, sys.stdout)
""".lstrip(),
        encoding="utf-8",
    )
    return provider


def _write_ci_provider(root: Path) -> Path:
    provider = root / "ci-provider.py"
    provider.write_text(
        """
import json
import sys

json.load(sys.stdin)
json.dump({"schema_version": 1, "provider": "local-ci", "status": "passed", "summary": "ci passed", "checks": []}, sys.stdout)
""".lstrip(),
        encoding="utf-8",
    )
    return provider


if __name__ == "__main__":
    unittest.main()
