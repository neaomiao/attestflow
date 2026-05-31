from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

import attestflow.cli as cli
from attestflow.cli import cmd_init
from attestflow.io import dump_data, load_data


ROOT = Path(__file__).resolve().parents[2]


class OpenSourceP1Tests(unittest.TestCase):
    def test_contract_validate_cli_accepts_valid_output_and_reports_specific_errors(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            valid = root / "capability-output.json"
            invalid = root / "invalid-capability-output.json"
            dump_data(
                {
                    "schema_version": 1,
                    "status": "passed",
                    "summary": "review passed",
                    "findings": [],
                    "evidence": [],
                },
                valid,
            )
            dump_data({"schema_version": 1, "status": "maybe", "summary": ""}, invalid)

            output = io.StringIO()
            with redirect_stdout(output):
                valid_exit = cli.main(["contract", "validate", "capability-output", str(valid)])

            error = io.StringIO()
            with redirect_stderr(error):
                invalid_exit = cli.main(["contract", "validate", "capability-output", str(invalid)])

            self.assertEqual(valid_exit, 0)
            self.assertIn("contract capability-output valid", output.getvalue())
            self.assertEqual(invalid_exit, 1)
            self.assertIn("capability-output.status must be one of: passed, failed, blocked", error.getvalue())
            self.assertIn("capability-output.summary must be non-empty", error.getvalue())

    def test_contract_validate_covers_core_provider_output_types(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures = {
                "planner-output": {
                    "schema_version": 1,
                    "goal": "ship",
                    "tasks": [
                        {
                            "title": "Task",
                            "purpose": "Ship a scoped task.",
                            "scope": ["implementation"],
                            "out_of_scope": ["unrelated work"],
                            "requirements": {"confirmed": ["needed"], "unresolved": [], "assumptions": []},
                            "bdd_scenarios": ["Given input, when run, then output is correct."],
                            "unit_tests": ["unit tests pass"],
                            "acceptance": ["task is done"],
                            "dependencies": [],
                            "files": {"read": [], "write": ["README.md"]},
                        }
                    ],
                },
                "session-launch-output": {"schema_version": 1, "status": "launched", "summary": "started"},
                "session-resume-output": {"schema_version": 1, "status": "resumed", "summary": "continued"},
                "ci-output": {"schema_version": 1, "status": "passed", "summary": "CI passed", "checks": []},
                "pr-output": {"schema_version": 1, "status": "open", "summary": "PR open", "checks": []},
                "release-output": {"schema_version": 1, "status": "released", "summary": "released", "artifacts": []},
            }
            for contract_type, payload in fixtures.items():
                path = root / f"{contract_type}.json"
                dump_data(payload, path)
                with self.subTest(contract_type=contract_type):
                    output = io.StringIO()
                    with redirect_stdout(output):
                        exit_code = cli.main(["contract", "validate", contract_type, str(path)])
                    self.assertEqual(exit_code, 0)
                    self.assertIn(f"contract {contract_type} valid", output.getvalue())

    def test_contract_validate_task_uses_runtime_task_schema(self) -> None:
        with TemporaryDirectory() as tmp:
            task_path = Path(tmp) / "TASK-0001.json"
            dump_data({"schema_version": 1, "id": "TASK-0001", "state": "ready"}, task_path)

            error = io.StringIO()
            with redirect_stderr(error):
                exit_code = cli.main(["contract", "validate", "task", str(task_path)])

            self.assertEqual(exit_code, 1)
            self.assertIn("task.missing required fields", error.getvalue())

    def test_evidence_export_writes_task_bundle_manifest_and_referenced_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_harness_config(root)
            _write_done_task_with_evidence(root)
            output_dir = root / "artifacts" / "TASK-0001"

            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(["evidence", "export", "TASK-0001", "--out", str(output_dir)])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 0)
            self.assertIn("exported evidence TASK-0001", output.getvalue())
            manifest = load_data(output_dir / "manifest.json")
            self.assertEqual(manifest["task_id"], "TASK-0001")
            self.assertEqual(manifest["run_id"], "run-1")
            self.assertIn("task.json", manifest["files"])
            self.assertTrue((output_dir / "task.json").exists())
            self.assertTrue((output_dir / "runs" / "run-1" / "evidence.md").exists())
            self.assertTrue((output_dir / "runs" / "run-1" / "metadata.yml").exists())
            self.assertTrue((output_dir / "runs" / "run-1" / "ledger.jsonl").exists())
            self.assertTrue((output_dir / "capability-runs" / "reviewer-TASK-0001" / "output.json").exists())

    def test_init_supports_go_and_rust_adapters_with_default_commands(self) -> None:
        with TemporaryDirectory() as go_tmp, TemporaryDirectory() as rust_tmp:
            go_root = Path(go_tmp)
            rust_root = Path(rust_tmp)
            (go_root / "go.mod").write_text("module example.com/demo\n\ngo 1.22\n", encoding="utf-8")
            (rust_root / "Cargo.toml").write_text("[package]\nname = \"demo\"\nversion = \"0.1.0\"\n", encoding="utf-8")

            go_exit = cmd_init(SimpleNamespace(path=str(go_root), adapter="go", agent_provider="command", agent_command=None))
            rust_exit = cmd_init(
                SimpleNamespace(path=str(rust_root), adapter="rust", agent_provider="command", agent_command=None)
            )
            go_config = load_data(go_root / "harness.yml")
            rust_config = load_data(rust_root / "harness.yml")

            self.assertEqual(go_exit, 0)
            self.assertEqual(go_config["project"]["adapter"], "go")
            self.assertEqual(go_config["project"]["module"], "go")
            self.assertEqual(go_config["commands"]["unit"], "go test ./...")
            self.assertTrue((go_root / "harness" / "adapters" / "go" / "README.md").exists())
            self.assertEqual(rust_exit, 0)
            self.assertEqual(rust_config["project"]["adapter"], "rust")
            self.assertEqual(rust_config["project"]["module"], "rust")
            self.assertEqual(rust_config["commands"]["unit"], "cargo test")
            self.assertEqual(rust_config["commands"]["typecheck"], "cargo check --all-targets --all-features")
            self.assertTrue((rust_root / "harness" / "adapters" / "rust" / "README.md").exists())

    def test_github_actions_pr_example_blocks_without_evidence_and_uploads_bundle(self) -> None:
        workflow = ROOT / "examples" / "github-actions" / "attestflow-pr.yml"
        self.assertTrue(workflow.exists())
        text = workflow.read_text(encoding="utf-8")
        self.assertIn("attestflow verify", text)
        self.assertIn("attestflow evidence export", text)
        self.assertIn("actions/upload-artifact", text)
        self.assertIn("No completed Attestflow task evidence found", text)
        self.assertIn("exit 1", text)

    def test_autopilot_status_prints_next_command_for_recovery_states(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_harness_config(root)
            metadata_path = root / "harness" / "autopilot-runs" / "run-1" / "metadata.json"
            metadata_path.parent.mkdir(parents=True)
            dump_data(
                {
                    "run_id": "run-1",
                    "status": "paused",
                    "pause_reason": "max_steps reached",
                    "steps": 1,
                    "blocked": [],
                    "failed": [],
                },
                metadata_path,
            )
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(["autopilot", "--status"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 0)
            self.assertIn("next:", output.getvalue())
            self.assertIn("python -m attestflow autopilot --resume", output.getvalue())


def _write_harness_config(root: Path) -> None:
    dump_data(
        {
            "schema_version": 1,
            "project": {"name": "demo", "adapter": "generic"},
            "paths": {
                "tasks": "harness/tasks",
                "runs": "harness/runs",
                "capability_runs": "harness/capability-runs",
                "autopilot_runs": "harness/autopilot-runs",
            },
            "commands": {},
            "policies": {},
        },
        root / "harness.yml",
    )


def _write_done_task_with_evidence(root: Path) -> None:
    task_dir = root / "harness" / "tasks" / "done"
    task_dir.mkdir(parents=True)
    run_dir = root / "harness" / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    capability_dir = root / "harness" / "capability-runs" / "reviewer-TASK-0001"
    capability_dir.mkdir(parents=True)
    (run_dir / "evidence.md").write_text("# Evidence\n\n- ID: TASK-0001\n- Run: run-1\n", encoding="utf-8")
    dump_data({"run_id": "run-1", "task_id": "TASK-0001", "status": "closed"}, run_dir / "metadata.yml")
    (run_dir / "ledger.jsonl").write_text('{"event":"closed"}\n', encoding="utf-8")
    dump_data(
        {"schema_version": 1, "status": "passed", "summary": "review passed", "findings": [], "evidence": []},
        capability_dir / "output.json",
    )
    dump_data(
        {
            "schema_version": 1,
            "id": "TASK-0001",
            "title": "Ship feature",
            "state": "done",
            "priority": 100,
            "type": "feature",
            "purpose": "Ship a feature",
            "scope": ["implement feature"],
            "out_of_scope": ["unrelated work"],
            "requirements": {"confirmed": ["feature is needed"], "unresolved": [], "assumptions": []},
            "bdd_scenarios": ["Given a user, when they run it, then it works"],
            "unit_tests": ["unit tests pass"],
            "acceptance": ["feature is done"],
            "dependencies": [],
            "files": {"read": [], "write": ["feature.py"]},
            "agents": {"owner": "orchestrator", "allowed_roles": []},
            "external_inputs": {"credentials": [], "services": [], "user_decisions": []},
            "evidence": {
                "run_id": "run-1",
                "packet": "harness/runs/run-1/evidence.md",
                "verify": "harness/runs/run-1/metadata.yml",
                "capabilities": {"reviewer": "harness/capability-runs/reviewer-TASK-0001/output.json"},
            },
            "links": {"issues": [], "prs": [], "docs": []},
            "risks": [],
            "notes": [],
            "created_at": "2026-05-31T00:00:00+00:00",
            "updated_at": "2026-05-31T00:00:00+00:00",
        },
        task_dir / "TASK-0001.json",
    )


if __name__ == "__main__":
    unittest.main()
