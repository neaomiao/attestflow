from __future__ import annotations

from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import shlex
import sys
from tempfile import TemporaryDirectory
import time
import unittest

import attestflow.cli as cli
from attestflow.autonomy import autonomy_doctor
from attestflow.capabilities import run_task_capability
from attestflow.ci import run_ci_status
from attestflow.config import DEFAULT_CONFIG, load_config
from attestflow.io import dump_data, load_data
from attestflow.recovery import recover_runtime


class OssCoreHardeningTests(unittest.TestCase):
    def test_recover_repairs_missing_runtime_directories_and_autonomy_checks_git_runs(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _configured_project(root)
            _create_runtime_layout(root, skip={"git-runs", "plugin-runs"})

            before = autonomy_doctor(root, load_config(root))
            runtime_before = _check(before, "runtime_layout")
            self.assertEqual(runtime_before["status"], "blocked")
            self.assertIn("git-runs", runtime_before["summary"])
            self.assertIn("plugin-runs", runtime_before["summary"])

            report = recover_runtime(root, load_config(root), apply=True)

            actions = {action["path"] for action in report["actions"] if action["type"] == "create_runtime_directory"}
            self.assertIn("harness/git-runs", actions)
            self.assertIn("harness/plugin-runs", actions)
            after = autonomy_doctor(root, load_config(root))
            self.assertEqual(_check(after, "runtime_layout")["status"], "passed")

    def test_provider_restricted_env_sandbox_removes_unapproved_env_and_marks_network_disabled(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = root / "ci_provider.py"
            provider.write_text(
                """
import json
import os
import sys

json.load(sys.stdin)
json.dump(
    {
        "schema_version": 1,
        "status": "passed",
        "summary": "ci passed",
        "checks": [
            {"name": "allowed", "status": os.environ.get("ALLOWED_ENV", "")},
            {"name": "secret", "status": os.environ.get("SECRET_TOKEN", "")},
            {"name": "network", "status": os.environ.get("ATTESTFLOW_NETWORK", "")},
        ],
    },
    sys.stdout,
)
""".lstrip(),
                encoding="utf-8",
            )
            config = {
                "paths": {"ci_runs": "harness/ci-runs"},
                "security": {
                    "provider_commands": {
                        "sandbox": {
                            "mode": "restricted-env",
                            "allowed_env": ["ALLOWED_ENV"],
                            "blocked_env": ["SECRET_TOKEN"],
                            "network": "disabled",
                        }
                    }
                },
                "integrations": {
                    "ci_provider": {
                        "provider": "command",
                        "command": f"{shlex.quote(sys.executable)} {shlex.quote(str(provider))}",
                    }
                },
            }
            old_allowed = os.environ.get("ALLOWED_ENV")
            old_secret = os.environ.get("SECRET_TOKEN")
            os.environ["ALLOWED_ENV"] = "kept"
            os.environ["SECRET_TOKEN"] = "must-not-leak"
            try:
                result = run_ci_status(root, config)
            finally:
                _restore_env("ALLOWED_ENV", old_allowed)
                _restore_env("SECRET_TOKEN", old_secret)

            checks = {item["name"]: item["status"] for item in result.output["checks"]}
            self.assertEqual(checks["allowed"], "kept")
            self.assertEqual(checks["secret"], "")
            self.assertEqual(checks["network"], "disabled")
            security = load_data(result.run_path / "input.json")["security"]
            self.assertEqual(security["provider_commands"]["sandbox"]["mode"], "restricted-env")

    def test_capability_auto_resolves_dynamic_context_requests_and_retries_once(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _configured_project(root)
            config["capabilities"]["reviewer"]["command"] = f"{shlex.quote(sys.executable)} {shlex.quote(str(root / 'reviewer.py'))}"
            dump_data(config, root / "harness.yml")
            _create_runtime_layout(root)
            _write_ready_task(root, "TASK-0001")
            (root / "README.md").write_text("needle context line\nsecond line\n", encoding="utf-8")
            attempts = root / "attempts.txt"
            (root / "reviewer.py").write_text(
                f"""
import json
from pathlib import Path
import sys

payload = json.load(sys.stdin)
attempts = Path({str(attempts)!r})
count = int(attempts.read_text(encoding="utf-8")) if attempts.exists() else 0
attempts.write_text(str(count + 1), encoding="utf-8")
if count == 0:
    json.dump({{
        "schema_version": 1,
        "status": "blocked",
        "summary": "need context",
        "findings": [],
        "evidence": [],
        "artifacts": {{
            "context_requests": [
                {{"request_id": "readme", "type": "file_slice", "path": "README.md", "start_line": 1, "end_line": 1}}
            ]
        }},
    }}, sys.stdout)
else:
    responses = payload["resolved_dynamic_context"]["responses"]
    assert responses[0]["items"][0]["content"] == "needle context line\\n"
    json.dump({{
        "schema_version": 1,
        "status": "passed",
        "summary": "review passed with context",
        "findings": [],
        "evidence": ["dynamic context resolved"],
    }}, sys.stdout)
""".lstrip(),
                encoding="utf-8",
            )

            result = run_task_capability(root, load_config(root), "reviewer", "TASK-0001")

            self.assertEqual(result.output["status"], "passed")
            self.assertEqual(attempts.read_text(encoding="utf-8"), "2")
            dynamic_context = load_data(result.run_path / "dynamic-context.json")
            self.assertEqual(dynamic_context["responses"][0]["request_id"], "readme")
            self.assertTrue((result.run_path / "output.context-request.json").exists())

    def test_plugin_run_executes_manifest_command_with_evidence(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _configured_project(root)
            _create_runtime_layout(root)
            plugin_dir = root / "harness" / "plugins" / "demo"
            plugin_dir.mkdir(parents=True)
            provider = plugin_dir / "plugin_provider.py"
            provider.write_text(
                """
import json
import sys

payload = json.load(sys.stdin)
json.dump(
    {
        "schema_version": 1,
        "status": "passed",
        "summary": payload["input"]["message"].upper(),
        "plugin": payload["plugin"]["name"],
    },
    sys.stdout,
)
""".lstrip(),
                encoding="utf-8",
            )
            dump_data(
                {
                    "schema_version": 1,
                    "name": "demo-plugin",
                    "version": "0.1.0",
                    "capabilities": [],
                    "providers": {},
                    "adapters": [],
                    "commands": {"echo": f"{shlex.quote(sys.executable)} {shlex.quote(str(provider))}"},
                },
                plugin_dir / "plugin.json",
            )
            input_path = root / "plugin-input.json"
            dump_data({"message": "hello"}, input_path)

            output = _run_cli(root, ["plugin", "run", "demo-plugin", "echo", "--from-json", str(input_path), "--json"])

            payload = json.loads(output)
            self.assertEqual(payload["output"]["summary"], "HELLO")
            self.assertTrue((root / payload["run_path"] / "output.json").exists())

    def test_evidence_maintain_can_gc_redact_and_compact_local_evidence(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _configured_project(root)
            old_run = root / "harness" / "runs" / "old-run"
            recent_run = root / "harness" / "runs" / "recent-run"
            old_run.mkdir(parents=True)
            recent_run.mkdir(parents=True)
            (old_run / "metadata.yml").write_text("status: closed\n", encoding="utf-8")
            (recent_run / "stderr.log").write_text("Authorization: Bearer secret-token\n", encoding="utf-8")
            (recent_run / "stdout.log").write_text("x" * 200 + "\n", encoding="utf-8")
            old_time = time.time() - 90 * 24 * 60 * 60
            os.utime(old_run, (old_time, old_time))
            os.utime(old_run / "metadata.yml", (old_time, old_time))

            output = _run_cli(
                root,
                [
                    "evidence",
                    "maintain",
                    "--retention-days",
                    "30",
                    "--redact",
                    "--compact",
                    "--max-file-bytes",
                    "80",
                    "--apply",
                    "--json",
                ],
            )

            report = json.loads(output)
            action_types = {action["type"] for action in report["actions"]}
            self.assertIn("gc_run", action_types)
            self.assertIn("redact_file", action_types)
            self.assertIn("compact_file", action_types)
            self.assertFalse(old_run.exists())
            self.assertEqual((recent_run / "stderr.log").read_text(encoding="utf-8"), "Authorization: Bearer <redacted>\n")
            self.assertIn("<attestflow evidence compacted>", (recent_run / "stdout.log").read_text(encoding="utf-8"))

    def test_policy_packs_are_listed_validated_and_merged_without_saas(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _configured_project(root)
            policy_dir = root / "harness" / "policies"
            policy_dir.mkdir(parents=True)
            dump_data(
                {
                    "schema_version": 1,
                    "name": "strict-local",
                    "version": "0.1.0",
                    "description": "Strict local gates.",
                    "config": {
                        "policies": {"require_fresh_verify_for_done": True},
                        "security": {"provider_commands": {"sandbox": {"mode": "restricted-env"}}},
                    },
                },
                policy_dir / "strict-local.json",
            )
            merged_path = root / "merged-harness.yml"

            listed = json.loads(_run_cli(root, ["policy", "list", "--json"]))
            validated = json.loads(_run_cli(root, ["policy", "validate", "strict-local", "--json"]))
            applied = json.loads(_run_cli(root, ["policy", "apply", "strict-local", "--out", str(merged_path), "--json"]))

            self.assertEqual(listed["packs"][0]["name"], "strict-local")
            self.assertEqual(validated["status"], "passed")
            self.assertEqual(applied["status"], "passed")
            merged = load_data(merged_path)
            self.assertTrue(merged["policies"]["require_fresh_verify_for_done"])
            self.assertEqual(merged["security"]["provider_commands"]["sandbox"]["mode"], "restricted-env")

    def test_dashboard_export_writes_static_html_and_data_snapshot(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _configured_project(root)
            _create_runtime_layout(root)
            _write_ready_task(root, "TASK-0001")
            out_dir = root / "dashboard"

            output = _run_cli(root, ["dashboard", "export", "--out", str(out_dir), "--json"])

            payload = json.loads(output)
            self.assertEqual(payload["status"], "passed")
            self.assertTrue((out_dir / "index.html").exists())
            self.assertTrue((out_dir / "data.json").exists())
            html = (out_dir / "index.html").read_text(encoding="utf-8")
            data = load_data(out_dir / "data.json")
            self.assertIn("Attestflow Local Dashboard", html)
            self.assertEqual(data["tasks"]["ready"], 1)
            self.assertEqual(data["total_tasks"], 1)

    def test_release_trust_generates_sbom_provenance_and_checklist(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _configured_project(root)
            (root / "pyproject.toml").write_text(
                """
[project]
name = "demo"
version = "0.1.0"
dependencies = ["click>=8"]
""".lstrip(),
                encoding="utf-8",
            )
            workflow_dir = root / ".github" / "workflows"
            workflow_dir.mkdir(parents=True)
            (workflow_dir / "ci.yml").write_text(
                """
jobs:
  verify:
    strategy:
      matrix:
        python-version: ["3.11", "3.12", "3.13"]
    steps:
      - run: python -m build
      - run: attestflow install-smoke --offline
      - uses: actions/upload-artifact@v4
""".lstrip(),
                encoding="utf-8",
            )
            out_dir = root / "release-trust"

            output = _run_cli(root, ["release", "trust", "--out", str(out_dir), "--json"])

            report = json.loads(output)
            self.assertEqual(report["status"], "passed")
            self.assertTrue((out_dir / "sbom.json").exists())
            self.assertTrue((out_dir / "provenance.json").exists())
            self.assertTrue((out_dir / "checklist.md").exists())
            self.assertTrue((out_dir / "manifest.json").exists())
            sbom = load_data(out_dir / "sbom.json")
            manifest = load_data(out_dir / "manifest.json")
            self.assertEqual(sbom["packages"][0]["name"], "demo")
            self.assertEqual(sbom["packages"][1]["name"], "click")
            self.assertIn("sbom.json", {artifact["path"] for artifact in manifest["artifacts"]})

    def test_all_builtin_adapters_initialize_runtime_fixture_without_external_commands(self) -> None:
        for adapter in cli.BUILTIN_PROJECT_ADAPTERS:
            with self.subTest(adapter=adapter), TemporaryDirectory() as tmp:
                root = Path(tmp)

                exit_code = cli.cmd_init(
                    type(
                        "Args",
                        (),
                        {"path": str(root), "adapter": adapter, "agent_provider": "command", "agent_command": None},
                    )()
                )

                config = load_config(root)
                errors = cli._doctor_runtime_layout_errors(root, config)
                self.assertEqual(exit_code, 0)
                self.assertEqual(errors, [])
                self.assertTrue((root / "harness" / "adapters" / adapter / "README.md").exists())


def _configured_project(root: Path) -> dict:
    config = deepcopy(DEFAULT_CONFIG)
    for name in config["commands"]:
        config["commands"][name] = None
    dump_data(config, root / "harness.yml")
    return load_config(root)


def _create_runtime_layout(root: Path, *, skip: set[str] | None = None) -> None:
    skip = skip or set()
    for state in ("proposed", "needs_clarification", "ready", "in_progress", "blocked", "review", "verified", "accepted", "done", "archived"):
        (root / "harness" / "tasks" / state).mkdir(parents=True, exist_ok=True)
    for name in (
        "runs",
        "locks",
        "capability-runs",
        "autopilot-runs",
        "ci-runs",
        "git-runs",
        "pr-runs",
        "release-runs",
        "plugin-runs",
    ):
        if name not in skip:
            (root / "harness" / name).mkdir(parents=True, exist_ok=True)


def _write_ready_task(root: Path, task_id: str) -> None:
    dump_data(
        {
            "schema_version": 1,
            "id": task_id,
            "title": "Harden OSS core",
            "state": "ready",
            "priority": 10,
            "type": "feature",
            "purpose": "Harden the OSS harness core.",
            "context": [],
            "scope": ["local harness"],
            "out_of_scope": ["SaaS"],
            "requirements": {"confirmed": ["local automation"], "unresolved": [], "assumptions": []},
            "bdd_scenarios": ["Given a harness, when checked, then it reports status."],
            "unit_tests": ["tests/unit/test_oss_core_hardening.py"],
            "acceptance": ["hardening behavior is verified"],
            "dependencies": [],
            "blocks": [],
            "blockers": [],
            "files": {"read": ["README.md"], "write": ["attestflow/capabilities.py"]},
            "agents": {"owner": "orchestrator", "allowed_roles": ["worker_agent"]},
            "external_inputs": {"credentials": [], "services": [], "user_decisions": []},
            "evidence": {"session": None, "run_id": None, "red": None, "green": None, "verify": None, "packet": None},
            "links": {"issues": [], "prs": [], "docs": []},
            "risks": [],
            "notes": [],
            "created_at": "2026-06-01T00:00:00Z",
            "updated_at": "2026-06-01T00:00:00Z",
        },
        root / "harness" / "tasks" / "ready" / f"{task_id}.json",
    )


def _run_cli(root: Path, argv: list[str]) -> str:
    original_root = cli.ROOT
    cli.ROOT = root
    try:
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = cli.main(argv)
    finally:
        cli.ROOT = original_root
    if exit_code != 0:
        self_message = f"CLI failed with exit code {exit_code}: {' '.join(argv)}"
        raise AssertionError(self_message)
    return output.getvalue()


def _check(report: dict, name: str) -> dict:
    for check in report["checks"]:
        if check["name"] == name:
            return check
    raise AssertionError(f"missing check: {name}")


def _restore_env(key: str, value: str | None) -> None:
    if value is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
