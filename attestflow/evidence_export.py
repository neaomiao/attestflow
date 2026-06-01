from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any

from .evidence import utc_timestamp
from .io import dump_data, load_data
from .tasks import TaskRecord, iter_tasks, validate_task


@dataclass(frozen=True)
class EvidenceExportResult:
    task_id: str
    run_id: str
    output_dir: Path
    manifest_path: Path
    files: list[str]


@dataclass(frozen=True)
class EvidenceBundleResult:
    kind: str
    identifier: str
    output_dir: Path
    manifest_path: Path
    files: list[str]


@dataclass(frozen=True)
class EvidenceVerifyResult:
    manifest_path: Path
    errors: list[str]
    warnings: list[str]


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


def export_autopilot_bundle(
    root: Path,
    config: dict[str, Any],
    run_id_or_path: str,
    output_dir: Path,
) -> EvidenceBundleResult:
    run_dir = _resolve_autopilot_run_dir(root, config, run_id_or_path)
    metadata_path = run_dir / "metadata.json"
    if not metadata_path.exists():
        raise ValueError(f"autopilot metadata does not exist: {_relative_to_root(root, metadata_path)}")
    metadata = load_manifest_data(metadata_path)
    run_id = str(metadata.get("run_id") or run_dir.name)
    output_dir.mkdir(parents=True, exist_ok=True)
    sources: dict[str, str | None] = {}

    _copy_tree_artifacts(root, run_dir, Path("autopilot-runs") / run_dir.name, output_dir, sources)
    task_ids = _metadata_task_ids(metadata)
    for record in iter_tasks(root, config):
        task_id = str(record.task.get("id") or "")
        if task_id in task_ids:
            _copy_task_artifacts(root, config, record, output_dir, sources)
    for ref in _release_refs(metadata):
        _copy_evidence_ref_artifact(root, ref, output_dir, sources, required=False)

    _write_pr_comment(output_dir, run_id=run_id, metadata=metadata)
    _write_audit_report(output_dir, kind="autopilot", identifier=run_id, metadata=metadata, files=sources)
    _add_generated_artifact(output_dir, Path("pr-comment.md"), sources)
    _add_generated_artifact(output_dir, Path("audit.md"), sources)
    manifest = _bundle_manifest(root, output_dir, sources, kind="autopilot", identifier=run_id, extra={"run_id": run_id})
    manifest_path = output_dir / "manifest.json"
    dump_data(manifest, manifest_path)
    return EvidenceBundleResult(
        kind="autopilot",
        identifier=run_id,
        output_dir=output_dir,
        manifest_path=manifest_path,
        files=manifest["files"],
    )


def export_release_bundle(
    root: Path,
    config: dict[str, Any],
    release_run_id_or_path: str,
    output_dir: Path,
) -> EvidenceBundleResult:
    release_dir = _resolve_release_run_dir(root, config, release_run_id_or_path)
    if not release_dir.exists() or not release_dir.is_dir():
        raise ValueError(f"release run does not exist: {_relative_to_root(root, release_dir)}")
    output_dir.mkdir(parents=True, exist_ok=True)
    sources: dict[str, str | None] = {}
    _copy_tree_artifacts(root, release_dir, Path("release-runs") / release_dir.name, output_dir, sources)
    release_output = load_manifest_data(release_dir / "output.json") if (release_dir / "output.json").exists() else {}
    _write_audit_report(output_dir, kind="release", identifier=release_dir.name, metadata=release_output, files=sources)
    _add_generated_artifact(output_dir, Path("audit.md"), sources)
    manifest = _bundle_manifest(
        root,
        output_dir,
        sources,
        kind="release",
        identifier=release_dir.name,
        extra={"release_run_id": release_dir.name},
    )
    manifest_path = output_dir / "manifest.json"
    dump_data(manifest, manifest_path)
    return EvidenceBundleResult(
        kind="release",
        identifier=release_dir.name,
        output_dir=output_dir,
        manifest_path=manifest_path,
        files=manifest["files"],
    )


