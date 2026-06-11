from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from attestflow import cli
from attestflow.graphify_sync import sync_graphify_outputs
from attestflow.io import load_data


class GraphifySyncTests(unittest.TestCase):
    def test_sync_all_centralizes_scope_outputs_and_writes_namespaced_merged_graph(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_graphify_scope(
                root,
                "attestflow",
                nodes=[{"id": "loader", "label": "Loader"}],
                edges=[],
            )
            _write_graphify_scope(
                root,
                "docs",
                nodes=[{"id": "loader", "label": "Docs Loader"}, {"id": "guide", "label": "Guide"}],
                edges=[{"source": "loader", "target": "guide", "relation": "references"}],
            )
            (root / "docs" / "graphify-out" / "graph.html").write_text("<html>docs</html>", encoding="utf-8")

            result = sync_graphify_outputs(root, all_scopes=True)

            self.assertEqual(result["status"], "synced")
            self.assertEqual([scope["scope"] for scope in result["scopes"]], ["attestflow", "docs"])
            self.assertTrue((root / "graphify-out" / "scopes" / "attestflow" / "graph.json").exists())
            self.assertTrue((root / "graphify-out" / "scopes" / "docs" / "graph.html").exists())

            index = load_data(root / "graphify-out" / "index.json")
            self.assertEqual(index["schema_version"], 1)
            self.assertEqual([scope["slug"] for scope in index["scopes"]], ["attestflow", "docs"])

            merged = load_data(root / "graphify-out" / "merged" / "graph.json")
            self.assertEqual(
                sorted(node["id"] for node in merged["nodes"]),
                ["attestflow::loader", "docs::guide", "docs::loader"],
            )
            self.assertEqual(merged["edges"][0]["source"], "docs::loader")
            self.assertEqual(merged["edges"][0]["target"], "docs::guide")
            self.assertEqual(merged["nodes"][0]["graphify_scope"], "attestflow")

    def test_sync_scope_uses_stable_slug_for_hidden_directories(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_graphify_scope(root, ".github", nodes=[{"id": "ci", "label": "CI"}], edges=[])

            result = sync_graphify_outputs(root, scopes=[".github"])

            self.assertEqual(result["scopes"][0]["slug"], "dot-github")
            self.assertTrue((root / "graphify-out" / "scopes" / "dot-github" / "GRAPH_REPORT.md").exists())

    def test_cli_graphify_sync_prints_summary_and_supports_json(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_graphify_scope(root, "docs", nodes=[{"id": "guide", "label": "Guide"}], edges=[])
            original_root = cli.ROOT
            cli.ROOT = root
            output = io.StringIO()
            try:
                with redirect_stdout(output):
                    exit_code = cli.main(["graphify-sync", "--all", "--json"])
            finally:
                cli.ROOT = original_root

            payload = json.loads(output.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["status"], "synced")
            self.assertEqual(payload["scopes"][0]["scope"], "docs")


def _write_graphify_scope(root: Path, scope: str, *, nodes: list[dict], edges: list[dict]) -> None:
    graphify_out = root / scope / "graphify-out"
    graphify_out.mkdir(parents=True)
    graphify_out.joinpath("graph.json").write_text(
        json.dumps({"nodes": nodes, "edges": edges, "hyperedges": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    graphify_out.joinpath("GRAPH_REPORT.md").write_text(f"# {scope}\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
