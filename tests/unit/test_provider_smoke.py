from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import attestflow.cli as cli
from attestflow.provider_smoke import run_provider_readiness_suite


class ProviderSmokeTests(unittest.TestCase):
    def test_provider_readiness_suite_reports_version_smoke_and_contract(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_provider = root / "fake-codex.py"
            fake_provider.write_text(_passing_provider_script(), encoding="utf-8")
            fake_provider.chmod(0o755)

            result = run_provider_readiness_suite(root, "codex", command=str(fake_provider))

            self.assertEqual(result["schema_version"], 1)
            self.assertEqual(result["provider"], "codex")
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["version"]["status"], "passed")
            self.assertIn("fake-codex 1.2.3", result["version"]["output"])
            self.assertEqual(result["smoke"]["status"], "passed")
            self.assertEqual(result["contract"]["status"], "passed")

    def test_provider_readiness_suite_supports_all_builtin_providers(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_provider = root / "fake-provider.py"
            fake_provider.write_text(_passing_provider_script(), encoding="utf-8")
            fake_provider.chmod(0o755)

            for provider in ("codex", "claude-code", "opencode"):
                with self.subTest(provider=provider):
                    result = run_provider_readiness_suite(root, provider, command=str(fake_provider), skip_contract=True)
                    self.assertEqual(result["status"], "passed")
                    self.assertEqual(result["version"]["status"], "passed")
                    self.assertEqual(result["smoke"]["status"], "passed")

    def test_provider_readiness_suite_classifies_auth_and_recovery_strategy(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_provider = root / "auth-missing-codex.py"
            fake_provider.write_text(
                """#!/usr/bin/env python3
import sys

if "--version" in sys.argv:
    print("fake-codex 1.2.3")
    raise SystemExit(0)
sys.stderr.write("not logged in: authentication required\\n")
raise SystemExit(1)
""",
                encoding="utf-8",
            )
            fake_provider.chmod(0o755)

            result = run_provider_readiness_suite(root, "codex", command=str(fake_provider), skip_contract=True)

            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["smoke"]["failure"]["type"], "auth_missing")
            self.assertEqual(result["smoke"]["failure"]["automatic_action"], "block_for_credentials")
            self.assertTrue(result["smoke"]["failure"]["recovery_strategy"])

    def test_cli_provider_smoke_outputs_readiness_json(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_provider = root / "fake-codex.py"
            fake_provider.write_text(_passing_provider_script(), encoding="utf-8")
            fake_provider.chmod(0o755)
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(
                        [
                            "provider",
                            "smoke",
                            "--provider",
                            "codex",
                            "--command",
                            str(fake_provider),
                            "--json",
                        ]
                    )
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["provider"], "codex")
            self.assertEqual(payload["status"], "passed")
            self.assertEqual(payload["smoke"]["status"], "passed")


def _passing_provider_script() -> str:
    return """#!/usr/bin/env python3
import json
import sys

if "--version" in sys.argv:
    print("fake-codex 1.2.3")
    raise SystemExit(0)

prompt = sys.argv[-1]

if "Capability: intake" in prompt:
    output = {
        "schema_version": 1,
        "status": "passed",
        "summary": "intake complete",
        "findings": [],
        "evidence": ["intake"],
        "artifacts": {"confirmed": ["fixture runs"], "decision_blockers": []},
    }
elif "Capability: planner" in prompt:
    output = {
        "schema_version": 1,
        "tasks": [
            {
                "title": "Contract task",
                "purpose": "Exercise planner fixture.",
                "scope": ["contract"],
                "out_of_scope": ["real provider calls"],
                "requirements": {"confirmed": ["fixture runs"], "unresolved": [], "assumptions": []},
                "bdd_scenarios": ["Given fixture, when planned, then task exists."],
                "unit_tests": ["tests/unit/test_provider_smoke.py"],
                "acceptance": ["fixture passes"],
                "files": {"read": [], "write": ["src/provider_contract.py"]},
            }
        ],
    }
elif "Capability: implementer" in prompt:
    output = {
        "schema_version": 1,
        "status": "passed",
        "summary": "implemented",
        "findings": [],
        "evidence": ["diff"],
        "artifacts": {
            "diff_summary": "Changed fixture file.",
            "written_files": ["src/provider_contract.py"],
            "incomplete": [],
            "risks": [],
            "command_results": [],
        },
    }
elif "Capability: verifier" in prompt:
    output = {
        "schema_version": 1,
        "status": "passed",
        "summary": "verified",
        "findings": [],
        "evidence": ["verify"],
        "artifacts": {
            "commands": [{"name": "unit", "command": "python -m unittest", "status": "passed"}],
            "environment": {"provider": "fixture"},
            "duration_seconds": 0,
            "flake": {"detected": False},
            "evidence": ["unit.log"],
        },
    }
elif "Capability: reviewer" in prompt or "Capability: releaser" in prompt:
    output = {
        "schema_version": 1,
        "status": "passed",
        "summary": "checked",
        "findings": [],
        "evidence": ["check"],
    }
else:
    print(json.dumps({"type": "thread.started", "thread_id": "codex-live-smoke-123"}))
    print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}}))
    raise SystemExit(0)

print(json.dumps({"nested": {"contract": output}}))
"""


if __name__ == "__main__":
    unittest.main()
