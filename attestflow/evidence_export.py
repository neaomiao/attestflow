from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
from typing import Any

from .evidence import utc_timestamp
from .io import dump_data
from .tasks import TaskRecord, iter_tasks, validate_task


@dataclass(frozen=True)
class EvidenceExportResult:
    task_id: str
    run_id: str
    output_dir: Path
    manifest_path: Path
    files: list[str]


def export_task_evidence(root: Path, config: dict[str, Any], task_id: str, output_dir: Path) -> EvidenceExportResult:
    record = _find_task(root, config, task_id)
    task = record.task
    state = str(task.get("state", ""))
    if state not in {"done", "archived"}:
        raise ValueError(f"{task_id} must be done or archived before evidence export, got {state}")
    errors = validate_task(task, directory_state=record.path.parent.name)
    if errors:
        raise ValueError(f"invalid completed task {task_id}: {'; '.join(errors)}")

    evidence = task.get("evidence", {})
    if not isinstance(evidence, dict):
        raise ValueError(f"{task_id} evidence must be a mapping")
    run_id = str(evidence.get("run_id") or "").strip()
    packet = str(evidence.get("packet") or "").strip()
    if not run_id or not packet:
        raise ValueError(f"{task_id} requires evidence.run_id and evidence.packet before export")

    output_dir.mkdir(parents=True, exist_ok=True)
    copied: set[str] = set()
    _copy_file(record.path, output_dir / "task.json", output_dir, copied)
    _copy_run_dir(root, config, run_id, output_dir, copied)

    capability_refs = evidence.get("capabilities", {})
    if isinstance(capability_refs, dict):
        for ref in capability_refs.values():
            if isinstance(ref, str) and ref.strip():
                _copy_evidence_ref(root, ref, output_dir, copied)

    for ref in _walk_evidence_refs(evidence):
        if ref == packet:
            continue
        _copy_evidence_ref(root, ref, output_dir, copied, required=False)

    manifest = {
        "schema_version": 1,
        "task_id": task_id,
        "run_id": run_id,
        "generated_at": utc_timestamp(),
        "source_task": _relative_to_root(root, record.path),
        "files": sorted(copied),
    }
    manifest_path = output_dir / "manifest.json"
    dump_data(manifest, manifest_path)
    return EvidenceExportResult(
        task_id=task_id,
        run_id=run_id,
        output_dir=output_dir,
        manifest_path=manifest_path,
        files=sorted(copied),
    )


def _find_task(root: Path, config: dict[str, Any], task_id: str) -> TaskRecord:
    for record in iter_tasks(root, config):
        if record.task.get("id") == task_id:
            return record
    raise FileNotFoundError(f"task not found: {task_id}")


def _copy_run_dir(root: Path, config: dict[str, Any], run_id: str, output_dir: Path, copied: set[str]) -> None:
    runs_root = root / str(config.get("paths", {}).get("runs", "harness/runs"))
    run_dir = (runs_root / run_id).resolve()
    _require_under_root(root, run_dir)
    if not run_dir.exists() or not run_dir.is_dir():
        raise ValueError(f"run evidence does not exist: {_relative_to_root(root, run_dir)}")
    target_dir = output_dir / "runs" / run_id
    for source in sorted(path for path in run_dir.rglob("*") if path.is_file()):
        relative = source.relative_to(run_dir)
        _copy_file(source, target_dir / relative, output_dir, copied)


def _copy_evidence_ref(
    root: Path,
    ref: str,
    output_dir: Path,
    copied: set[str],
    *,
    required: bool = True,
) -> None:
    source = _resolve_ref(root, ref)
    if not source.exists():
        if required:
            raise ValueError(f"evidence reference does not exist: {ref}")
        return
    if source.is_dir():
        for file_path in sorted(path for path in source.rglob("*") if path.is_file()):
            _copy_file(file_path, output_dir / _bundle_relative(root, file_path), output_dir, copied)
        return
    _copy_file(source, output_dir / _bundle_relative(root, source), output_dir, copied)


def _copy_file(source: Path, target: Path, output_dir: Path, copied: set[str]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    copied.add(target.relative_to(output_dir).as_posix())


def _walk_evidence_refs(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        refs: list[str] = []
        for item in value.values():
            refs.extend(_walk_evidence_refs(item))
        return refs
    if isinstance(value, list):
        refs = []
        for item in value:
            refs.extend(_walk_evidence_refs(item))
        return refs
    return []


def _resolve_ref(root: Path, ref: str) -> Path:
    path = Path(ref)
    resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
    _require_under_root(root, resolved)
    return resolved


def _require_under_root(root: Path, path: Path) -> None:
    path.relative_to(root.resolve())


def _relative_to_root(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _bundle_relative(root: Path, path: Path) -> Path:
    relative = path.resolve().relative_to(root.resolve())
    parts = relative.parts
    if len(parts) >= 3 and parts[0] == "harness" and parts[1] in {
        "runs",
        "capability-runs",
        "ci-runs",
        "pr-runs",
        "release-runs",
    }:
        return Path(*parts[1:])
    return relative
