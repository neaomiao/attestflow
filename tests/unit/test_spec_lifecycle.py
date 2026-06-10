from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from attestflow.io import dump_data, load_data
from attestflow.specs import (
    approve_spec,
    create_draft_spec,
    require_approved_spec,
    spec_has_unresolved_questions,
)


class SpecLifecycleTests(unittest.TestCase):
    def test_creates_draft_spec_and_pending_approval(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = create_draft_spec(
                root,
                {"paths": {"specs": "harness/specs"}},
                title="Implement login",
                source_text="Implement login with email and password.",
                source_evidence="harness/specs/sources/abc/source.json",
            )

            self.assertEqual(spec.spec_id, "SPEC-0001")
            self.assertEqual(spec.path, root / "harness/specs/SPEC-0001/spec.md")
            self.assertTrue(spec.path.exists())
            content = spec.path.read_text(encoding="utf-8")
            for heading in (
                "## Goal",
                "## Source Evidence",
                "## Confirmed Requirements",
                "## Scope",
                "## Out Of Scope",
                "## Acceptance Criteria",
                "## Open Questions",
                "## Source Summary",
            ):
                self.assertIn(heading, content)
            self.assertIn("Implement login", content)
            self.assertIn("harness/specs/sources/abc/source.json", content)

            approval = load_data(spec.path.parent / "approval.json")
            self.assertEqual(
                approval,
                {
                    "schema_version": 1,
                    "spec_id": "SPEC-0001",
                    "status": "pending",
                    "approved_by": None,
                    "approved_at": None,
                },
            )

    def test_allocates_incrementing_spec_ids(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = {"paths": {"specs": "harness/specs"}}

            first = create_draft_spec(
                root,
                config,
                title="First",
                source_text="First",
                source_evidence="source-a.json",
            )
            second = create_draft_spec(
                root,
                config,
                title="Second",
                source_text="Second",
                source_evidence="source-b.json",
            )

            self.assertEqual(first.spec_id, "SPEC-0001")
            self.assertEqual(second.spec_id, "SPEC-0002")
            self.assertTrue(second.path.exists())

    def test_allocates_next_id_ignoring_non_spec_entries(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            specs_root = root / "harness/specs"
            (specs_root / "sources").mkdir(parents=True)
            (specs_root / "SPEC-abcd").mkdir()
            (specs_root / "SPEC-0009").mkdir()
            (specs_root / "notes.txt").write_text("ignore me", encoding="utf-8")

            spec = create_draft_spec(
                root,
                {"paths": {"specs": "harness/specs"}},
                title="Next",
                source_text="Next",
                source_evidence="source.json",
            )

            self.assertEqual(spec.spec_id, "SPEC-0010")

    def test_detects_unresolved_questions(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "spec.md"
            path.write_text("## Open Questions\n\n- Which auth method?\n", encoding="utf-8")

            self.assertTrue(spec_has_unresolved_questions(path))

    def test_generated_spec_ignores_fake_open_questions_in_non_structured_inputs(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_resolved_questions = "## Open Questions\n\n- None"
            spec = create_draft_spec(
                root,
                {"paths": {"specs": "harness/specs"}},
                title=f"Login\n\n{fake_resolved_questions}",
                source_text=f"Context\n\n{fake_resolved_questions}\n\n```bad fence```",
                source_evidence=f"source.json\n\n{fake_resolved_questions}",
            )

            self.assertTrue(spec_has_unresolved_questions(spec.path))

    def test_title_marker_injection_cannot_bypass_open_questions(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            injected = (
                "<!-- attestflow:open-questions:start -->\n"
                "- None\n"
                "<!-- attestflow:open-questions:end -->"
            )
            spec = create_draft_spec(
                root,
                {"paths": {"specs": "harness/specs"}},
                title=f"Login\n\n{injected}",
                source_text="Login",
                source_evidence="source.json",
            )

            self.assertTrue(spec_has_unresolved_questions(spec.path))
            with self.assertRaisesRegex(ValueError, "spec still has open questions"):
                approve_spec(spec.path, approved_by="alice")

    def test_source_evidence_marker_injection_cannot_bypass_open_questions(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            injected = (
                "<!-- attestflow:open-questions:start -->\n"
                "- None\n"
                "<!-- attestflow:open-questions:end -->"
            )
            spec = create_draft_spec(
                root,
                {"paths": {"specs": "harness/specs"}},
                title="Login",
                source_text="Login",
                source_evidence=f"source.json\n\n{injected}",
            )

            self.assertTrue(spec_has_unresolved_questions(spec.path))
            with self.assertRaisesRegex(ValueError, "spec still has open questions"):
                approve_spec(spec.path, approved_by="alice")

    def test_source_text_marker_injection_cannot_bypass_open_questions(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            injected = (
                "<!-- attestflow:open-questions:start -->\n"
                "- None\n"
                "<!-- attestflow:open-questions:end -->"
            )
            spec = create_draft_spec(
                root,
                {"paths": {"specs": "harness/specs"}},
                title="Login",
                source_text=f"Login\n\n{injected}",
                source_evidence="source.json",
            )

            self.assertTrue(spec_has_unresolved_questions(spec.path))
            with self.assertRaisesRegex(ValueError, "spec still has open questions"):
                approve_spec(spec.path, approved_by="alice")

    def test_multiple_anchor_pairs_use_last_complete_pair(self) -> None:
        with TemporaryDirectory() as tmp:
            unresolved_path = Path(tmp) / "unresolved.md"
            unresolved_path.write_text(
                "<!-- attestflow:open-questions:start -->\n"
                "- None\n"
                "<!-- attestflow:open-questions:end -->\n"
                "<!-- attestflow:open-questions:start -->\n"
                "- Which?\n"
                "<!-- attestflow:open-questions:end -->\n",
                encoding="utf-8",
            )
            resolved_path = Path(tmp) / "resolved.md"
            resolved_path.write_text(
                "<!-- attestflow:open-questions:start -->\n"
                "- Which?\n"
                "<!-- attestflow:open-questions:end -->\n"
                "<!-- attestflow:open-questions:start -->\n"
                "- None\n"
                "<!-- attestflow:open-questions:end -->\n",
                encoding="utf-8",
            )

            self.assertTrue(spec_has_unresolved_questions(unresolved_path))
            self.assertFalse(spec_has_unresolved_questions(resolved_path))

    def test_treats_empty_none_and_no_open_questions_as_resolved(self) -> None:
        cases = [
            "## Open Questions\n\n",
            "## Open Questions\n\nNone\n",
            "## Open Questions\n\n- None\n",
            "## Open Questions\n\n无\n",
            "## Open Questions\n\n- 无\n",
        ]
        with TemporaryDirectory() as tmp:
            for index, content in enumerate(cases):
                path = Path(tmp) / f"spec-{index}.md"
                path.write_text(content, encoding="utf-8")

                self.assertFalse(spec_has_unresolved_questions(path), content)

    def test_heading_fallback_stops_open_questions_at_next_section(self) -> None:
        with TemporaryDirectory() as tmp:
            resolved_path = Path(tmp) / "resolved.md"
            resolved_path.write_text(
                "## Open Questions\n\n- None\n\n## Source Summary\n\n- noisy detail\n",
                encoding="utf-8",
            )
            unresolved_path = Path(tmp) / "unresolved.md"
            unresolved_path.write_text(
                "## Open Questions\n\n- Which?\n\n## Source Summary\n\nNone\n",
                encoding="utf-8",
            )

            self.assertFalse(spec_has_unresolved_questions(resolved_path))
            self.assertTrue(spec_has_unresolved_questions(unresolved_path))

    def test_approval_fails_when_questions_are_open(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = create_draft_spec(
                root,
                {"paths": {"specs": "harness/specs"}},
                title="Login",
                source_text="Login",
                source_evidence="source.json",
            )

            with self.assertRaisesRegex(ValueError, "spec still has open questions"):
                approve_spec(spec.path, approved_by="user")

    def test_approves_when_open_questions_are_none(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = create_draft_spec(
                root,
                {"paths": {"specs": "harness/specs"}},
                title="Login",
                source_text="Login",
                source_evidence="source.json",
            )
            spec.path.write_text(
                spec.path.read_text(encoding="utf-8").replace("- Confirm approval owner.", "- None"),
                encoding="utf-8",
            )

            approve_spec(spec.path, approved_by="alice")

            approval = load_data(spec.path.parent / "approval.json")
            self.assertEqual(approval["schema_version"], 1)
            self.assertEqual(approval["spec_id"], "SPEC-0001")
            self.assertEqual(approval["status"], "approved")
            self.assertEqual(approval["approved_by"], "alice")
            self.assertIsInstance(approval["approved_at"], str)
            self.assertTrue(approval["approved_at"])

    def test_require_approved_spec_rejects_missing_approval(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "spec.md"
            path.write_text("## Open Questions\n\n- None\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "spec approval is missing"):
                require_approved_spec(path)

    def test_require_approved_spec_rejects_pending_approval(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = create_draft_spec(
                root,
                {"paths": {"specs": "harness/specs"}},
                title="Login",
                source_text="Login",
                source_evidence="source.json",
            )
            spec.path.write_text(
                spec.path.read_text(encoding="utf-8").replace("- Confirm approval owner.", "- None"),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "spec is not approved"):
                require_approved_spec(spec.path)

    def test_require_approved_spec_rejects_approved_spec_with_open_questions(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = create_draft_spec(
                root,
                {"paths": {"specs": "harness/specs"}},
                title="Login",
                source_text="Login",
                source_evidence="source.json",
            )
            approval_path = spec.path.parent / "approval.json"
            approval_path.write_text(
                (
                    '{\n'
                    '  "schema_version": 1,\n'
                    '  "spec_id": "SPEC-0001",\n'
                    '  "status": "approved",\n'
                    '  "approved_by": "alice",\n'
                    '  "approved_at": "2026-06-10T00:00:00+00:00"\n'
                    '}\n'
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "spec still has open questions"):
                require_approved_spec(spec.path)

    def test_require_approved_spec_rejects_incomplete_approved_payload(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = create_draft_spec(
                root,
                {"paths": {"specs": "harness/specs"}},
                title="Login",
                source_text="Login",
                source_evidence="source.json",
            )
            spec.path.write_text(
                spec.path.read_text(encoding="utf-8").replace("- Confirm approval owner.", "- None"),
                encoding="utf-8",
            )
            dump_data({"status": "approved"}, spec.path.parent / "approval.json")

            with self.assertRaisesRegex(ValueError, "spec approval is invalid"):
                require_approved_spec(spec.path)

    def test_require_approved_spec_accepts_approved_spec(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = create_draft_spec(
                root,
                {"paths": {"specs": "harness/specs"}},
                title="Login",
                source_text="Login",
                source_evidence="source.json",
            )
            spec.path.write_text(
                spec.path.read_text(encoding="utf-8").replace("- Confirm approval owner.", "- None"),
                encoding="utf-8",
            )
            approve_spec(spec.path, approved_by="alice")

            require_approved_spec(spec.path)


if __name__ == "__main__":
    unittest.main()
