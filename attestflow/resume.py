from __future__ import annotations

from pathlib import Path
from typing import Any

from .io import load_data


def resume_summary(root: Path, config: dict[str, Any]) -> str:
    runs_root = root / config.get("paths", {}).get("runs", "harness/runs")
    if not runs_root.exists():
        return "no unfinished runs"

    active: list[dict[str, Any]] = []
    for metadata_path in sorted(runs_root.glob("*/metadata.yml")):
        metadata = load_data(metadata_path)
        if metadata.get("ended_at") is None and metadata.get("status") != "closed":
            metadata["_path"] = str(metadata_path.parent)
            active.append(metadata)

    if not active:
        return "no unfinished runs"
    if len(active) > 1:
        ids = ", ".join(str(item.get("task_id")) for item in active)
        return f"multiple unfinished runs: {ids}"

    item = active[0]
    task_id = item.get("task_id")
    run_id = item.get("run_id")
    session = item.get("agent_session", {}) if isinstance(item.get("agent_session"), dict) else {}
    if session.get("session_id"):
        return (
            f"{task_id} is in progress in {run_id}; "
            f"session {session.get('session_id')} is {session.get('status')}; next action: run BDD"
        )
    return f"{task_id} is in progress in {run_id}; next action: run BDD"
