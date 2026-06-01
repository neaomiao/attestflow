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

    def test_github_actions_status_filters_run_and_collects_failure_details(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_gh = root / "fake-gh"
            calls = root / "calls.jsonl"
            fake_gh.write_text(
                f"""
#!/usr/bin/env python3
import json
import sys
from pathlib import Path

calls = Path({str(calls)!r})
args = sys.argv[1:]
with calls.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(args) + "\\n")

if args[:2] == ["run", "list"]:
    assert "--repo" in args and args[args.index("--repo") + 1] == "owner/repo"
    assert "--branch" in args and args[args.index("--branch") + 1] == "feature/actions"
    assert "--commit" in args and args[args.index("--commit") + 1] == "abc123"
    assert "--workflow" in args and args[args.index("--workflow") + 1] == "ci.yml"
    assert "--event" in args and args[args.index("--event") + 1] == "pull_request"
    assert "--status" in args and args[args.index("--status") + 1] == "completed"
    json.dump(
        [
            {{
                "databaseId": 456,
                "status": "completed",
                "conclusion": "failure",
                "workflowName": "CI",
                "displayTitle": "feature build",
                "headBranch": "feature/actions",
                "headSha": "abc123",
                "url": "https://github.example/run/456",
                "event": "pull_request",
                "createdAt": "2026-06-01T00:00:00Z",
                "updatedAt": "2026-06-01T00:04:00Z",
            }}
        ],
        sys.stdout,
    )
elif args[:3] == ["run", "view", "456"] and "--json" in args:
    json.dump(
        {{
            "databaseId": 456,
            "status": "completed",
            "conclusion": "failure",
            "workflowName": "CI",
            "displayTitle": "feature build",
            "headBranch": "feature/actions",
            "headSha": "abc123",
            "url": "https://github.example/run/456",
            "jobs": [
                {{
                    "name": "unit",
                    "status": "completed",
                    "conclusion": "failure",
                    "url": "https://github.example/run/456/job/1",
                    "steps": [{{"name": "test", "conclusion": "failure"}}],
                    "annotations": [
                        {{"path": "tests/unit/test_checkout.py", "message": "assertion failed", "level": "failure"}}
                    ],
                }}
            ],
        }},
        sys.stdout,
    )
elif args[:3] == ["run", "view", "456"] and "--log-failed" in args:
    sys.stdout.write("unit failure line\\n")
else:
    raise SystemExit(f"unexpected args: {{args}}")
""".lstrip(),
                encoding="utf-8",
            )
            fake_gh.chmod(0o755)
            payload = {
                "schema_version": 1,
                "provider": "github-actions",
                "action": "status",
                "root": str(root),
                "provider_options": {
                    "command": str(fake_gh),
                    "repository": "owner/repo",
                    "branch": "feature/actions",
                    "head_sha": "abc123",
                    "workflow": "ci.yml",
                    "event": "pull_request",
                    "status_filter": "completed",
                },
            }

            result = ci_adapters.run_github_actions(payload)

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["external_id"], "456")
            self.assertEqual(result["failure_summary"]["failed_jobs"], ["unit"])
            self.assertIn("unit failure line", result["logs"]["failed"])
            self.assertEqual(result["annotations"][0]["path"], "tests/unit/test_checkout.py")
            recorded_calls = [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(recorded_calls[0][:2], ["run", "list"])
            self.assertEqual(recorded_calls[1][:3], ["run", "view", "456"])
            self.assertEqual(recorded_calls[2][:3], ["run", "view", "456"])

    def test_github_actions_await_polls_until_terminal_run(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            counter = root / "counter.txt"
            fake_gh = root / "fake-gh"
            fake_gh.write_text(
                f"""
#!/usr/bin/env python3
import json
import sys
from pathlib import Path

counter = Path({str(counter)!r})
count = int(counter.read_text(encoding="utf-8")) if counter.exists() else 0
counter.write_text(str(count + 1), encoding="utf-8")
conclusion = None if count == 0 else "success"
status = "in_progress" if count == 0 else "completed"
json.dump(
    [
        {{
            "databaseId": 789,
            "status": status,
            "conclusion": conclusion,
            "workflowName": "CI",
            "displayTitle": "feature build",
            "headBranch": "feature/actions",
            "headSha": "abc123",
            "url": "https://github.example/run/789",
        }}
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
                "action": "await",
                "root": str(root),
                "provider_options": {
                    "command": str(fake_gh),
                    "head_sha": "abc123",
                    "max_wait_seconds": 1,
                    "poll_interval_seconds": 0,
                },
            }

            result = ci_adapters.run_github_actions(payload)

            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["external_id"], "789")
            self.assertEqual(result["polls"], 2)

    def test_github_actions_logs_action_reads_specific_run(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_gh = root / "fake-gh"
            fake_gh.write_text(
                """
#!/usr/bin/env python3
import json
import sys

args = sys.argv[1:]
if args[:3] == ["run", "view", "456"] and "--json" in args:
    json.dump(
        {
            "databaseId": 456,
            "status": "completed",
            "conclusion": "failure",
            "workflowName": "CI",
            "headSha": "abc123",
            "jobs": [{"name": "unit", "conclusion": "failure"}],
        },
        sys.stdout,
    )
elif args[:3] == ["run", "view", "456"] and "--log-failed" in args:
    sys.stdout.write("unit failure line\\n")
else:
    raise SystemExit(f"unexpected args: {args}")
""".lstrip(),
                encoding="utf-8",
            )
            fake_gh.chmod(0o755)
            payload = {
                "schema_version": 1,
                "provider": "github-actions",
                "action": "logs",
                "root": str(root),
                "provider_options": {"command": str(fake_gh), "run_id": "456"},
            }

            result = ci_adapters.run_github_actions(payload)

            self.assertEqual(result["action"], "logs")
            self.assertEqual(result["status"], "failed")
            self.assertIn("unit failure line", result["logs"]["failed"])
            self.assertEqual(result["jobs"][0]["name"], "unit")

    def test_github_actions_artifacts_rerun_and_dispatch_actions(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls = root / "calls.jsonl"
            download_dir = root / "artifacts"
            fake_gh = root / "fake-gh"
            fake_gh.write_text(
                f"""
#!/usr/bin/env python3
import json
import sys
from pathlib import Path

calls = Path({str(calls)!r})
args = sys.argv[1:]
with calls.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(args) + "\\n")

if args[:2] == ["api", "repos/owner/repo/actions/runs/456/artifacts"]:
    json.dump(
        {{
            "artifacts": [
                {{"id": 1, "name": "attestflow-evidence", "archive_download_url": "https://github.example/artifact/1"}}
            ]
        }},
        sys.stdout,
    )
elif args[:3] == ["run", "download", "456"]:
    Path(args[args.index("--dir") + 1]).mkdir(parents=True, exist_ok=True)
elif args[:3] == ["run", "rerun", "456"]:
    pass
elif args[:3] == ["workflow", "run", "ci.yml"]:
    assert "--ref" in args and args[args.index("--ref") + 1] == "feature/actions"
    assert "-f" in args and "task=TASK-0001" in args
else:
    raise SystemExit(f"unexpected args: {{args}}")
""".lstrip(),
                encoding="utf-8",
            )
            fake_gh.chmod(0o755)

            artifacts = ci_adapters.run_github_actions(
                {
                    "schema_version": 1,
                    "provider": "github-actions",
                    "action": "artifacts",
                    "root": str(root),
                    "provider_options": {
                        "command": str(fake_gh),
                        "repository": "owner/repo",
                        "run_id": "456",
                        "download_dir": str(download_dir),
                    },
                }
            )
            rerun = ci_adapters.run_github_actions(
                {
                    "schema_version": 1,
                    "provider": "github-actions",
                    "action": "rerun",
                    "root": str(root),
                    "provider_options": {"command": str(fake_gh), "run_id": "456", "rerun_failed": True},
                }
            )
            dispatch = ci_adapters.run_github_actions(
                {
                    "schema_version": 1,
                    "provider": "github-actions",
                    "action": "dispatch",
                    "root": str(root),
                    "provider_options": {
                        "command": str(fake_gh),
                        "workflow": "ci.yml",
                        "ref": "feature/actions",
                        "inputs": {"task": "TASK-0001"},
                    },
                }
            )

            self.assertEqual(artifacts["status"], "passed")
            self.assertEqual(artifacts["artifacts"][0]["name"], "attestflow-evidence")
            self.assertEqual(artifacts["download_dir"], str(download_dir))
            self.assertEqual(rerun["status"], "queued")
            self.assertEqual(dispatch["status"], "queued")
            recorded_calls = [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]
            self.assertIn(["run", "rerun", "456", "--failed"], recorded_calls)
            self.assertIn(["workflow", "run", "ci.yml", "--ref", "feature/actions", "-f", "task=TASK-0001"], recorded_calls)


if __name__ == "__main__":
    unittest.main()
