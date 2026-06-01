from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

from .io import dump_data, load_data
from .tasks import TASK_STATES, iter_tasks


def export_dashboard(root: Path, config: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    data = _dashboard_data(root, config)
    dump_data(data, output_dir / "data.json")
    (output_dir / "index.html").write_text(_dashboard_html(data), encoding="utf-8")
    return {
        "schema_version": 1,
        "status": "passed",
        "output_dir": str(output_dir),
        "index": str(output_dir / "index.html"),
        "data": str(output_dir / "data.json"),
    }


def _dashboard_data(root: Path, config: dict[str, Any]) -> dict[str, Any]:
    task_counts = {state: 0 for state in sorted(TASK_STATES)}
    task_rows: list[dict[str, Any]] = []
    for record in iter_tasks(root, config):
        state = str(record.task.get("state", record.path.parent.name))
        if state in task_counts:
            task_counts[state] += 1
        task_rows.append(
            {
                "id": str(record.task.get("id", record.path.stem)),
                "state": state,
                "title": str(record.task.get("title", "")),
                "path": _relative(root, record.path),
            }
        )
    latest_runs = _latest_run_metadata(root, config)
    return {
        "schema_version": 1,
        "total_tasks": len(task_rows),
        "tasks": task_counts,
        "task_rows": sorted(task_rows, key=lambda item: (item["state"], item["id"])),
        "latest_runs": latest_runs,
    }


def _latest_run_metadata(root: Path, config: dict[str, Any]) -> list[dict[str, Any]]:
    paths = config.get("paths", {}) if isinstance(config.get("paths"), dict) else {}
    run_root = root / str(paths.get("autopilot_runs", "harness/autopilot-runs"))
    runs: list[dict[str, Any]] = []
    if not run_root.exists():
        return runs
    for metadata_path in sorted(run_root.glob("*/metadata.json"), key=lambda path: path.stat().st_mtime, reverse=True)[:10]:
        try:
            metadata = load_data(metadata_path)
        except (OSError, ValueError):
            continue
        runs.append(
            {
                "run_id": str(metadata.get("run_id") or metadata_path.parent.name),
                "status": str(metadata.get("status") or "unknown"),
                "release_status": str(metadata.get("release_status") or ""),
                "path": _relative(root, metadata_path),
            }
        )
    return runs


def _dashboard_html(data: dict[str, Any]) -> str:
    rows = "\n".join(
        f"<tr><td>{escape(row['id'])}</td><td>{escape(row['state'])}</td><td>{escape(row['title'])}</td><td>{escape(row['path'])}</td></tr>"
        for row in data["task_rows"]
    )
    counts = "\n".join(
        f"<li><strong>{escape(state)}</strong>: {count}</li>" for state, count in sorted(data["tasks"].items())
    )
    runs = "\n".join(
        f"<li>{escape(run['run_id'])}: {escape(run['status'])} {escape(run['release_status'])}</li>"
        for run in data["latest_runs"]
    )
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            "<title>Attestflow Local Dashboard</title>",
            "<style>",
            "body{font-family:Arial,sans-serif;margin:32px;color:#172033;background:#f7f8fb}",
            "main{max-width:1080px;margin:0 auto}",
            "section{margin:24px 0}",
            "table{border-collapse:collapse;width:100%;background:#fff}",
            "th,td{border:1px solid #d9deea;padding:8px;text-align:left}",
            "th{background:#eef2f7}",
            "</style>",
            "</head>",
            "<body><main>",
            "<h1>Attestflow Local Dashboard</h1>",
            f"<p>Total tasks: {data['total_tasks']}</p>",
            "<section><h2>Task States</h2><ul>",
            counts,
            "</ul></section>",
            "<section><h2>Tasks</h2><table><thead><tr><th>ID</th><th>State</th><th>Title</th><th>Path</th></tr></thead><tbody>",
            rows,
            "</tbody></table></section>",
            "<section><h2>Latest Runs</h2><ul>",
            runs,
            "</ul></section>",
            "</main></body></html>",
        ]
    )


def _relative(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)
