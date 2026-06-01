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
            (root / "harness" / "pr-runs" / "pr-1").mkdir(parents=True)
            (root / "harness" / "pr-runs" / "pr-1" / "output.json").write_text('{"status":"merged"}\n', encoding="utf-8")
            (root / "harness" / "release-runs" / "release-1").mkdir(parents=True)
            (root / "harness" / "release-runs" / "release-1" / "output.json").write_text(
                '{"status":"released"}\n',
                encoding="utf-8",
            )
            (root / "asset.bin").write_bytes(b"\x00\x01binary")

            context = collect_repository_context(
                root,
                {"context": {"documents": ["asset.bin"], "max_tree_entries": 20, "max_file_bytes": 100}},
                focus_files=[
                    "harness/runs/run-1/metadata.yml",
                    "harness/ci-runs/ci-1/output.json",
                    "harness/pr-runs/pr-1/output.json",
                    "harness/release-runs/release-1/output.json",
                    "asset.bin",
                ],
            )

            self.assertNotIn("harness/runs/run-1/metadata.yml", context["tree"])
            self.assertNotIn("harness/ci-runs/ci-1/output.json", context["tree"])
            self.assertNotIn("harness/pr-runs/pr-1/output.json", context["tree"])
            self.assertNotIn("harness/release-runs/release-1/output.json", context["tree"])
            self.assertEqual(context["documents"], [])
            self.assertEqual(context["files"], [])

    def test_non_mapping_context_config_falls_back_to_defaults(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("# Demo\n", encoding="utf-8")

            context = collect_repository_context(root, {"context": "invalid"})

            self.assertTrue(context["enabled"])
            self.assertEqual(context["documents"][0]["path"], "README.md")

    def test_context_includes_symbol_dependency_test_and_dynamic_context_indexes(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "tests" / "unit").mkdir(parents=True)
            (root / "src" / "payments.py").write_text(
                "\n".join(
                    [
                        "import json",
                        "from pathlib import Path",
                        "",
                        "class PaymentService:",
                        "    def charge(self) -> None:",
                        "        pass",
                        "",
                        "def normalize_amount(value: str) -> int:",
                        "    return int(value)",
                    ]
                ),
                encoding="utf-8",
            )
            (root / "tests" / "unit" / "test_payments.py").write_text(
                "from src.payments import normalize_amount\n",
                encoding="utf-8",
            )

            context = collect_repository_context(
                root,
                {"context": {"max_tree_entries": 20, "max_file_bytes": 1000}},
                focus_files=["src/payments.py"],
            )

            symbols = {(item["kind"], item["name"], item["path"]) for item in context["symbols"]}
            self.assertIn(("class", "PaymentService", "src/payments.py"), symbols)
            self.assertIn(("function", "normalize_amount", "src/payments.py"), symbols)
            dependencies = {(item["source"], item["target"]) for item in context["dependencies"]}
            self.assertIn(("src/payments.py", "json"), dependencies)
            self.assertIn(("src/payments.py", "pathlib.Path"), dependencies)
            test_map = {(item["source"], item["test"]) for item in context["test_map"]}
            self.assertIn(("src/payments.py", "tests/unit/test_payments.py"), test_map)
            self.assertEqual(context["dynamic_context"]["request_schema_version"], 1)
            self.assertIn("symbol_lookup", context["dynamic_context"]["allowed_requests"])


if __name__ == "__main__":
    unittest.main()
