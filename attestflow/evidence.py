from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from .io import dump_data, load_data


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    path: Path


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def create_run(
    root: Path,
    config: dict[str, Any],
    task: dict[str, Any],
    actor_role: str,
    task_lock: Path,
    file_locks: list[Path],
) -> RunRecord:
    task_id = str(task["id"])
    run_id = f"{utc_timestamp()}-{task_id}"
    run_root = root / config.get("paths", {}).get("runs", "harness/runs")
    run_path = run_root / run_id
    (run_path / "commands").mkdir(parents=True, exist_ok=True)

    metadata = {
        "schema_version": 1,
        "run_id": run_id,
        "task_id": task_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "ended_at": None,
        "status": "in_progress",
        "actor": {"role": actor_role, "id": "local"},
        "workspace": {
            "root": str(root),
            "branch": None,
            "worktree": None,
            "commit_before": None,
            "commit_after": None,
        },
        "locks": {
            "task": str(task_lock.relative_to(root)),
            "files": [str(path.relative_to(root)) for path in file_locks],
        },
        "commands": {
            "bdd": None,
            "unit": None,
            "lint": None,
            "typecheck": None,
            "verify": None,
            "secret_scan": None,
        },
        "result": {"dor_passed": True, "dod_passed": False, "conclusion": None},
    }
    dump_data(metadata, run_path / "metadata.yml")
    write_evidence_packet(run_path / "evidence.md", task, run_id)
    append_ledger(
        run_path,
        "task_started",
        task_id,
        run_id,
        actor_role,
        {"state": "in_progress"},
    )
    return RunRecord(run_id=run_id, path=run_path)


def append_ledger(
    run_path: Path,
    event: str,
    task_id: str,
    run_id: str,
    actor_role: str,
    data: dict[str, Any],
) -> None:
    line = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "task_id": task_id,
        "run_id": run_id,
        "actor": {"role": actor_role, "id": "local"},
        "data": data,
    }
    with (run_path / "ledger.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line, ensure_ascii=False) + "\n")


def write_evidence_packet(path: Path, task: dict[str, Any], run_id: str) -> None:
    path.write_text(
        "\n".join(
            [
                "# Evidence Packet",
                "",
                "## Task",
                "",
                f"- ID: {task.get('id')}",
                f"- Title: {task.get('title')}",
                f"- Run: {run_id}",
                "- Branch:",
                "- Commit Before:",
                "- Commit After:",
                "",
                "## Requirement Boundary",
                "",
                f"- Purpose: {task.get('purpose')}",
                f"- Scope: {task.get('scope')}",
                f"- Out of Scope: {task.get('out_of_scope')}",
                f"- Unresolved Requirements: {task.get('requirements', {}).get('unresolved', [])}",
                "",
                "## BDD",
                "",
                "- Command:",
                "- Result:",
                "- Log:",
                "- Scenarios Covered:",
                "",
                "## Unit Tests",
                "",
                "- Command:",
                "- Result:",
                "- Log:",
                "- Tests Covered:",
                "",
                "## Risks",
                "",
                "- Remaining:",
                "- Follow-ups:",
                "",
            ]
        ),
        encoding="utf-8",
    )


def close_run(run_path: Path, task_id: str) -> None:
    metadata_path = run_path / "metadata.yml"
    metadata = load_data(metadata_path)
    metadata["ended_at"] = datetime.now(timezone.utc).isoformat()
    metadata["status"] = "closed"
    result = dict(metadata.get("result", {}))
    result["dod_passed"] = True
    result["conclusion"] = "done"
    metadata["result"] = result
    dump_data(metadata, metadata_path)
    append_ledger(
        run_path,
        "closed",
        task_id,
        str(metadata.get("run_id")),
        str(metadata.get("actor", {}).get("role", "orchestrator")),
        {"state": "done"},
    )
