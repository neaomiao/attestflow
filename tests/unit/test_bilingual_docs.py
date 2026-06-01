from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]


EXPECTED_DOC_PAIRS = [
    ("README.md", "README.zh-CN.md"),
    ("CHANGELOG.md", "CHANGELOG.zh-CN.md"),
    ("CONTRIBUTING.md", "CONTRIBUTING.zh-CN.md"),
    ("SECURITY.md", "SECURITY.zh-CN.md"),
    ("docs/getting-started.en.md", "docs/getting-started.md"),
    ("docs/github-actions.md", "docs/github-actions.zh-CN.md"),
    ("docs/governance.en.md", "docs/governance.md"),
    ("docs/providers.en.md", "docs/providers.md"),
    ("docs/design/universal-harness.en.md", "docs/design/universal-harness.md"),
    ("docs/contracts/autonomy-contract.en.md", "docs/contracts/autonomy-contract.md"),
    ("docs/contracts/capability-schema.en.md", "docs/contracts/capability-schema.md"),
    ("docs/contracts/ci-provider-schema.en.md", "docs/contracts/ci-provider-schema.md"),
    ("docs/contracts/evidence-schema.en.md", "docs/contracts/evidence-schema.md"),
    ("docs/contracts/git-provider-schema.en.md", "docs/contracts/git-provider-schema.md"),
    ("docs/contracts/planner-output-schema.en.md", "docs/contracts/planner-output-schema.md"),
    ("docs/contracts/pr-provider-schema.en.md", "docs/contracts/pr-provider-schema.md"),
    ("docs/contracts/release-provider-schema.en.md", "docs/contracts/release-provider-schema.md"),
    ("docs/contracts/session-adapter-schema.en.md", "docs/contracts/session-adapter-schema.md"),
    ("docs/contracts/task-schema.en.md", "docs/contracts/task-schema.md"),
    ("examples/node-basic/README.md", "examples/node-basic/README.zh-CN.md"),
    ("examples/python-basic/README.md", "examples/python-basic/README.zh-CN.md"),
]

ADAPTERS = [
    "bazel",
    "dart",
    "docker",
    "dotnet",
    "generic",
    "go",
    "java",
    "kotlin",
    "monorepo",
    "node",
    "php",
    "python",
    "ruby",
    "rust",
    "swift",
]

for adapter in ADAPTERS:
    EXPECTED_DOC_PAIRS.append((f"templates/adapters/{adapter}/README.md", f"templates/adapters/{adapter}/README.zh-CN.md"))
    EXPECTED_DOC_PAIRS.append(
        (
            f"attestflow/templates/adapters/{adapter}/README.md",
            f"attestflow/templates/adapters/{adapter}/README.zh-CN.md",
        )
    )


class BilingualDocsTests(unittest.TestCase):
    maxDiff = None

    def test_public_markdown_docs_have_declared_language_pairs(self) -> None:
        missing: list[str] = []
        for english, chinese in EXPECTED_DOC_PAIRS:
            if not (ROOT / english).exists():
                missing.append(english)
            if not (ROOT / chinese).exists():
                missing.append(chinese)

        self.assertEqual(missing, [])

    def test_language_pairs_link_to_each_other(self) -> None:
        missing_links: list[str] = []
        for english, chinese in EXPECTED_DOC_PAIRS:
            english_path = ROOT / english
            chinese_path = ROOT / chinese
            if not english_path.exists() or not chinese_path.exists():
                continue
            english_text = english_path.read_text(encoding="utf-8")[:500]
            chinese_text = chinese_path.read_text(encoding="utf-8")[:500]
            if chinese_path.name not in english_text:
                missing_links.append(f"{english} -> {chinese_path.name}")
            if english_path.name not in chinese_text:
                missing_links.append(f"{chinese} -> {english_path.name}")

        self.assertEqual(missing_links, [])

    def test_public_markdown_inventory_has_no_untracked_single_language_docs(self) -> None:
        expected = {tuple(pair) for pair in EXPECTED_DOC_PAIRS}
        expected.update((chinese, english) for english, chinese in EXPECTED_DOC_PAIRS)
        roots = [
            ROOT / "CHANGELOG.md",
            ROOT / "CONTRIBUTING.md",
            ROOT / "README.md",
            ROOT / "SECURITY.md",
            ROOT / "docs",
            ROOT / "examples",
            ROOT / "templates" / "adapters",
            ROOT / "attestflow" / "templates" / "adapters",
        ]
        untracked: list[str] = []
        for root in roots:
            files = [root] if root.is_file() else sorted(root.rglob("*.md"))
            for path in files:
                relative = path.relative_to(ROOT).as_posix()
                if relative.endswith(".en.md") or relative.endswith(".zh-CN.md"):
                    continue
                counterpart = _expected_counterpart(path)
                pair = (relative, counterpart.relative_to(ROOT).as_posix())
                if pair not in expected:
                    untracked.append(f"{pair[0]} -> {pair[1]}")

        self.assertEqual(untracked, [])


def _expected_counterpart(path: Path) -> Path:
    text = path.read_text(encoding="utf-8")
    body_without_language_links = "\n".join(
        line for line in text.splitlines() if not (line.startswith("[") and "](" in line)
    )
    if re.search(r"[\u4e00-\u9fff]", body_without_language_links):
        return path.with_name(path.stem + ".en.md")
    return path.with_name(path.stem + ".zh-CN.md")


if __name__ == "__main__":
    unittest.main()
