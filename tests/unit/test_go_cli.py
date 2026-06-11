from __future__ import annotations

import io
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from attestflow import cli
from attestflow.go import prepare_go_run
from attestflow.io import dump_data


CONFIG = {"paths": {"specs": "harness/specs"}}


class GoCliTests(unittest.TestCase):
    def test_cli_go_inline_text_requires_spec_approval_without_autopilot(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            original_run_autopilot = cli.run_autopilot
            calls: list[dict[str, object]] = []

            def fake_run_autopilot(*args: object, **kwargs: object) -> cli.AutopilotRunResult:
                calls.append({"args": args, "kwargs": kwargs})
                return _autopilot_result(root)

            cli.run_autopilot = fake_run_autopilot

            stdout = io.StringIO()
            try:
                exit_code = self._run_cli(root, ["go", "实现登录功能"], stdout=stdout)
            finally:
                cli.run_autopilot = original_run_autopilot

            self.assertEqual(exit_code, 2)
            self.assertIn("spec approval required", stdout.getvalue())
            self.assertIn("open questions:", stdout.getvalue())
            self.assertTrue((root / "harness/specs/SPEC-0001/spec.md").exists())
            self.assertEqual(calls, [])

    def test_cli_go_clarify_captures_answers_but_still_requires_approval(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            stdin = io.StringIO(
                "\n".join(
                    [
                        "Admins signing in through the web app.",
                        "Email/password only; no OAuth.",
                        "No password reset in v1.",
                        "Login succeeds and bad passwords are rejected.",
                        "No external service; store credentials locally for the demo.",
                    ]
                )
                + "\n"
            )

            stdout = io.StringIO()
            exit_code = self._run_cli(root, ["go", "实现登录功能", "--clarify"], stdout=stdout, stdin=stdin)
            spec = root / "harness/specs/SPEC-0001/spec.md"
            content = spec.read_text(encoding="utf-8")

            self.assertEqual(exit_code, 2)
            self.assertIn("clarifications captured", stdout.getvalue())
            self.assertIn("## Clarifications", content)
            self.assertIn("Admins signing in through the web app.", content)
            self.assertIn("No password reset in v1.", content)
            self.assertIn("## Open Questions", content)
            self.assertIn("- None", content)

    def test_cli_go_non_interactive_requires_approved_spec(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)

            stderr = io.StringIO()
            exit_code = self._run_cli(root, ["go", "实现登录功能", "--non-interactive"], stderr=stderr)

            self.assertEqual(exit_code, 1)
            self.assertIn("--non-interactive requires --from-spec and --approve", stderr.getvalue())

    def test_cli_go_missing_requirement_path_returns_error(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)

            stderr = io.StringIO()
            exit_code = self._run_cli(root, ["go", "docs/missing.md"], stderr=stderr)

            self.assertEqual(exit_code, 1)
            self.assertIn("requirement source path does not exist", stderr.getvalue())

    def test_cli_go_approved_spec_runs_autopilot_with_spec_goal_and_limits(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            goal = _approved_spec_content("SPEC-0001")
            spec = _write_approved_spec(root, "SPEC-0001", goal)
            original_run_autopilot = cli.run_autopilot
            calls: list[dict[str, object]] = []

            def fake_run_autopilot(*args: object, **kwargs: object) -> cli.AutopilotRunResult:
                calls.append({"args": args, "kwargs": kwargs})
                return _autopilot_result(root, status="finished")

            cli.run_autopilot = fake_run_autopilot

            stdout = io.StringIO()
            try:
                exit_code = self._run_cli(
                    root,
                    ["go", "--from-spec", str(spec), "--approve", "--non-interactive", "--limit", "3", "--max-steps", "5"],
                    stdout=stdout,
                )
            finally:
                cli.run_autopilot = original_run_autopilot

            self.assertEqual(exit_code, 0)
            self.assertIn(f"spec approved: {spec.resolve()}", stdout.getvalue())
            self.assertIn("autopilot run:", stdout.getvalue())
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["args"], (root, cli.load_config(root)))
            self.assertEqual(
                calls[0]["kwargs"],
                {
                    "limit": 3,
                    "max_steps": 5,
                    "actor_role": "orchestrator",
                    "goal": goal,
                    "approved_spec_path": spec.resolve(),
                },
            )

    def test_cli_go_approved_spec_returns_nonzero_when_autopilot_blocks(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = _write_approved_spec(root, "SPEC-0001", _approved_spec_content("SPEC-0001"))
            original_run_autopilot = cli.run_autopilot

            def fake_run_autopilot(*args: object, **kwargs: object) -> cli.AutopilotRunResult:
                return _autopilot_result(root, status="blocked", blocked=["TASK-0001"])

            cli.run_autopilot = fake_run_autopilot

            stdout = io.StringIO()
            try:
                exit_code = self._run_cli(
                    root,
                    ["go", "--from-spec", str(spec), "--approve", "--non-interactive"],
                    stdout=stdout,
                )
            finally:
                cli.run_autopilot = original_run_autopilot

            self.assertEqual(exit_code, 1)
            self.assertIn("blocked 1 task(s): TASK-0001", stdout.getvalue())

    def test_cli_go_approved_spec_returns_nonzero_when_autopilot_pauses(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = _write_approved_spec(root, "SPEC-0001", _approved_spec_content("SPEC-0001"))
            original_run_autopilot = cli.run_autopilot

            def fake_run_autopilot(*args: object, **kwargs: object) -> cli.AutopilotRunResult:
                return _autopilot_result(root, status="paused")

            cli.run_autopilot = fake_run_autopilot

            try:
                exit_code = self._run_cli(root, ["go", "--from-spec", str(spec), "--approve"])
            finally:
                cli.run_autopilot = original_run_autopilot

            self.assertEqual(exit_code, 1)

    def test_cli_go_rejects_approved_spec_outside_configured_specs_without_autopilot(self) -> None:
        with TemporaryDirectory() as tmp, TemporaryDirectory() as outside_tmp:
            root = Path(tmp)
            spec = Path(outside_tmp) / "SPEC-0001/spec.md"
            spec.parent.mkdir(parents=True)
            spec.write_text(_approved_spec_content("SPEC-0001"), encoding="utf-8")
            dump_data(
                {
                    "schema_version": 1,
                    "spec_id": "SPEC-0001",
                    "status": "approved",
                    "approved_by": "alice",
                    "approved_at": "2026-06-10T00:00:00+00:00",
                },
                spec.parent / "approval.json",
            )
            original_run_autopilot = cli.run_autopilot
            calls: list[dict[str, object]] = []

            def fake_run_autopilot(*args: object, **kwargs: object) -> cli.AutopilotRunResult:
                calls.append({"args": args, "kwargs": kwargs})
                return _autopilot_result(root)

            cli.run_autopilot = fake_run_autopilot

            stderr = io.StringIO()
            try:
                exit_code = self._run_cli(root, ["go", "--from-spec", str(spec), "--approve"], stderr=stderr)
            finally:
                cli.run_autopilot = original_run_autopilot

            self.assertEqual(exit_code, 1)
            self.assertIn("spec path must be under configured specs directory", stderr.getvalue())
            self.assertEqual(calls, [])

    def test_cli_go_rejects_approved_spec_with_invalid_spec_directory_without_autopilot(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = root / "harness/specs/not-a-spec/spec.md"
            spec.parent.mkdir(parents=True)
            spec.write_text(_approved_spec_content("SPEC-0001"), encoding="utf-8")
            dump_data(
                {
                    "schema_version": 1,
                    "spec_id": "not-a-spec",
                    "status": "approved",
                    "approved_by": "alice",
                    "approved_at": "2026-06-10T00:00:00+00:00",
                },
                spec.parent / "approval.json",
            )
            original_run_autopilot = cli.run_autopilot
            calls: list[dict[str, object]] = []

            def fake_run_autopilot(*args: object, **kwargs: object) -> cli.AutopilotRunResult:
                calls.append({"args": args, "kwargs": kwargs})
                return _autopilot_result(root)

            cli.run_autopilot = fake_run_autopilot

            stderr = io.StringIO()
            try:
                exit_code = self._run_cli(root, ["go", "--from-spec", str(spec), "--approve"], stderr=stderr)
            finally:
                cli.run_autopilot = original_run_autopilot

            self.assertEqual(exit_code, 1)
            self.assertIn("spec path must be SPEC-####/spec.md", stderr.getvalue())
            self.assertEqual(calls, [])

    def test_cli_go_rejects_nested_approved_spec_under_specs_without_autopilot(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = root / "harness/specs/archive/SPEC-0001/spec.md"
            spec.parent.mkdir(parents=True)
            spec.write_text(_approved_spec_content("SPEC-0001"), encoding="utf-8")
            dump_data(
                {
                    "schema_version": 1,
                    "spec_id": "SPEC-0001",
                    "status": "approved",
                    "approved_by": "alice",
                    "approved_at": "2026-06-10T00:00:00+00:00",
                },
                spec.parent / "approval.json",
            )
            original_run_autopilot = cli.run_autopilot
            calls: list[dict[str, object]] = []

            def fake_run_autopilot(*args: object, **kwargs: object) -> cli.AutopilotRunResult:
                calls.append({"args": args, "kwargs": kwargs})
                return _autopilot_result(root)

            cli.run_autopilot = fake_run_autopilot

            stderr = io.StringIO()
            try:
                exit_code = self._run_cli(root, ["go", "--from-spec", str(spec), "--approve"], stderr=stderr)
            finally:
                cli.run_autopilot = original_run_autopilot

            self.assertEqual(exit_code, 1)
            self.assertIn("spec path must be SPEC-####/spec.md", stderr.getvalue())
            self.assertEqual(calls, [])

    def test_cli_go_rejects_repo_external_configured_specs_without_autopilot(self) -> None:
        with TemporaryDirectory() as tmp, TemporaryDirectory() as outside_tmp:
            root = Path(tmp)
            specs_root = Path(outside_tmp) / "specs"
            dump_data({"schema_version": 1, "paths": {"specs": str(specs_root)}}, root / "harness.yml")
            spec = specs_root / "SPEC-0001/spec.md"
            spec.parent.mkdir(parents=True)
            spec.write_text(_approved_spec_content("SPEC-0001"), encoding="utf-8")
            dump_data(
                {
                    "schema_version": 1,
                    "spec_id": "SPEC-0001",
                    "status": "approved",
                    "approved_by": "alice",
                    "approved_at": "2026-06-10T00:00:00+00:00",
                },
                spec.parent / "approval.json",
            )
            original_run_autopilot = cli.run_autopilot
            calls: list[dict[str, object]] = []

            def fake_run_autopilot(*args: object, **kwargs: object) -> cli.AutopilotRunResult:
                calls.append({"args": args, "kwargs": kwargs})
                return _autopilot_result(root)

            cli.run_autopilot = fake_run_autopilot

            stderr = io.StringIO()
            try:
                exit_code = self._run_cli(root, ["go", "--from-spec", str(spec), "--approve"], stderr=stderr)
            finally:
                cli.run_autopilot = original_run_autopilot

            self.assertEqual(exit_code, 1)
            self.assertIn("configured specs directory must be under project root", stderr.getvalue())
            self.assertEqual(calls, [])

    def test_prepare_go_run_creates_spec_for_inline_text(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)

            result = prepare_go_run(root, CONFIG, "实现登录功能")

            self.assertEqual(result.status, "needs_approval")
            self.assertEqual(result.spec_id, "SPEC-0001")
            self.assertEqual(result.spec_path, root / "harness/specs/SPEC-0001/spec.md")
            self.assertTrue(result.spec_path.exists())
            self.assertIsNone(result.goal)
            self.assertTrue(result.open_questions)
            self.assertIn("[Q1]", result.open_questions[0])
            self.assertIn("实现登录功能", result.spec_path.read_text(encoding="utf-8"))

    def test_prepare_go_run_uses_configured_clarifier_command_for_open_questions(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            clarifier = root / "clarifier.py"
            clarifier.write_text(
                "import json, sys\n"
                "payload = json.loads(sys.stdin.read())\n"
                "assert payload['source']['text'] == '实现登录功能'\n"
                "print(json.dumps({\n"
                "  'schema_version': 1,\n"
                "  'questions': [\n"
                "    'Who can log in and from which client?',\n"
                "    'Which auth methods are in scope?'\n"
                "  ]\n"
                "}))\n",
                encoding="utf-8",
            )
            config = {
                "paths": {
                    "specs": "harness/specs",
                    "requirement_runs": "harness/requirement-runs",
                },
                "requirements": {
                    "clarifier_command": f"{sys.executable} {clarifier}",
                    "max_open_questions": 5,
                },
            }

            result = prepare_go_run(root, config, "实现登录功能")

            self.assertEqual(
                result.open_questions,
                ["Who can log in and from which client?", "Which auth methods are in scope?"],
            )
            self.assertIn("Who can log in", result.spec_path.read_text(encoding="utf-8"))
            self.assertTrue(any((root / "harness" / "requirement-runs").glob("clarifier-*/input.json")))

    def test_prepare_go_run_creates_spec_for_markdown_file_with_filename_title(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "login_requirements-v1.md"
            source.write_text("# Login\n\nUsers sign in with email.\n", encoding="utf-8")

            result = prepare_go_run(root, CONFIG, str(source))

            self.assertEqual(result.status, "needs_approval")
            self.assertEqual(result.spec_id, "SPEC-0001")
            content = result.spec_path.read_text(encoding="utf-8")
            self.assertIn("# SPEC-0001: login requirements v1", content)
            self.assertIn("Users sign in with email.", content)

    def test_prepare_go_run_rejects_missing_relative_document_path(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)

            with self.assertRaisesRegex(
                ValueError,
                "requirement source path does not exist: docs/missing-requirements.md",
            ):
                prepare_go_run(root, CONFIG, "docs/missing-requirements.md")

            self.assertFalse((root / "harness/specs/SPEC-0001/spec.md").exists())

    def test_prepare_go_run_rejects_missing_absolute_document_path(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "missing.pdf"

            with self.assertRaisesRegex(ValueError, f"requirement source path does not exist: {source}"):
                prepare_go_run(root, CONFIG, str(source))

            self.assertFalse((root / "harness/specs/SPEC-0001/spec.md").exists())

    def test_non_interactive_raw_text_is_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "--non-interactive requires --from-spec and --approve"):
                prepare_go_run(Path(tmp), CONFIG, "实现登录功能", non_interactive=True)

    def test_from_spec_without_approve_is_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            spec = Path(tmp) / "harness/specs/SPEC-0001/spec.md"
            spec.parent.mkdir(parents=True)
            spec.write_text("Implement login.", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "--from-spec requires --approve before execution"):
                prepare_go_run(Path(tmp), CONFIG, None, from_spec=spec)

    def test_from_spec_with_approve_requires_approval_metadata(self) -> None:
        with TemporaryDirectory() as tmp:
            spec = Path(tmp) / "harness/specs/SPEC-0001/spec.md"
            spec.parent.mkdir(parents=True)
            spec.write_text("## Open Questions\n\n- None\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "spec approval is missing"):
                prepare_go_run(Path(tmp), CONFIG, None, from_spec=spec, approve=True)

    def test_cli_go_interactive_approve_pending_spec_before_autopilot(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = _write_pending_spec(root, "SPEC-0001", _approved_spec_content("SPEC-0001"))
            original_run_autopilot = cli.run_autopilot
            calls: list[dict[str, object]] = []

            def fake_run_autopilot(*args: object, **kwargs: object) -> cli.AutopilotRunResult:
                calls.append({"args": args, "kwargs": kwargs})
                return _autopilot_result(root, status="finished")

            cli.run_autopilot = fake_run_autopilot

            stdout = io.StringIO()
            try:
                exit_code = self._run_cli(root, ["go", "--from-spec", str(spec), "--approve"], stdout=stdout)
            finally:
                cli.run_autopilot = original_run_autopilot

            self.assertEqual(exit_code, 0)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["kwargs"]["approved_spec_path"], spec.resolve())

    def test_cli_go_non_interactive_rejects_pending_spec(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = _write_pending_spec(root, "SPEC-0001", _approved_spec_content("SPEC-0001"))

            stderr = io.StringIO()
            exit_code = self._run_cli(
                root,
                ["go", "--from-spec", str(spec), "--approve", "--non-interactive"],
                stderr=stderr,
            )

            self.assertEqual(exit_code, 1)
            self.assertIn("spec is not approved", stderr.getvalue())

    def test_approved_spec_returns_goal_text(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = root / "harness/specs/SPEC-0042/spec.md"
            spec.parent.mkdir(parents=True)
            goal = _approved_spec_content("SPEC-0042")
            spec.write_text(goal, encoding="utf-8")
            dump_data(
                {
                    "schema_version": 1,
                    "spec_id": "SPEC-0042",
                    "status": "approved",
                    "approved_by": "alice",
                    "approved_at": "2026-06-10T00:00:00+00:00",
                },
                spec.parent / "approval.json",
            )

            result = prepare_go_run(root, CONFIG, None, from_spec=spec, approve=True, non_interactive=True)

            self.assertEqual(result.status, "approved")
            self.assertEqual(result.spec_id, "SPEC-0042")
            self.assertEqual(result.spec_path, spec.resolve())
            self.assertEqual(result.goal, goal)

    def _run_cli(
        self,
        root: Path,
        argv: list[str],
        *,
        stdout: io.StringIO | None = None,
        stderr: io.StringIO | None = None,
        stdin: io.StringIO | None = None,
    ) -> int:
        original_root = cli.ROOT
        cli.ROOT = root
        stdout = stdout or io.StringIO()
        stderr = stderr or io.StringIO()
        stdin = stdin or io.StringIO()
        try:
            with redirect_stdout(stdout), redirect_stderr(stderr), patch("sys.stdin", stdin):
                return cli.main(argv)
        finally:
            cli.ROOT = original_root


def _write_approved_spec(root: Path, spec_id: str, content: str) -> Path:
    spec = root / "harness/specs" / spec_id / "spec.md"
    spec.parent.mkdir(parents=True)
    spec.write_text(content, encoding="utf-8")
    dump_data(
        {
            "schema_version": 1,
            "spec_id": spec_id,
            "status": "approved",
            "approved_by": "alice",
            "approved_at": "2026-06-10T00:00:00+00:00",
        },
        spec.parent / "approval.json",
    )
    return spec


def _write_pending_spec(root: Path, spec_id: str, content: str) -> Path:
    spec = root / "harness/specs" / spec_id / "spec.md"
    spec.parent.mkdir(parents=True)
    spec.write_text(content, encoding="utf-8")
    dump_data(
        {
            "schema_version": 1,
            "spec_id": spec_id,
            "status": "pending",
            "approved_by": None,
            "approved_at": None,
        },
        spec.parent / "approval.json",
    )
    return spec


def _approved_spec_content(spec_id: str) -> str:
    return (
        f"# {spec_id}: Login\n"
        "\n"
        "## Goal\n"
        "Ship login.\n"
        "\n"
        "## Acceptance Criteria\n"
        "- Login works.\n"
        "\n"
        "## Open Questions\n"
        "- None\n"
    )


def _autopilot_result(
    root: Path,
    *,
    status: str = "finished",
    blocked: list[str] | None = None,
) -> cli.AutopilotRunResult:
    return cli.AutopilotRunResult(
        run_id="run-1",
        path=root / "harness/autopilot-runs/run-1",
        status=status,
        pause_reason=None,
        dispatched=[],
        actions=[],
        failed=[],
        blocked=blocked or [],
        cancelled=[],
        planned=[],
        skipped=[],
        steps=1,
        limit=1,
    )


if __name__ == "__main__":
    unittest.main()
