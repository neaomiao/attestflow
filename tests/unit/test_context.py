from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from attestflow.context import collect_repository_context


class RepositoryContextTests(unittest.TestCase):
    def test_collects_tree_documents_and_focus_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("# Demo\n", encoding="utf-8")
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")

            context = collect_repository_context(
                root,
                {"context": {"max_tree_entries": 20, "max_file_bytes": 100}},
                focus_files=["src/app.py"],
            )

            self.assertIn("README.md", context["tree"])
            self.assertIn("src/app.py", context["tree"])
            self.assertEqual(context["documents"][0]["path"], "README.md")
            self.assertEqual(context["files"][0]["path"], "src/app.py")

    def test_excludes_runtime_outputs_and_binary_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "harness" / "runs" / "run-1").mkdir(parents=True)
            (root / "harness" / "runs" / "run-1" / "metadata.yml").write_text("secret: no\n", encoding="utf-8")
            (root / "harness" / "ci-runs" / "ci-1").mkdir(parents=True)
            (root / "harness" / "ci-runs" / "ci-1" / "output.json").write_text('{"status":"passed"}\n', encoding="utf-8")
            (root / "asset.bin").write_bytes(b"\x00\x01binary")

            context = collect_repository_context(
                root,
                {"context": {"documents": ["asset.bin"], "max_tree_entries": 20, "max_file_bytes": 100}},
                focus_files=["harness/runs/run-1/metadata.yml", "harness/ci-runs/ci-1/output.json", "asset.bin"],
            )

            self.assertNotIn("harness/runs/run-1/metadata.yml", context["tree"])
            self.assertNotIn("harness/ci-runs/ci-1/output.json", context["tree"])
            self.assertEqual(context["documents"], [])
            self.assertEqual(context["files"], [])

    def test_non_mapping_context_config_falls_back_to_defaults(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("# Demo\n", encoding="utf-8")

            context = collect_repository_context(root, {"context": "invalid"})

            self.assertTrue(context["enabled"])
            self.assertEqual(context["documents"][0]["path"], "README.md")


if __name__ == "__main__":
    unittest.main()
