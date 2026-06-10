from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class OpenSourceP0Tests(unittest.TestCase):
    def test_local_provider_emits_planner_and_capability_contracts(self) -> None:
        provider = ROOT / "examples" / "providers" / "local_agent.py"
        self.assertTrue(provider.exists())

        planner_payload = {
            "schema_version": 1,
            "goal": "Add greeting support",
            "root": str(ROOT / "examples" / "python-basic"),
            "project": {"name": "python-basic", "adapter": "python"},
            "capability": {"name": "planner"},
        }
        planner = _run_provider(provider, planner_payload)
        self.assertEqual(planner["schema_version"], 1)
        self.assertEqual(planner["goal"], "Add greeting support")
        self.assertEqual(len(planner["tasks"]), 1)
        self.assertEqual(planner["tasks"][0]["files"]["write"][0], "greeter.py")

        capability_payload = {
            "schema_version": 1,
            "root": str(ROOT / "examples" / "python-basic"),
            "capability": {"name": "reviewer"},
            "task": {"id": "TASK-0001", "title": "Add greeting support"},
        }
        capability = _run_provider(provider, capability_payload)
        self.assertEqual(capability["schema_version"], 1)
        self.assertEqual(capability["status"], "passed")
        self.assertTrue(capability["summary"])
        self.assertIsInstance(capability["evidence"], list)

    def test_python_example_runs_local_autopilot_to_done(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            shutil.copytree(ROOT / "examples", tmp_root / "examples")
            example_root = tmp_root / "examples" / "python-basic"
            env = dict(os.environ)
            env["PYTHONPATH"] = str(ROOT)

            doctor = subprocess.run(
                [sys.executable, "-m", "attestflow", "doctor"],
                cwd=example_root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(doctor.returncode, 0, doctor.stderr + doctor.stdout)
            spec = _write_approved_spec(example_root, "Add greeting support")

            run = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "attestflow",
                    "go",
                    "--from-spec",
                    str(spec),
                    "--approve",
                    "--non-interactive",
                    "--loop",
                    "--max-cycles",
                    "12",
                    "--max-steps",
                    "1",
                ],
                cwd=example_root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
            done_tasks = sorted((example_root / "harness" / "tasks" / "done").glob("TASK-*.json"))
            self.assertEqual(len(done_tasks), 1)
            task = json.loads(done_tasks[0].read_text(encoding="utf-8"))
            self.assertEqual(task["state"], "done")
            self.assertEqual(task["title"], "Add greeting helper")

    def test_node_example_runs_local_autopilot_to_done_when_node_is_available(self) -> None:
        if shutil.which("node") is None:
            self.skipTest("node is not available")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            shutil.copytree(ROOT / "examples", tmp_root / "examples")
            example_root = tmp_root / "examples" / "node-basic"
            env = dict(os.environ)
            env["PYTHONPATH"] = str(ROOT)
            spec = _write_approved_spec(example_root, "Add greeting support")

            run = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "attestflow",
                    "go",
                    "--from-spec",
                    str(spec),
                    "--approve",
                    "--non-interactive",
                    "--loop",
                    "--max-cycles",
                    "12",
                    "--max-steps",
                    "1",
                ],
                cwd=example_root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
            done_tasks = sorted((example_root / "harness" / "tasks" / "done").glob("TASK-*.json"))
            self.assertEqual(len(done_tasks), 1)
            self.assertTrue((example_root / "greeter.js").exists())

    def test_repository_harness_runs_local_dogfood_autopilot_to_done(self) -> None:
        self.assertTrue((ROOT / "harness.yml").exists())
        for state in ("proposed", "needs_clarification", "ready", "in_progress", "blocked", "review", "verified", "accepted", "done", "archived"):
            self.assertTrue((ROOT / "harness" / "tasks" / state).is_dir())

        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp) / "attestflow"
            shutil.copytree(
                ROOT,
                repo_root,
                ignore=shutil.ignore_patterns(
                    ".git",
                    ".venv",
                    "__pycache__",
                    ".pytest_cache",
                    "harness/runs",
                    "harness/capability-runs",
                    "harness/autopilot-runs",
                    "harness/ci-runs",
                    "harness/git-runs",
                    "harness/pr-runs",
                    "harness/release-runs",
                    "harness/locks",
                ),
            )
            env = dict(os.environ)
            env["PYTHONPATH"] = str(repo_root)
            spec = _write_approved_spec(repo_root, "Dogfood Attestflow by adding the deterministic dogfood marker.")

            run = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "attestflow",
                    "go",
                    "--from-spec",
                    str(spec),
                    "--approve",
                    "--non-interactive",
                    "--until",
                    "terminal",
                    "--max-cycles",
                    "20",
                    "--max-steps",
                    "1",
                ],
                cwd=repo_root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
            done_tasks = sorted((repo_root / "harness" / "tasks" / "done").glob("TASK-*.json"))
            self.assertTrue(done_tasks)
            self.assertTrue((repo_root / "attestflow" / "dogfood_marker.py").exists())
            self.assertTrue(sorted((repo_root / "harness" / "autopilot-runs").glob("*/metadata.json")))

    def test_open_source_onboarding_docs_and_project_files_exist(self) -> None:
        required = [
            "docs/getting-started.md",
            "docs/providers.md",
            "CONTRIBUTING.md",
            "SECURITY.md",
            "CHANGELOG.md",
            "examples/python-basic/README.md",
            "examples/node-basic/README.md",
        ]
        missing = [path for path in required if not (ROOT / path).exists()]
        self.assertEqual(missing, [])

    def test_ci_verifies_supported_python_versions_and_build_artifact(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        for version in ("3.11", "3.12", "3.13"):
            self.assertIn(version, workflow)
        self.assertIn("python -m build", workflow)
        self.assertIn("python -m pip install", workflow)
        self.assertIn("attestflow install-smoke --offline", workflow)

    def test_source_distribution_manifest_includes_open_source_onboarding_assets(self) -> None:
        manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
        self.assertIn("include CHANGELOG.md CONTRIBUTING.md SECURITY.md", manifest)
        self.assertIn("recursive-include docs *.md", manifest)
        self.assertIn("recursive-include examples *", manifest)


def _run_provider(provider: Path, payload: dict) -> dict:
    completed = subprocess.run(
        [sys.executable, str(provider)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr + completed.stdout)
    try:
        output = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(completed.stdout) from exc
    if not isinstance(output, dict):
        raise AssertionError(f"provider output must be an object: {output!r}")
    return output


def _write_approved_spec(root: Path, goal: str, spec_id: str = "SPEC-0001") -> Path:
    spec = root / "harness/specs" / spec_id / "spec.md"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(
        f"# {spec_id}: {goal}\n\n## Goal\n{goal}\n\n## Acceptance Criteria\n- Planned work completes.\n\n## Open Questions\n- None\n",
        encoding="utf-8",
    )
    (spec.parent / "approval.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "spec_id": spec_id,
                "status": "approved",
                "approved_by": "test",
                "approved_at": "2026-06-10T00:00:00+00:00",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return spec
