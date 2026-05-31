from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import attestflow.cli as cli


class ProviderContractTests(unittest.TestCase):
    def test_provider_contract_suite_runs_fixed_capability_fixtures(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_provider = root / "fake-provider.py"
            fake_provider.write_text(
                """#!/usr/bin/env python3
import json
import sys

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
                "unit_tests": ["tests/unit/test_provider_contracts.py"],
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
elif "Capability: reviewer" in prompt:
    output = {
        "schema_version": 1,
        "status": "passed",
        "summary": "reviewed",
        "findings": [{"severity": "info", "blocking": False, "summary": "No issues."}],
        "evidence": ["review"],
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
elif "Capability: releaser" in prompt:
    output = {
        "schema_version": 1,
        "status": "passed",
        "summary": "release handoff",
        "findings": [],
        "evidence": ["release checklist"],
    }
else:
    raise SystemExit("unknown fixture")

print("provider log before json")
print(json.dumps({"nested": {"contract": output}}))
""".lstrip(),
                encoding="utf-8",
            )
            fake_provider.chmod(0o755)

            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(
                        [
                            "provider",
                            "contract",
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
            self.assertEqual(payload["status"], "passed")
            self.assertEqual([fixture["name"] for fixture in payload["fixtures"]], ["intake", "planner", "task", "reviewer", "verifier", "release"])


if __name__ == "__main__":
    unittest.main()
