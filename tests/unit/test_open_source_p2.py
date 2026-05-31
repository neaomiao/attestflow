from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
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

    def test_evidence_bundle_exports_autopilot_release_pr_comment_and_audit_manifest(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _configured_project(root, unit_command=f"{shlex.quote(sys.executable)} -c 'print(\"unit ok\")'")
            _write_done_task_evidence(root, "TASK-0001")
            _write_release_run(root, "release-1")
            _write_autopilot_run(root, "auto-1", ["TASK-0001"], release_run_id="release-1")
            output_dir = root / "artifacts" / "auto-1"

            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    bundle_exit = cli.main(["evidence", "bundle", "--run", "auto-1", "--out", str(output_dir)])
                    verify_exit = cli.main(["evidence", "verify", str(output_dir)])
            finally:
                cli.ROOT = original_root

            self.assertEqual((bundle_exit, verify_exit), (0, 0))
            self.assertIn("exported autopilot evidence auto-1", output.getvalue())
            self.assertIn("evidence bundle valid", output.getvalue())
            manifest = load_data(output_dir / "manifest.json")
            self.assertEqual(manifest["kind"], "autopilot")
            self.assertEqual(manifest["run_id"], "auto-1")
            self.assertIn("artifacts", manifest)
            self.assertIn("autopilot-runs/auto-1/metadata.json", manifest["files"])
            self.assertIn("release-runs/release-1/output.json", manifest["files"])
            self.assertIn("tasks/done/TASK-0001.json", manifest["files"])
            self.assertTrue((output_dir / "pr-comment.md").exists())
            self.assertTrue((output_dir / "audit.md").exists())
            self.assertIn("auto-1", (output_dir / "pr-comment.md").read_text(encoding="utf-8"))
            self.assertIn("released", (output_dir / "audit.md").read_text(encoding="utf-8"))

    def test_evidence_bundle_exports_release_run_bundle(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _configured_project(root, unit_command=f"{shlex.quote(sys.executable)} -c 'print(\"unit ok\")'")
            _write_release_run(root, "release-1")
            output_dir = root / "artifacts" / "release-1"

            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(["evidence", "bundle", "--release", "release-1", "--out", str(output_dir)])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 0)
            self.assertIn("exported release evidence release-1", output.getvalue())
            manifest = load_data(output_dir / "manifest.json")
            self.assertEqual(manifest["kind"], "release")
            self.assertEqual(manifest["release_run_id"], "release-1")
            self.assertIn("release-runs/release-1/output.json", manifest["files"])

    def test_evidence_verify_detects_tampered_bundle_file(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _configured_project(root, unit_command=f"{shlex.quote(sys.executable)} -c 'print(\"unit ok\")'")
            _write_done_task_evidence(root, "TASK-0001")
            _write_autopilot_run(root, "auto-1", ["TASK-0001"])
            output_dir = root / "artifacts" / "auto-1"
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                cli.main(["evidence", "bundle", "--run", "auto-1", "--out", str(output_dir)])
                (output_dir / "autopilot-runs" / "auto-1" / "metadata.json").write_text('{"tampered": true}\n', encoding="utf-8")
                error = io.StringIO()
                with redirect_stderr(error):
                    exit_code = cli.main(["evidence", "verify", str(output_dir)])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 1)
            self.assertIn("hash mismatch", error.getvalue())

    def test_evidence_verify_detects_stale_source_when_requested(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _configured_project(root, unit_command=f"{shlex.quote(sys.executable)} -c 'print(\"unit ok\")'")
            _write_done_task_evidence(root, "TASK-0001")
            _write_autopilot_run(root, "auto-1", ["TASK-0001"])
            output_dir = root / "artifacts" / "auto-1"
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                cli.main(["evidence", "bundle", "--run", "auto-1", "--out", str(output_dir)])
                metadata_path = root / "harness" / "autopilot-runs" / "auto-1" / "metadata.json"
                metadata = load_data(metadata_path)
                metadata["status"] = "failed"
                dump_data(metadata, metadata_path)
                error = io.StringIO()
                with redirect_stderr(error):
                    exit_code = cli.main(["evidence", "verify", str(output_dir), "--check-source"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 1)
            self.assertIn("stale source", error.getvalue())

    def test_inspect_run_prints_timeline_blockers_provider_failures_and_next_action(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _configured_project(root, unit_command=f"{shlex.quote(sys.executable)} -c 'print(\"unit ok\")'")
            _write_inspectable_autopilot_run(root, "auto-1")
            _write_blocked_task(root, "TASK-0002")
            _write_provider_failure(root, "reviewer-TASK-0003")

            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(["inspect", "--run", "auto-1"])
            finally:
                cli.ROOT = original_root

            text = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("inspect run: auto-1", text)
            self.assertIn("status=paused", text)
            self.assertIn("timeline:", text)
            self.assertIn("planner_finished", text)
            self.assertIn("blockers:", text)
            self.assertIn("TASK-0002", text)
            self.assertIn("needs API key", text)
            self.assertIn("provider failures:", text)
            self.assertIn("rate_limited", text)
            self.assertIn("retry_later", text)
            self.assertIn("next: resolve blockers", text)

    def test_inspect_run_json_reports_timeline_blockers_provider_failures_and_next_action(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _configured_project(root, unit_command=f"{shlex.quote(sys.executable)} -c 'print(\"unit ok\")'")
            _write_inspectable_autopilot_run(root, "auto-1")
            _write_blocked_task(root, "TASK-0002")
            _write_provider_failure(root, "reviewer-TASK-0003")

            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(["inspect", "--run", "auto-1", "--json"])
            finally:
                cli.ROOT = original_root

            report = json.loads(output.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(report["run_id"], "auto-1")
            self.assertEqual(report["status"], "paused")
            self.assertEqual(report["timeline"][1]["event"], "planner_finished")
            self.assertEqual(report["blockers"][0]["task_id"], "TASK-0002")
            self.assertEqual(report["provider_failures"][0]["type"], "rate_limited")
            self.assertIn("resolve blockers", report["next_action"])

    def test_inspect_diff_reports_run_status_action_and_release_changes(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _configured_project(root, unit_command=f"{shlex.quote(sys.executable)} -c 'print(\"unit ok\")'")
            _write_run_metadata(
                root,
                "auto-old",
                {"status": "paused", "actions": ["autopilot:intake"], "release_status": "pending"},
            )
            _write_run_metadata(
                root,
                "auto-new",
                {
                    "status": "failed",
                    "actions": ["autopilot:intake", "autopilot:planner_retry"],
                    "planned": ["TASK-0001"],
                    "release_status": "failed",
                },
            )

            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(["inspect", "--diff", "auto-old", "auto-new"])
            finally:
                cli.ROOT = original_root

            text = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("run diff: auto-old -> auto-new", text)
            self.assertIn("status: paused -> failed", text)
            self.assertIn("release_status: pending -> failed", text)
            self.assertIn("actions_added: autopilot:planner_retry", text)
            self.assertIn("planned_added: TASK-0001", text)

    def test_inspect_reports_missing_run_metadata_with_clear_error(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _configured_project(root, unit_command=f"{shlex.quote(sys.executable)} -c 'print(\"unit ok\")'")
            (root / "harness" / "autopilot-runs" / "empty-run").mkdir(parents=True)

            original_root = cli.ROOT
            cli.ROOT = root
            try:
                error = io.StringIO()
                with redirect_stderr(error):
                    exit_code = cli.main(["inspect", "--run", "empty-run"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 1)
            self.assertIn(
                "autopilot run metadata missing: harness/autopilot-runs/empty-run/metadata.json",
                error.getvalue(),
            )

    def test_recover_dry_run_reports_orphan_run_mismatched_task_and_interrupted_session(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _configured_project(root, unit_command=f"{shlex.quote(sys.executable)} -c 'print(\"unit ok\")'")
            _write_orphan_autopilot_run(root, "auto-orphan")
            _write_mismatched_task(root, "TASK-0099", directory_state="ready", task_state="review")
            _write_interrupted_session_run(root, "run-cancelled", "TASK-0098")

            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(["recover", "--json"])
            finally:
                cli.ROOT = original_root

            report = json.loads(output.getvalue())
            issue_types = {issue["type"] for issue in report["issues"]}
            self.assertEqual(exit_code, 0)
            self.assertFalse(report["applied"])
            self.assertIn("orphan_autopilot_run", issue_types)
            self.assertIn("task_state_mismatch", issue_types)
            self.assertIn("interrupted_session", issue_types)
            self.assertFalse((root / "harness" / "autopilot-runs" / "auto-orphan" / "metadata.json").exists())
            self.assertTrue((root / "harness" / "tasks" / "ready" / "TASK-0099.json").exists())

    def test_recover_apply_repairs_runtime_state_and_writes_ledger_snapshot(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _configured_project(root, unit_command=f"{shlex.quote(sys.executable)} -c 'print(\"unit ok\")'")
            _write_orphan_autopilot_run(root, "auto-orphan")
            _write_mismatched_task(root, "TASK-0099", directory_state="ready", task_state="review")
            stale_worktree = root / "worktrees" / "TASK-0097-run-closed"
            stale_worktree.mkdir(parents=True)
            (stale_worktree / "old.txt").write_text("stale\n", encoding="utf-8")
            _write_closed_worktree_run(root, "run-closed", "TASK-0097", stale_worktree)

            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(["recover", "--apply", "--json"])
            finally:
                cli.ROOT = original_root

            report = json.loads(output.getvalue())
            action_types = {action["type"] for action in report["actions"]}
            orphan_metadata = load_data(root / "harness" / "autopilot-runs" / "auto-orphan" / "metadata.json")
            self.assertEqual(exit_code, 0)
            self.assertTrue(report["applied"])
            self.assertIn("repair_orphan_autopilot_run", action_types)
            self.assertIn("move_task_to_recorded_state", action_types)
            self.assertIn("remove_stale_worktree", action_types)
            self.assertIn("write_ledger_snapshot", action_types)
            self.assertEqual(orphan_metadata["status"], "failed")
            self.assertEqual(orphan_metadata["pause_reason"], "recovered_missing_metadata")
            self.assertFalse((root / "harness" / "tasks" / "ready" / "TASK-0099.json").exists())
            self.assertTrue((root / "harness" / "tasks" / "review" / "TASK-0099.json").exists())
            self.assertFalse(stale_worktree.exists())
            snapshots = sorted((root / "harness" / "snapshots").glob("ledger-snapshot-*.json"))
            self.assertTrue(snapshots)
            snapshot = load_data(snapshots[-1])
            self.assertIn("harness/autopilot-runs/auto-orphan/ledger.jsonl", snapshot["ledgers"])

    def test_recover_apply_can_resume_interrupted_provider_sessions_when_requested(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _configured_project(root, unit_command=f"{shlex.quote(sys.executable)} -c 'print(\"unit ok\")'")
            resume_provider = root / "resume-provider.py"
            resume_provider.write_text(
                """
import json
import sys

payload = json.load(sys.stdin)
assert payload["action"] == "resume"
assert payload["session"]["task_id"] == "TASK-0098"
json.dump(
    {
        "schema_version": 1,
        "status": "resumed",
        "external_session_id": "provider-session-98",
        "summary": "recovered interrupted session",
    },
    sys.stdout,
)
""".lstrip(),
                encoding="utf-8",
            )
            config["sessions"]["resume_command"] = f"{shlex.quote(sys.executable)} {shlex.quote(str(resume_provider))}"
            dump_data(config, root / "harness.yml")
            _write_interrupted_session_run(root, "run-cancelled", "TASK-0098")

            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(["recover", "--apply", "--resume-interrupted", "--json"])
            finally:
                cli.ROOT = original_root

            report = json.loads(output.getvalue())
            action_types = {action["type"] for action in report["actions"]}
            session = load_data(root / "harness" / "runs" / "run-cancelled" / "session.yml")
            metadata = load_data(root / "harness" / "runs" / "run-cancelled" / "metadata.yml")
            ledger = (root / "harness" / "runs" / "run-cancelled" / "ledger.jsonl").read_text(encoding="utf-8")
            self.assertEqual(exit_code, 0)
            self.assertIn("resume_interrupted_session", action_types)
            self.assertEqual(session["status"], "resumed")
            self.assertEqual(metadata["agent_session"]["status"], "resumed")
            self.assertIn('"event": "session_resumed"', ledger)

    def test_source_import_creates_proposed_task_with_source_evidence_and_priority(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _configured_project(root, unit_command=f"{shlex.quote(sys.executable)} -c 'print(\"unit ok\")'")
            source_path = root / "github-issue.json"
            source_path.write_text(
                json.dumps(
                    {
                        "number": 42,
                        "title": "Fix flaky checkout flow",
                        "body": "Checkout fails when the payment callback arrives late.",
                        "url": "https://github.example/acme/shop/issues/42",
                        "labels": ["bug", "priority:high"],
                        "state": "open",
                    }
                ),
                encoding="utf-8",
            )

            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(["source", "import", "--kind", "github-issue", "--from-json", str(source_path), "--json"])
            finally:
                cli.ROOT = original_root

            payload = json.loads(output.getvalue())
            task = load_data(root / "harness" / "tasks" / "proposed" / "TASK-0001.json")
            source_evidence = load_data(root / task["source"]["evidence"])
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["task_id"], "TASK-0001")
            self.assertEqual(payload["kind"], "github_issue")
            self.assertEqual(task["state"], "proposed")
            self.assertEqual(task["priority"], 20)
            self.assertEqual(task["source"]["kind"], "github_issue")
            self.assertEqual(task["source"]["external_id"], "42")
            self.assertEqual(task["source"]["url"], "https://github.example/acme/shop/issues/42")
            self.assertIn("https://github.example/acme/shop/issues/42", task["links"]["issues"])
            self.assertIn("harness/sources/github-issue-42/source.json", payload["evidence"])
            self.assertEqual(source_evidence["raw"]["number"], 42)

    def test_source_import_supports_pr_review_comment_and_ci_failure_sources(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _configured_project(root, unit_command=f"{shlex.quote(sys.executable)} -c 'print(\"unit ok\")'")
            pr_comment = root / "pr-comment.json"
            pr_comment.write_text(
                json.dumps(
                    {
                        "id": "RVC-7",
                        "pr_number": 17,
                        "path": "attestflow/cli.py",
                        "body": "Please add a regression test for this branch.",
                        "url": "https://github.example/acme/shop/pull/17#discussion_r7",
                        "severity": "major",
                    }
                ),
                encoding="utf-8",
            )
            ci_failure = root / "ci-failure.json"
            ci_failure.write_text(
                json.dumps(
                    {
                        "run_id": "build-123",
                        "job": "unit",
                        "summary": "tests/unit/test_checkout.py failed",
                        "log_url": "https://ci.example/build-123/log",
                        "priority": "p0",
                    }
                ),
                encoding="utf-8",
            )

            original_root = cli.ROOT
            cli.ROOT = root
            try:
                with redirect_stdout(io.StringIO()):
                    first_exit = cli.main(["source", "import", "--kind", "pr-review-comment", "--from-json", str(pr_comment)])
                    second_exit = cli.main(["source", "import", "--kind", "ci-failure", "--from-json", str(ci_failure)])
            finally:
                cli.ROOT = original_root

            first = load_data(root / "harness" / "tasks" / "proposed" / "TASK-0001.json")
            second = load_data(root / "harness" / "tasks" / "proposed" / "TASK-0002.json")
            self.assertEqual((first_exit, second_exit), (0, 0))
            self.assertEqual(first["source"]["kind"], "pr_review_comment")
            self.assertEqual(first["source"]["external_id"], "RVC-7")
            self.assertEqual(first["priority"], 20)
            self.assertIn("https://github.example/acme/shop/pull/17#discussion_r7", first["links"]["prs"])
            self.assertEqual(second["source"]["kind"], "ci_failure")
            self.assertEqual(second["source"]["external_id"], "build-123")
            self.assertEqual(second["priority"], 5)
            self.assertIn("https://ci.example/build-123/log", second["links"]["docs"])


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


def _write_done_task_evidence(root: Path, task_id: str) -> None:
    task_dir = root / "harness" / "tasks" / "done"
    run_dir = root / "harness" / "runs" / "run-1"
    ci_dir = root / "harness" / "ci-runs" / "ci-1"
    pr_request_dir = root / "harness" / "pr-runs" / "pr-ensure-1"
    pr_status_dir = root / "harness" / "pr-runs" / "pr-status-1"
    task_dir.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    ci_dir.mkdir(parents=True, exist_ok=True)
    pr_request_dir.mkdir(parents=True, exist_ok=True)
    pr_status_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "evidence.md").write_text("# Evidence\n\n- Run: run-1\n", encoding="utf-8")
    dump_data({"run_id": "run-1", "task_id": task_id, "status": "closed"}, run_dir / "metadata.yml")
    (run_dir / "ledger.jsonl").write_text(
        '{"timestamp":"2026-05-31T00:00:00Z","event":"closed","previous_hash":null,"hash":"legacy"}\n',
        encoding="utf-8",
    )
    dump_data({"schema_version": 1, "status": "passed", "summary": "ci passed", "checks": []}, ci_dir / "output.json")
    dump_data({"schema_version": 1, "status": "open", "summary": "pr open", "checks": []}, pr_request_dir / "output.json")
    dump_data({"schema_version": 1, "status": "merged", "summary": "pr merged", "checks": []}, pr_status_dir / "output.json")
    dump_data(
        {
            "schema_version": 1,
            "id": task_id,
            "title": "Ship feature",
            "state": "done",
            "priority": 10,
            "type": "feature",
            "purpose": "Ship a feature.",
            "context": [],
            "scope": ["feature"],
            "out_of_scope": ["unrelated"],
            "requirements": {"confirmed": ["ship it"], "unresolved": [], "assumptions": []},
            "bdd_scenarios": ["Given feature, when used, then it works."],
            "unit_tests": ["unit passes"],
            "acceptance": ["accepted"],
            "dependencies": [],
            "blocks": [],
            "blockers": [],
            "files": {"read": [], "write": ["feature.py"]},
            "agents": {"owner": "orchestrator", "allowed_roles": ["worker_agent"]},
            "external_inputs": {"credentials": [], "services": [], "user_decisions": []},
            "evidence": {
                "run_id": "run-1",
                "packet": "harness/runs/run-1/evidence.md",
                "verify": "harness/runs/run-1/metadata.yml",
                "ci": "harness/ci-runs/ci-1/output.json",
                "pr_request": "harness/pr-runs/pr-ensure-1/output.json",
                "pr": "harness/pr-runs/pr-status-1/output.json",
            },
            "links": {"issues": [], "prs": [], "docs": []},
            "risks": [],
            "notes": [],
            "created_at": "2026-05-31T00:00:00Z",
            "updated_at": "2026-05-31T00:00:00Z",
        },
        task_dir / f"{task_id}.json",
    )


def _write_autopilot_run(root: Path, run_id: str, task_ids: list[str], *, release_run_id: str | None = None) -> None:
    run_dir = root / "harness" / "autopilot-runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    metadata = {
        "schema_version": 1,
        "run_id": run_id,
        "status": "finished",
        "planned": task_ids,
        "dispatched": task_ids,
        "actions": ["autopilot:release_status"] if release_run_id else ["autopilot:close"],
        "release_status": "released" if release_run_id else "skipped",
    }
    if release_run_id:
        metadata["release"] = {"path": f"harness/release-runs/{release_run_id}/output.json", "status": "released"}
    dump_data(metadata, run_dir / "metadata.json")
    (run_dir / "ledger.jsonl").write_text('{"event":"autopilot_finished"}\n', encoding="utf-8")


def _write_release_run(root: Path, release_run_id: str) -> None:
    release_dir = root / "harness" / "release-runs" / release_run_id
    release_dir.mkdir(parents=True, exist_ok=True)
    dump_data(
        {"schema_version": 1, "status": "released", "summary": "released", "artifacts": []},
        release_dir / "output.json",
    )
    (release_dir / "stdout.log").write_text("released\n", encoding="utf-8")


def _write_inspectable_autopilot_run(root: Path, run_id: str) -> None:
    _write_run_metadata(
        root,
        run_id,
        {
            "status": "paused",
            "pause_reason": "max_steps_reached",
            "steps": 3,
            "actions": ["autopilot:intake", "autopilot:planner"],
            "planned": ["TASK-0001"],
            "dispatched": [],
            "blocked": [],
            "failed": [
                {
                    "task_id": "TASK-0003",
                    "provider_failure": {
                        "type": "rate_limited",
                        "summary": "rate limited",
                        "automatic_action": "retry_later",
                    },
                }
            ],
            "release_status": "pending",
        },
    )
    ledger_path = root / "harness" / "autopilot-runs" / run_id / "ledger.jsonl"
    ledger_path.write_text(
        "\n".join(
            [
                '{"timestamp":"2026-05-31T00:00:00Z","event":"autopilot_started","data":{"step":0}}',
                '{"timestamp":"2026-05-31T00:00:01Z","event":"planner_finished","data":{"tasks":["TASK-0001"]}}',
                '{"timestamp":"2026-05-31T00:00:02Z","event":"autopilot_paused","data":{"reason":"max_steps_reached"}}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _write_run_metadata(root: Path, run_id: str, values: dict) -> None:
    run_dir = root / "harness" / "autopilot-runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    metadata = {
        "schema_version": 1,
        "run_id": run_id,
        "status": "finished",
        "steps": 0,
        "actions": [],
        "planned": [],
        "dispatched": [],
        "failed": [],
        "blocked": [],
        "cancelled": [],
        "release_status": "skipped",
    }
    metadata.update(values)
    dump_data(metadata, run_dir / "metadata.json")
    if not (run_dir / "ledger.jsonl").exists():
        (run_dir / "ledger.jsonl").write_text('{"event":"autopilot_finished"}\n', encoding="utf-8")


def _write_blocked_task(root: Path, task_id: str) -> None:
    task_dir = root / "harness" / "tasks" / "blocked"
    task_dir.mkdir(parents=True, exist_ok=True)
    dump_data(
        {
            "schema_version": 1,
            "id": task_id,
            "title": "Needs credential",
            "state": "blocked",
            "blockers": [
                {
                    "id": "BLOCK-1",
                    "reason": "needs API key",
                    "owner": "user",
                    "status": "active",
                    "unblock_condition": "configure provider credentials",
                }
            ],
        },
        task_dir / f"{task_id}.json",
    )


def _write_provider_failure(root: Path, run_id: str) -> None:
    run_dir = root / "harness" / "capability-runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    dump_data(
        {
            "schema_version": 1,
            "provider": "reviewer",
            "type": "rate_limited",
            "summary": "rate limited",
            "automatic_action": "retry_later",
            "recovery_strategy": ["Retry after cooldown."],
            "retriable": True,
        },
        run_dir / "failure.json",
    )


def _write_orphan_autopilot_run(root: Path, run_id: str) -> None:
    run_dir = root / "harness" / "autopilot-runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "ledger.jsonl").write_text(
        '{"timestamp":"2026-05-31T00:00:00Z","event":"autopilot_started","data":{}}\n',
        encoding="utf-8",
    )


def _write_mismatched_task(root: Path, task_id: str, *, directory_state: str, task_state: str) -> None:
    task_dir = root / "harness" / "tasks" / directory_state
    task_dir.mkdir(parents=True, exist_ok=True)
    dump_data(
        {
            "schema_version": 1,
            "id": task_id,
            "title": "Mismatched task",
            "state": task_state,
            "priority": 1,
            "type": "feature",
        },
        task_dir / f"{task_id}.json",
    )


def _write_interrupted_session_run(root: Path, run_id: str, task_id: str) -> None:
    run_dir = root / "harness" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    dump_data(
        {
            "schema_version": 1,
            "run_id": run_id,
            "task_id": task_id,
            "status": "in_progress",
            "ended_at": None,
        },
        run_dir / "metadata.yml",
    )
    dump_data(
        {
            "schema_version": 1,
            "session_id": f"session-{run_id}",
            "task_id": task_id,
            "run_id": run_id,
            "agent_provider": "command",
            "role": "worker_agent",
            "status": "resume_cancelled",
            "external_session_id": "provider-session-cancelled",
            "workspace_root": str(root),
            "prompt_packet": "prompt.md",
            "resume_command": None,
            "resume_exit_code": -1,
            "resume_stdout_log": "session-resume-adapter.stdout.log",
            "resume_stderr_log": "session-resume-adapter.stderr.log",
            "resume_adapter_input": None,
            "resume_adapter_output": None,
            "failure": "adapter command cancelled",
        },
        run_dir / "session.yml",
    )
    (run_dir / "prompt.md").write_text("# Prompt\n", encoding="utf-8")
    (run_dir / "ledger.jsonl").write_text('{"event":"session_resume_failed"}\n', encoding="utf-8")


def _write_closed_worktree_run(root: Path, run_id: str, task_id: str, worktree: Path) -> None:
    run_dir = root / "harness" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    dump_data(
        {
            "schema_version": 1,
            "run_id": run_id,
            "task_id": task_id,
            "status": "closed",
            "ended_at": "2026-05-31T00:00:00Z",
            "workspace": {
                "worktree": str(worktree),
                "worktree_finalized": True,
                "applied_to_control": True,
            },
        },
        run_dir / "metadata.yml",
    )
    (run_dir / "ledger.jsonl").write_text('{"event":"closed","hash":"abc"}\n', encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
