from __future__ import annotations

from pathlib import Path
import shlex
import sys
from tempfile import TemporaryDirectory
import unittest

from attestflow.ci import run_ci_status
from attestflow.io import load_data
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
        cases = {
            "auth_missing": {"stderr": "Authentication required. Missing API key."},
            "rate_limited": {"stderr": "HTTP 429 too many requests"},
            "context_too_large": {"stderr": "context length exceeded token limit"},
            "tool_denied": {"stderr": "tool denied by policy"},
            "timeout": {"reason": "timeout"},
            "network": {"stderr": "Could not resolve host api.github.com"},
            "invalid_output": {"reason": "invalid_output"},
        }

        for expected, kwargs in cases.items():
            with self.subTest(expected=expected):
                failure = classify_provider_failure("ci", **kwargs)
                self.assertEqual(failure["type"], expected)
                self.assertTrue(failure["automatic_action"])


if __name__ == "__main__":
    unittest.main()
