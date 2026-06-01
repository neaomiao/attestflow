from __future__ import annotations

from pathlib import Path
import shlex
import sys
from tempfile import TemporaryDirectory
import unittest

from attestflow.ci import run_ci_status
from attestflow.evidence import append_ledger
from attestflow.io import load_data
from attestflow.release import run_release_status
from attestflow.provider_failures import classify_provider_failure, redact_text


class ProviderSafetyTests(unittest.TestCase):
    def test_command_provider_uses_argv_execution_instead_of_shell(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = root / "ci_provider.py"
            marker = root / "shell-was-used.txt"
            provider.write_text(
                """
import json
import sys

json.load(sys.stdin)
json.dump({"schema_version": 1, "status": "passed", "summary": "ci passed", "checks": []}, sys.stdout)
""".lstrip(),
                encoding="utf-8",
            )
            config = {
                "paths": {"ci_runs": "harness/ci-runs"},
                "integrations": {
                    "ci_provider": {
                        "provider": "command",
                        "command": (
                            f"{shlex.quote(sys.executable)} {shlex.quote(str(provider))} ; "
                            f"{shlex.quote(sys.executable)} -c 'from pathlib import Path; Path({str(marker)!r}).write_text(\"bad\", encoding=\"utf-8\")'"
                        ),
                    }
                },
            }

            result = run_ci_status(root, config)

            self.assertEqual(result.status, "passed")
            self.assertFalse(marker.exists())

    def test_provider_usage_is_written_as_run_evidence(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = root / "ci_provider.py"
            provider.write_text(
                """
import json
import sys

json.load(sys.stdin)
json.dump(
    {
        "schema_version": 1,
        "status": "passed",
        "summary": "ci passed",
        "checks": [],
        "usage": {
            "provider": "codex",
            "model": "gpt-5",
            "input_tokens": 20,
            "output_tokens": 5,
            "total_tokens": 25,
        },
    },
    sys.stdout,
)
""".lstrip(),
                encoding="utf-8",
            )
            config = {
                "paths": {"ci_runs": "harness/ci-runs"},
                "integrations": {"ci_provider": {"provider": "command", "command": f"{sys.executable} {provider}"}},
            }

            result = run_ci_status(root, config)

            usage = load_data(result.run_path / "usage.json")
            self.assertEqual(result.status, "passed")
            self.assertEqual(usage["input_tokens"], 20)
            self.assertEqual(usage["output_tokens"], 5)
            self.assertEqual(usage["total_tokens"], 25)

    def test_provider_failure_taxonomy_and_redacted_logs_are_written(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = root / "ci_provider.py"
            provider.write_text(
                """
import sys

sys.stderr.write("API_TOKEN=super-secret-token\\n")
print("not json")
""".lstrip(),
                encoding="utf-8",
            )
            config = {
                "paths": {"ci_runs": "harness/ci-runs"},
                "integrations": {
                    "ci_provider": {
                        "provider": "command",
                        "command": f"{shlex.quote(sys.executable)} {shlex.quote(str(provider))}",
                    }
                },
            }

            with self.assertRaisesRegex(ValueError, "invalid_output"):
                run_ci_status(root, config)

            run_dir = sorted((root / "harness" / "ci-runs").glob("ci-*"))[-1]
            failure = load_data(run_dir / "failure.json")
            self.assertEqual(failure["type"], "invalid_output")
            self.assertEqual(failure["automatic_action"], "fix_provider_output")
            stderr = (run_dir / "stderr.log").read_text(encoding="utf-8")
            self.assertNotIn("super-secret-token", stderr)
            self.assertIn("API_TOKEN=<redacted>", stderr)

    def test_redaction_preserves_bearer_prefix_without_leaking_token(self) -> None:
        redacted = redact_text("Authorization: Bearer abc.def.secret-token")

        self.assertEqual(redacted, "Authorization: Bearer <redacted>")
        self.assertNotIn("secret-token", redacted)

    def test_provider_failure_classifier_covers_contract_taxonomy(self) -> None:
        cases = [
            ("auth_missing", {"stderr": "Authentication required. Missing API key."}),
            ("rate_limited", {"stderr": "HTTP 429 too many requests"}),
            ("context_too_large", {"stderr": "context length exceeded token limit"}),
            ("tool_denied", {"stderr": "tool denied by policy"}),
            ("tool_denied", {"stderr": "Operation not permitted while opening state database"}),
            ("timeout", {"reason": "timeout"}),
            ("network", {"stderr": "Could not resolve host api.github.com"}),
            ("invalid_output", {"reason": "invalid_output"}),
        ]

        for expected, kwargs in cases:
            with self.subTest(expected=expected):
                failure = classify_provider_failure("ci", **kwargs)
                self.assertEqual(failure["type"], expected)
                self.assertTrue(failure["automatic_action"])

    def test_provider_command_allowlist_blocks_unapproved_executable_before_running(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "provider-ran.txt"
            provider = root / "ci_provider.py"
            provider.write_text(
                f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad', encoding='utf-8')\n",
                encoding="utf-8",
            )
            config = {
                "paths": {"ci_runs": "harness/ci-runs"},
                "security": {"provider_commands": {"allowlist": ["definitely-not-python"]}},
                "integrations": {
                    "ci_provider": {
                        "provider": "command",
                        "command": f"{shlex.quote(sys.executable)} {shlex.quote(str(provider))}",
                    }
                },
            }

            with self.assertRaisesRegex(ValueError, "not allowed"):
                run_ci_status(root, config)

            self.assertFalse(marker.exists())
            run_dir = sorted((root / "harness" / "ci-runs").glob("ci-*"))[-1]
            failure = load_data(run_dir / "failure.json")
            self.assertEqual(failure["type"], "tool_denied")

    def test_provider_output_size_limit_fails_closed_and_truncates_logs(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = root / "ci_provider.py"
            provider.write_text("print('x' * 2000)\n", encoding="utf-8")
            config = {
                "paths": {"ci_runs": "harness/ci-runs"},
                "security": {"provider_commands": {"max_output_bytes": 64}},
                "integrations": {
                    "ci_provider": {
                        "provider": "command",
                        "command": f"{shlex.quote(sys.executable)} {shlex.quote(str(provider))}",
                    }
                },
            }

            with self.assertRaisesRegex(ValueError, "output_too_large"):
                run_ci_status(root, config)

            run_dir = sorted((root / "harness" / "ci-runs").glob("ci-*"))[-1]
            failure = load_data(run_dir / "failure.json")
            self.assertEqual(failure["type"], "output_too_large")
            stdout = (run_dir / "stdout.log").read_text(encoding="utf-8")
            self.assertLess(len(stdout), 300)
            self.assertIn("<truncated>", stdout)

    def test_irreversible_provider_action_requires_approval_file(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "release-ran.txt"
            provider = root / "release_provider.py"
            provider.write_text(
                f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad', encoding='utf-8')\n",
                encoding="utf-8",
            )
            config = {
                "paths": {"release_runs": "harness/release-runs"},
                "integrations": {
                    "release_provider": {
                        "provider": "command",
                        "command": f"{shlex.quote(sys.executable)} {shlex.quote(str(provider))}",
                        "provider_options": {"irreversible": True, "approval_id": "REL-1"},
                    }
                },
            }

            with self.assertRaisesRegex(ValueError, "approval required"):
                run_release_status(root, config, done_tasks=[])

            self.assertFalse(marker.exists())
            run_dir = sorted((root / "harness" / "release-runs").glob("release-*"))[-1]
            failure = load_data(run_dir / "failure.json")
            self.assertEqual(failure["type"], "approval_required")

    def test_irreversible_provider_action_runs_with_approval_file(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            approvals = root / "harness" / "approvals"
            approvals.mkdir(parents=True)
            (approvals / "REL-1.json").write_text('{"approved": true, "reason": "test"}\n', encoding="utf-8")
            provider = root / "release_provider.py"
            provider.write_text(
                """
import json
import sys

json.load(sys.stdin)
json.dump({"schema_version": 1, "provider": "local-release", "status": "released", "summary": "approved"}, sys.stdout)
""".lstrip(),
                encoding="utf-8",
            )
            config = {
                "paths": {"release_runs": "harness/release-runs"},
                "integrations": {
                    "release_provider": {
                        "provider": "command",
                        "command": f"{shlex.quote(sys.executable)} {shlex.quote(str(provider))}",
                        "provider_options": {"irreversible": True, "approval_id": "REL-1"},
                    }
                },
            }

            result = run_release_status(root, config, done_tasks=[])

            self.assertEqual(result.status, "released")
            security = load_data(result.run_path / "input.json")["security"]
            self.assertTrue(security["approval"]["approved"])

    def test_ledger_entries_are_hash_chained(self) -> None:
        with TemporaryDirectory() as tmp:
            run_path = Path(tmp)

            append_ledger(run_path, "first", "TASK-1", "run-1", "tester", {"n": 1})
            append_ledger(run_path, "second", "TASK-1", "run-1", "tester", {"n": 2})

            lines = [load_data_line for load_data_line in _read_jsonl(run_path / "ledger.jsonl")]
            self.assertIsNone(lines[0]["previous_hash"])
            self.assertEqual(lines[1]["previous_hash"], lines[0]["hash"])
            self.assertNotEqual(lines[0]["hash"], lines[1]["hash"])


def _read_jsonl(path: Path) -> list[dict]:
    import json

    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


if __name__ == "__main__":
    unittest.main()
