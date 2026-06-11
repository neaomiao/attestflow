from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import html
import re
import shutil
from pathlib import Path
from typing import Any

from .io import dump_data, load_data


GRAPHIFY_DIR = "graphify-out"
ROOT_SUBDIRS = {"scopes", "merged", "obsidian", "cache"}


@dataclass(frozen=True)
class GraphifyScope:
    scope: str
    slug: str
    source: Path
    destination: Path


def sync_graphify_outputs(
    root: Path,
    *,
    scopes: list[str] | None = None,
    all_scopes: bool = False,
    merge: bool = True,
) -> dict[str, Any]:
    selected = _select_scopes(root, scopes=scopes, all_scopes=all_scopes)
    output_root = root / GRAPHIFY_DIR
    copied: list[dict[str, Any]] = []
    for scope in selected:
        files = _copy_scope(scope)
        copied.append(
            {
                "scope": scope.scope,
                "slug": scope.slug,
                "source": _relative(root, scope.source),
                "destination": _relative(root, scope.destination),
                "files": files,
                "graph": _graph_summary(scope.destination / "graph.json"),
            }
        )
    merged = _write_merged_graph(root, copied) if merge else None
    quality = _index_quality(copied, merged)
    index = {
        "schema_version": 1,
        "status": "synced",
        "generated_at": _now(),
        "output_root": _relative(root, output_root),
        "scopes": copied,
        "merged": merged,
        "quality": quality,
    }
    dump_data(index, output_root / "index.json")
    return index


def _select_scopes(root: Path, *, scopes: list[str] | None, all_scopes: bool) -> list[GraphifyScope]:
    if scopes and all_scopes:
        raise ValueError("use either --all or --scope, not both")
    if not scopes and not all_scopes:
        raise ValueError("graphify-sync requires --all or at least one --scope")
    raw_scopes = scopes or _discover_scopes(root)
    selected: list[GraphifyScope] = []
    seen_slugs: set[str] = set()
    for raw_scope in raw_scopes:
        scope_path = Path(raw_scope)
        if scope_path.is_absolute() or ".." in scope_path.parts:
            raise ValueError(f"invalid graphify scope: {raw_scope}")
        source = root / scope_path / GRAPHIFY_DIR
        if not source.is_dir():
            raise ValueError(f"graphify output does not exist for scope {raw_scope}: {source}")
        slug = _scope_slug(scope_path)
        if slug in seen_slugs:
            raise ValueError(f"duplicate graphify scope slug: {slug}")
        seen_slugs.add(slug)
        selected.append(
            GraphifyScope(
                scope=scope_path.as_posix(),
                slug=slug,
                source=source,
                destination=root / GRAPHIFY_DIR / "scopes" / slug,
            )
        )
    return sorted(selected, key=lambda item: item.scope)


def _discover_scopes(root: Path) -> list[str]:
    scopes: list[str] = []
    for child in sorted(root.iterdir(), key=lambda path: path.name):
        if not child.is_dir():
            continue
        if child.name == GRAPHIFY_DIR:
            continue
        if (child / GRAPHIFY_DIR).is_dir():
            scopes.append(child.name)
    return scopes


def _scope_slug(scope: Path) -> str:
    raw = scope.as_posix().strip("/")
    if raw.startswith("."):
        raw = "dot-" + raw[1:]
    raw = raw.replace("/", "--")
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw).strip("-")
    return slug or "root"


def _copy_scope(scope: GraphifyScope) -> list[str]:
    copied: list[str] = []
    scope.destination.mkdir(parents=True, exist_ok=True)
    for source_file in sorted(scope.source.rglob("*")):
        if not source_file.is_file():
            continue
        relative = source_file.relative_to(scope.source)
        if GRAPHIFY_DIR in relative.parts:
            continue
        destination = scope.destination / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, destination)
        copied.append(relative.as_posix())
    return copied


