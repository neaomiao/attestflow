from pathlib import Path
from tempfile import TemporaryDirectory
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


if __name__ == "__main__":
    unittest.main()
