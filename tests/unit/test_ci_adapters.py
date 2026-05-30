import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from attestflow import ci_adapters


class CiAdapterTests(unittest.TestCase):
    def test_github_actions_adapter_maps_latest_run(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_gh = root / "fake-gh"
            fake_gh.write_text(
                """
#!/usr/bin/env python3
import json
import sys

assert sys.argv[1:4] == ["run", "list", "--limit"]
json.dump(
    [
        {
            "databaseId": 123,
            "status": "completed",
            "conclusion": "success",
            "workflowName": "CI",
            "displayTitle": "main build",
            "headBranch": "main",
            "headSha": "abc123",
            "url": "https://github.example/run/123",
            "createdAt": "2026-05-30T00:00:00Z",
            "updatedAt": "2026-05-30T00:01:00Z",
        }
    ],
    sys.stdout,
)
""".lstrip(),
                encoding="utf-8",
            )
            fake_gh.chmod(0o755)
            payload = {
                "schema_version": 1,
                "provider": "github-actions",
                "root": str(root),
                "provider_options": {"command": str(fake_gh), "repository": "owner/repo"},
            }

            result = ci_adapters.run_github_actions(payload)

            self.assertEqual(result["schema_version"], 1)
            self.assertEqual(result["provider"], "github-actions")
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["external_id"], "123")
            self.assertEqual(result["url"], "https://github.example/run/123")
            self.assertEqual(result["branch"], "main")
            self.assertEqual(result["commit"], "abc123")

    def test_github_actions_adapter_reports_no_runs_as_unknown(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_gh = root / "fake-gh"
            fake_gh.write_text("#!/usr/bin/env python3\nprint('[]')\n", encoding="utf-8")
            fake_gh.chmod(0o755)
            payload = {
                "schema_version": 1,
                "provider": "github-actions",
                "root": str(root),
                "provider_options": {"command": str(fake_gh)},
            }

            result = ci_adapters.run_github_actions(payload)

            self.assertEqual(result["status"], "unknown")
            self.assertEqual(result["summary"], "No GitHub Actions runs found")


if __name__ == "__main__":
    unittest.main()
