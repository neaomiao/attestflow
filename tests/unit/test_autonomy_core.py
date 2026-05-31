from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import shlex
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

import attestflow.cli as cli
from attestflow.config import DEFAULT_CONFIG
from attestflow.io import dump_data, load_data
from tests.unit.test_orchestrator import ready_task, write_capability_stub, write_task


class AutonomyCoreTests(unittest.TestCase):
    def test_cli_autonomy_doctor_json_reports_delivery_blockers(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            provider = root / "provider.py"
            provider.write_text(
                "import json, sys\n"
                "json.load(sys.stdin)\n"
                "json.dump({'schema_version': 1, 'status': 'skipped', 'summary': 'not needed'}, sys.stdout)\n",
                encoding="utf-8",
            )
            dump_data(
                {
                    "schema_version": 1,
                    "project": {"name": "demo"},
                    "paths": {
                        "tasks": "harness/tasks",
                        "runs": "harness/runs",
                        "locks": "harness/locks",
                        "capability_runs": "harness/capability-runs",
                        "autopilot_runs": "harness/autopilot-runs",
                        "ci_runs": "harness/ci-runs",
                        "pr_runs": "harness/pr-runs",
                        "release_runs": "harness/release-runs",
                    },
                    "commands": {},
                    "policies": {},
                    "sessions": {"agent_provider": "command"},
                    "integrations": {
                        "pr_provider": {"provider": "command", "command": f"{shlex.quote(sys.executable)} {shlex.quote(str(provider))}"}
                    },
                },
                root / "harness.yml",
            )
            for directory in (
                "harness/tasks/ready",
                "harness/runs",
                "harness/locks",
                "harness/capability-runs",
                "harness/autopilot-runs",
                "harness/ci-runs",
                "harness/pr-runs",
                "harness/release-runs",
            ):
                (root / directory).mkdir(parents=True, exist_ok=True)
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(["autonomy", "doctor", "--json"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 1)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["schema_version"], 1)
            self.assertEqual(payload["status"], "blocked")
            checks = {check["name"]: check for check in payload["checks"]}
            self.assertEqual(checks["git_remote"]["status"], "blocked")
            self.assertIn("PR provider", checks["git_remote"]["summary"])
            self.assertEqual(checks["provider_commands"]["status"], "passed")

    def test_autopilot_until_terminal_resumes_paused_run_without_manual_resume(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            capability_script = root / "capability.py"
            write_capability_stub(capability_script)
            command = f"{shlex.quote(sys.executable)} {shlex.quote(str(capability_script))}"
            config = deepcopy(DEFAULT_CONFIG)
            config["paths"] = {
                **config.get("paths", {}),
                "tasks": "harness/tasks",
                "runs": "harness/runs",
                "locks": "harness/locks",
                "capability_runs": "harness/capability-runs",
                "autopilot_runs": "harness/autopilot-runs",
            }
            config["commands"] = {
                "bdd": f"{shlex.quote(sys.executable)} -c 'print(\"bdd ok\")'",
                "unit": f"{shlex.quote(sys.executable)} -c 'print(\"unit ok\")'",
                "lint": None,
                "typecheck": None,
                "secret_scan": None,
                "project_verify": None,
            }
            config["autopilot"] = {"default_limit": 1, "max_steps": 1, "max_loop_cycles": 20}
            for capability in ("bdd", "tdd", "implementer", "reviewer"):
                config["capabilities"][capability]["command"] = command
            dump_data(config, root / "harness.yml")
            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001"))
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(["autopilot", "--run", "--until", "terminal", "--max-steps", "1"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 0)
            self.assertIn("status=finished", output.getvalue())
            self.assertTrue((root / "harness" / "tasks" / "done" / "TASK-0001.json").exists())
            metadata = load_data(sorted((root / "harness" / "autopilot-runs").glob("*/metadata.json"))[-1])
            self.assertEqual(metadata["loop_stop_reason"], "terminal_status")
            self.assertGreater(metadata["loop_cycles"], 1)


if __name__ == "__main__":
    unittest.main()
