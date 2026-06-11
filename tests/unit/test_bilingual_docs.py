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
    ("docs/contracts/blackboard-schema.en.md", "docs/contracts/blackboard-schema.md"),
    ("docs/contracts/capability-schema.en.md", "docs/contracts/capability-schema.md"),
    ("docs/contracts/ci-provider-schema.en.md", "docs/contracts/ci-provider-schema.md"),
    ("docs/contracts/evidence-schema.en.md", "docs/contracts/evidence-schema.md"),
    ("docs/contracts/git-provider-schema.en.md", "docs/contracts/git-provider-schema.md"),
    ("docs/contracts/planner-output-schema.en.md", "docs/contracts/planner-output-schema.md"),
    ("docs/contracts/pr-provider-schema.en.md", "docs/contracts/pr-provider-schema.md"),
    ("docs/contracts/release-provider-schema.en.md", "docs/contracts/release-provider-schema.md"),
    ("docs/contracts/session-adapter-schema.en.md", "docs/contracts/session-adapter-schema.md"),
    ("docs/contracts/spec-schema.en.md", "docs/contracts/spec-schema.md"),
    ("docs/contracts/task-schema.en.md", "docs/contracts/task-schema.md"),
    ("examples/node-basic/README.md", "examples/node-basic/README.zh-CN.md"),
    ("examples/python-basic/README.md", "examples/python-basic/README.zh-CN.md"),
]

PUBLIC_MARKDOWN_INVENTORY_EXCLUDED_PREFIXES = (
    "docs/superpowers/plans/",
    "docs/superpowers/specs/",
)

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

    def test_only_root_readmes_are_language_switch_entrypoints(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")[:500]
        chinese_readme = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")[:500]
        self.assertIn("README.zh-CN.md", readme)
        self.assertIn("README.md", chinese_readme)

        unexpected_links: list[str] = []
        language_link = re.compile(r"^\[(English|中文|Chinese(?: README)?)\]\([^)]+\)$")
        for english, chinese in EXPECTED_DOC_PAIRS:
            for relative in (english, chinese):
                if relative in {"README.md", "README.zh-CN.md"}:
                    continue
                path = ROOT / relative
                if not path.exists():
                    continue
                for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                    if language_link.match(line):
                        unexpected_links.append(f"{relative}:{line_number}")

        self.assertEqual(unexpected_links, [])

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
                if relative.startswith(PUBLIC_MARKDOWN_INVENTORY_EXCLUDED_PREFIXES):
                    continue
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