def verify_evidence_bundle(root: Path, bundle_dir: Path, *, check_source: bool = False) -> EvidenceVerifyResult:
    manifest_path = bundle_dir / "manifest.json"
    errors: list[str] = []
    warnings: list[str] = []
    try:
        manifest = load_manifest_data(manifest_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return EvidenceVerifyResult(manifest_path=manifest_path, errors=[f"failed to load manifest: {exc}"], warnings=[])
    artifacts = manifest.get("artifacts")
    if isinstance(artifacts, list):
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                errors.append("manifest artifact must be a mapping")
                continue
            relative = str(artifact.get("path") or "")
            if not relative:
                errors.append("manifest artifact path must be non-empty")
                continue
            path = bundle_dir / relative
            if not path.exists() or not path.is_file():
                errors.append(f"missing artifact: {relative}")
                continue
            actual_hash = _sha256(path)
            expected_hash = str(artifact.get("sha256") or "")
            if expected_hash and actual_hash != expected_hash:
                errors.append(f"hash mismatch for {relative}")
            expected_size = artifact.get("size")
            if type(expected_size) is int and path.stat().st_size != expected_size:
                errors.append(f"size mismatch for {relative}")
            if check_source and artifact.get("source"):
                source_root = Path(str(manifest.get("source_root") or root))
                source = source_root / str(artifact["source"])
                if not source.exists():
                    errors.append(f"stale source missing for {relative}: {artifact['source']}")
                elif artifact.get("source_sha256") and _sha256(source) != artifact.get("source_sha256"):
                    errors.append(f"stale source for {relative}: {artifact['source']}")
    else:
        files = manifest.get("files", [])
        if not isinstance(files, list):
            errors.append("manifest.files must be a list")
        else:
            for relative in files:
                if not (bundle_dir / str(relative)).exists():
                    errors.append(f"missing artifact: {relative}")
    return EvidenceVerifyResult(manifest_path=manifest_path, errors=errors, warnings=warnings)


def _find_task(root: Path, config: dict[str, Any], task_id: str) -> TaskRecord:
    for record in iter_tasks(root, config):
        if record.task.get("id") == task_id:
            return record
    raise FileNotFoundError(f"task not found: {task_id}")


def load_manifest_data(path: Path) -> dict[str, Any]:
    return load_data(path)


def _resolve_autopilot_run_dir(root: Path, config: dict[str, Any], run_id_or_path: str) -> Path:
    candidate = Path(run_id_or_path)
    if candidate.exists():
        resolved = candidate.resolve()
    else:
        run_root = root / str(config.get("paths", {}).get("autopilot_runs", "harness/autopilot-runs"))
        resolved = (run_root / run_id_or_path).resolve()
    _require_under_root(root, resolved)
    if not resolved.exists() or not resolved.is_dir():
        raise ValueError(f"autopilot run does not exist: {_relative_to_root(root, resolved)}")
    return resolved


def _resolve_release_run_dir(root: Path, config: dict[str, Any], release_run_id_or_path: str) -> Path:
    candidate = Path(release_run_id_or_path)
    if candidate.exists():
        resolved = candidate.resolve()
    else:
        run_root = root / str(config.get("paths", {}).get("release_runs", "harness/release-runs"))
        resolved = (run_root / release_run_id_or_path).resolve()
    _require_under_root(root, resolved)
    return resolved


def _metadata_task_ids(metadata: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for key in ("planned", "dispatched", "releaser_tasks", "done_tasks"):
        value = metadata.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    ids.add(item)
                elif isinstance(item, dict) and item.get("id"):
                    ids.add(str(item["id"]))
    return ids


def _release_refs(metadata: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for key in ("release", "releaser", "release_repair_planner"):
        value = metadata.get(key)
        if value is not None:
            refs.extend(_walk_evidence_refs(value))
    return refs


def _copy_task_artifacts(
    root: Path,
    config: dict[str, Any],
    record: TaskRecord,
    output_dir: Path,
    sources: dict[str, str | None],
) -> None:
    state = record.path.parent.name
    _copy_artifact(root, record.path, Path("tasks") / state / record.path.name, output_dir, sources)
    evidence = record.task.get("evidence", {})
    if not isinstance(evidence, dict):
        return
    run_id = str(evidence.get("run_id") or "").strip()
    if run_id:
        runs_root = root / str(config.get("paths", {}).get("runs", "harness/runs"))
        run_dir = runs_root / run_id
        if run_dir.exists():
            _copy_tree_artifacts(root, run_dir, Path("runs") / run_id, output_dir, sources)
    for ref in _walk_evidence_refs(evidence):
        _copy_evidence_ref_artifact(root, ref, output_dir, sources, required=False)


def _copy_evidence_ref_artifact(
    root: Path,
    ref: str,
    output_dir: Path,
    sources: dict[str, str | None],
    *,
    required: bool = True,
) -> None:
    try:
        source = _resolve_ref(root, ref)
    except ValueError:
        if required:
            raise
        return
    if not source.exists():
        if required:
            raise ValueError(f"evidence reference does not exist: {ref}")
        return
    if source.is_dir():
        _copy_tree_artifacts(root, source, _bundle_relative(root, source), output_dir, sources)
        return
    _copy_artifact(root, source, _bundle_relative(root, source), output_dir, sources)


def _copy_tree_artifacts(
    root: Path,
    source_dir: Path,
    target_prefix: Path,
    output_dir: Path,
    sources: dict[str, str | None],
) -> None:
    for source in sorted(path for path in source_dir.rglob("*") if path.is_file()):
        _copy_artifact(root, source, target_prefix / source.relative_to(source_dir), output_dir, sources)


def _copy_artifact(
    root: Path,
    source: Path,
    relative: Path,
    output_dir: Path,
    sources: dict[str, str | None],
) -> None:
    target = output_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    bundle_path = relative.as_posix()
    sources[bundle_path] = _relative_to_root(root, source)


def _add_generated_artifact(output_dir: Path, relative: Path, sources: dict[str, str | None]) -> None:
    if (output_dir / relative).exists():
        sources[relative.as_posix()] = None


def _bundle_manifest(
    root: Path,
    output_dir: Path,
    sources: dict[str, str | None],
    *,
    kind: str,
    identifier: str,
    extra: dict[str, Any],
) -> dict[str, Any]:
    artifacts = []
    for relative in sorted(sources):
        path = output_dir / relative
        source = sources[relative]
        artifact = {
            "path": relative,
            "size": path.stat().st_size,
            "sha256": _sha256(path),
        }
        if source:
            source_path = root / source
            artifact["source"] = source
            artifact["source_sha256"] = _sha256(source_path) if source_path.exists() else None
        artifacts.append(artifact)
    manifest = {
        "schema_version": 1,
        "kind": kind,
        "identifier": identifier,
        "generated_at": utc_timestamp(),
        "source_root": str(root.resolve()),
        "files": [artifact["path"] for artifact in artifacts],
        "artifacts": artifacts,
    }
    manifest.update(extra)
    return manifest


def _write_pr_comment(output_dir: Path, *, run_id: str, metadata: dict[str, Any]) -> None:
    lines = [
        "## Attestflow Evidence",
        "",
        f"- Run: `{run_id}`",
        f"- Status: `{metadata.get('status', 'unknown')}`",
        f"- Release: `{metadata.get('release_status', 'not_recorded')}`",
        "",
        f"Evidence bundle manifest: `manifest.json`",
        f"Verify locally: `python -m attestflow evidence verify {output_dir}`",
        "",
    ]
    (output_dir / "pr-comment.md").write_text("\n".join(lines), encoding="utf-8")


def _write_audit_report(
    output_dir: Path,
    *,
    kind: str,
    identifier: str,
    metadata: dict[str, Any],
    files: dict[str, str | None],
) -> None:
    status = metadata.get("status", "unknown")
    release_status = metadata.get("release_status", metadata.get("status", "not_recorded"))
    lines = [
        f"# Attestflow {kind.title()} Evidence Audit",
        "",
        f"- ID: `{identifier}`",
        f"- Status: `{status}`",
        f"- Release Status: `{release_status}`",
        f"- Files: {len(files)}",
        "",
        "## Included Files",
        "",
    ]
    lines.extend(f"- `{path}`" for path in sorted(files))
    lines.append("")
    (output_dir / "audit.md").write_text("\n".join(lines), encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
        "git-runs",
        "pr-runs",
        "release-runs",
    }:
        return Path(*parts[1:])
    return relative