def _write_merged_graph(root: Path, scopes: list[dict[str, Any]]) -> dict[str, Any]:
    merged_nodes: list[dict[str, Any]] = []
    merged_edges: list[dict[str, Any]] = []
    merged_hyperedges: list[dict[str, Any]] = []
    for scope in scopes:
        slug = str(scope["slug"])
        graph_path = root / str(scope["destination"]) / "graph.json"
        if not graph_path.exists():
            continue
        graph = load_data(graph_path)
        node_ids = {str(node.get("id")) for node in graph.get("nodes", []) if isinstance(node, dict)}
        for node in graph.get("nodes", []):
            if not isinstance(node, dict) or not str(node.get("id", "")).strip():
                continue
            original_id = str(node["id"])
            merged_nodes.append(
                {
                    **node,
                    "id": _namespaced(slug, original_id),
                    "original_id": original_id,
                    "graphify_scope": scope["scope"],
                }
            )
        for edge in graph.get("edges", []):
            if not isinstance(edge, dict):
                continue
            source = str(edge.get("source", ""))
            target = str(edge.get("target", ""))
            if source not in node_ids or target not in node_ids:
                continue
            merged_edges.append(
                {
                    **edge,
                    "source": _namespaced(slug, source),
                    "target": _namespaced(slug, target),
                    "graphify_scope": scope["scope"],
                }
            )
        for hyperedge in graph.get("hyperedges", []):
            if not isinstance(hyperedge, dict):
                continue
            original_id = str(hyperedge.get("id", ""))
            nodes = [str(node) for node in hyperedge.get("nodes", []) if str(node) in node_ids]
            merged_hyperedges.append(
                {
                    **hyperedge,
                    "id": _namespaced(slug, original_id) if original_id else original_id,
                    "nodes": [_namespaced(slug, node) for node in nodes],
                    "graphify_scope": scope["scope"],
                }
            )
    merged_dir = root / GRAPHIFY_DIR / "merged"
    merged_graph = {
        "schema_version": 1,
        "generated_at": _now(),
        "nodes": merged_nodes,
        "edges": merged_edges,
        "hyperedges": merged_hyperedges,
    }
    dump_data(merged_graph, merged_dir / "graph.json")
    _write_merged_report(merged_dir, merged_graph, scopes)
    _write_merged_html(merged_dir, merged_graph)
    quality = _graph_quality(
        nodes=len(merged_nodes),
        edges=len(merged_edges),
        hyperedges=len(merged_hyperedges),
        label="merged graph",
    )
    return {
        "path": _relative(root, merged_dir / "graph.json"),
        "nodes": len(merged_nodes),
        "edges": len(merged_edges),
        "hyperedges": len(merged_hyperedges),
        "quality": quality,
    }


def _write_merged_report(merged_dir: Path, merged_graph: dict[str, Any], scopes: list[dict[str, Any]]) -> None:
    lines = [
        "# Graphify Merged Report",
        "",
        f"- Scopes: {len(scopes)}",
        f"- Nodes: {len(merged_graph['nodes'])}",
        f"- Edges: {len(merged_graph['edges'])}",
        f"- Hyperedges: {len(merged_graph['hyperedges'])}",
        "",
        "## Scopes",
    ]
    for scope in scopes:
        summary = scope.get("graph") or {}
        lines.append(
            f"- `{scope['scope']}` -> `{scope['destination']}` "
            f"({summary.get('nodes', 0)} nodes, {summary.get('edges', 0)} edges)"
        )
    merged_dir.mkdir(parents=True, exist_ok=True)
    (merged_dir / "GRAPH_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_merged_html(merged_dir: Path, merged_graph: dict[str, Any]) -> None:
    title = "Graphify Merged Graph"
    body = (
        f"<h1>{title}</h1>"
        f"<p>{len(merged_graph['nodes'])} nodes, {len(merged_graph['edges'])} edges, "
        f"{len(merged_graph['hyperedges'])} hyperedges.</p>"
        "<p>Open <code>graph.json</code> for the GraphRAG-ready merged graph.</p>"
    )
    merged_dir.mkdir(parents=True, exist_ok=True)
    (merged_dir / "graph.html").write_text(
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        f"<title>{html.escape(title)}</title></head><body>{body}</body></html>\n",
        encoding="utf-8",
    )


def _graph_summary(path: Path) -> dict[str, int]:
    if not path.exists():
        return {"nodes": 0, "edges": 0, "hyperedges": 0}
    graph = load_data(path)
    return {
        "nodes": len(graph.get("nodes", [])),
        "edges": len(graph.get("edges", [])),
        "hyperedges": len(graph.get("hyperedges", [])),
    }


def _index_quality(scopes: list[dict[str, Any]], merged: dict[str, Any] | None) -> dict[str, Any]:
    if merged:
        return dict(merged["quality"])
    nodes = 0
    edges = 0
    hyperedges = 0
    for scope in scopes:
        graph = scope.get("graph") or {}
        nodes += int(graph.get("nodes", 0) or 0)
        edges += int(graph.get("edges", 0) or 0)
        hyperedges += int(graph.get("hyperedges", 0) or 0)
    return _graph_quality(nodes=nodes, edges=edges, hyperedges=hyperedges, label="graphify sync")


def _graph_quality(*, nodes: int, edges: int, hyperedges: int, label: str) -> dict[str, Any]:
    warnings: list[str] = []
    if nodes == 0:
        return {"status": "empty", "warnings": [f"{label} has no nodes"]}
    if edges + hyperedges == 0:
        warnings.append(f"{label} has nodes but no edges or hyperedges")
        return {"status": "weak", "warnings": warnings}
    return {"status": "usable", "warnings": []}


def _namespaced(scope_slug: str, node_id: str) -> str:
    return f"{scope_slug}::{node_id}"


def _relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
