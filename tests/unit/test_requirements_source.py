from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import os
import sys
import unittest
from unittest.mock import patch

from attestflow.io import load_data
from attestflow.requirements import ingest_requirement_source


class RequirementSourceTests(unittest.TestCase):
    def test_ingests_inline_text_as_source_evidence(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = ingest_requirement_source(root, {"paths": {"specs": "harness/specs"}}, "实现登录功能")

            self.assertEqual(result.kind, "inline_text")
            self.assertEqual(result.text, "实现登录功能")
            self.assertIsNone(result.source_path)
            self.assertIsNone(result.format)
            self.assertEqual(result.evidence_path.name, "source.json")
            self.assertEqual(result.evidence_path.parent.parent.name, "sources")
            self.assertTrue(result.evidence_path.parent.name.endswith("-inline"))
            source = load_data(result.evidence_path)
            self.assertEqual(source["schema_version"], 1)
            self.assertEqual(source["kind"], "inline_text")
            self.assertEqual(source["original"], "实现登录功能")
            self.assertTrue(source["content_hash"])
            self.assertTrue(source["received_at"])

    def test_preserves_inline_text_whitespace_after_empty_check(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = "  实现登录功能  "
            result = ingest_requirement_source(root, {"paths": {"specs": "harness/specs"}}, original)

            self.assertEqual(result.text, original)
            source = load_data(result.evidence_path)
            self.assertEqual(source["original"], original)

    def test_ingests_markdown_file_and_preserves_source_text(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            prd = root / "PRD.md"
            prd.write_text("# Login\n\nUsers sign in with email.\n", encoding="utf-8")

            result = ingest_requirement_source(root, {"paths": {"specs": "harness/specs"}}, str(prd))

            self.assertEqual(result.kind, "file")
            self.assertEqual(result.source_path, prd)
            self.assertEqual(result.format, "md")
            self.assertIn("Users sign in with email.", result.text)
            source = load_data(result.evidence_path)
            self.assertEqual(source["schema_version"], 1)
            self.assertEqual(source["kind"], "file")
            self.assertEqual(source["path"], str(prd))
            self.assertEqual(source["format"], "md")
            self.assertTrue(source["content_hash"])
            self.assertTrue(source["received_at"])
            self.assertEqual((result.evidence_path.parent / "source.txt").read_text(encoding="utf-8"), result.text)

    def test_resolves_relative_file_paths_from_root_not_process_cwd(self) -> None:
        with TemporaryDirectory() as tmp, TemporaryDirectory() as cwd_tmp:
            root = Path(tmp)
            cwd = Path(cwd_tmp)
            root_prd = root / "PRD.md"
            cwd_prd = cwd / "PRD.md"
            root_prd.write_text("root requirement\n", encoding="utf-8")
            cwd_prd.write_text("cwd requirement\n", encoding="utf-8")

            previous_cwd = Path.cwd()
            try:
                os.chdir(cwd)
                result = ingest_requirement_source(root, {"paths": {"specs": "harness/specs"}}, "PRD.md")
            finally:
                os.chdir(previous_cwd)

            self._assert_file_source(result, root_prd, "md", "root requirement\n")

    def test_same_content_from_different_paths_uses_distinct_evidence_paths(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first.md"
            second = root / "second.md"
            first.write_text("same requirement\n", encoding="utf-8")
            second.write_text("same requirement\n", encoding="utf-8")

            first_result = ingest_requirement_source(root, {"paths": {"specs": "harness/specs"}}, str(first))
            second_result = ingest_requirement_source(root, {"paths": {"specs": "harness/specs"}}, str(second))

            self.assertNotEqual(first_result.evidence_path, second_result.evidence_path)
            self.assertEqual(load_data(first_result.evidence_path)["path"], str(first))
            self.assertEqual(load_data(second_result.evidence_path)["path"], str(second))

    def test_same_source_reingestion_is_idempotent(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            prd = root / "PRD.md"
            prd.write_text("same requirement\n", encoding="utf-8")

            first = ingest_requirement_source(root, {"paths": {"specs": "harness/specs"}}, str(prd))
            first_received_at = load_data(first.evidence_path)["received_at"]
            second = ingest_requirement_source(root, {"paths": {"specs": "harness/specs"}}, str(prd))

            self.assertEqual(first.evidence_path, second.evidence_path)
            self.assertEqual(load_data(second.evidence_path)["received_at"], first_received_at)

    def test_ingests_markdown_extension_file(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            prd = root / "PRD.markdown"
            prd.write_text("# Login\n\nUsers sign in with email.\n", encoding="utf-8")

            result = ingest_requirement_source(root, {"paths": {"specs": "harness/specs"}}, str(prd))

            self._assert_file_source(result, prd, "markdown", "# Login\n\nUsers sign in with email.\n")

    def test_ingests_text_file(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            prd = root / "requirements.txt"
            prd.write_text("Users sign in with email.\n", encoding="utf-8")

            result = ingest_requirement_source(root, {"paths": {"specs": "harness/specs"}}, str(prd))

            self._assert_file_source(result, prd, "txt", "Users sign in with email.\n")

    def test_dispatches_docx_file_extraction(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            prd = root / "requirements.docx"
            prd.write_bytes(b"not a real docx")

            with patch("attestflow.requirements._extract_docx_text", return_value="Users sign in with email."):
                result = ingest_requirement_source(root, {"paths": {"specs": "harness/specs"}}, str(prd))

            self._assert_file_source(result, prd, "docx", "Users sign in with email.")

    def test_dispatches_pdf_file_extraction(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            prd = root / "requirements.pdf"
            prd.write_bytes(b"%PDF-1.4\n")

            with patch("attestflow.requirements._extract_pdf_text", return_value="Users sign in with email."):
                result = ingest_requirement_source(root, {"paths": {"specs": "harness/specs"}}, str(prd))

            self._assert_file_source(result, prd, "pdf", "Users sign in with email.")

    def test_rejects_empty_requirement_source(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "requirement source cannot be empty"):
                ingest_requirement_source(Path(tmp), {"paths": {"specs": "harness/specs"}}, "   ")

    def test_rejects_unsupported_file_suffix(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "requirements.rtf"
            source.write_text("{\\rtf1}", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "unsupported requirement source format: rtf"):
                ingest_requirement_source(root, {"paths": {"specs": "harness/specs"}}, str(source))

    def test_rejects_pdf_without_extractable_text(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / "scan.pdf"
            pdf.write_bytes(b"%PDF-1.4\n")

            with patch("attestflow.requirements._extract_pdf_text", return_value=""):
                with self.assertRaisesRegex(ValueError, "PDF text layer could not be extracted"):
                    ingest_requirement_source(root, {"paths": {"specs": "harness/specs"}}, str(pdf))

    def test_docx_dependency_error_is_stable_when_missing(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            docx = root / "requirements.docx"
            docx.write_bytes(b"not a real docx")

            with patch.dict(sys.modules, {"docx": None}):
                with self.assertRaisesRegex(ValueError, r"DOCX support requires installing attestflow\[documents\]"):
                    ingest_requirement_source(root, {"paths": {"specs": "harness/specs"}}, str(docx))

    def test_pdf_dependency_error_is_stable_when_missing(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / "requirements.pdf"
            pdf.write_bytes(b"%PDF-1.4\n")

            with patch.dict(sys.modules, {"pypdf": None}):
                with self.assertRaisesRegex(ValueError, r"PDF support requires installing attestflow\[documents\]"):
                    ingest_requirement_source(root, {"paths": {"specs": "harness/specs"}}, str(pdf))

    def test_pdf_parser_errors_are_reported_as_parse_failures(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / "requirements.pdf"
            pdf.write_bytes(b"%PDF-1.4\n")

            def fail_reader(path: str) -> object:
                raise RuntimeError(f"cannot parse {path}")

            with patch.dict(sys.modules, {"pypdf": SimpleNamespace(PdfReader=fail_reader)}):
                with self.assertRaisesRegex(ValueError, "PDF could not be parsed"):
                    ingest_requirement_source(root, {"paths": {"specs": "harness/specs"}}, str(pdf))

    def _assert_file_source(self, result, path: Path, source_format: str, text: str) -> None:
        self.assertEqual(result.kind, "file")
        self.assertEqual(result.source_path, path)
        self.assertEqual(result.format, source_format)
        self.assertEqual(result.text, text)
        source = load_data(result.evidence_path)
        self.assertEqual(source["schema_version"], 1)
        self.assertEqual(source["kind"], "file")
        self.assertEqual(source["path"], str(path))
        self.assertEqual(source["format"], source_format)
        self.assertTrue(source["content_hash"])
        self.assertTrue(source["received_at"])
        self.assertEqual((result.evidence_path.parent / "source.txt").read_text(encoding="utf-8"), text)


if __name__ == "__main__":
    unittest.main()
