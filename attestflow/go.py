from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .provider_commands import provider_timeout_seconds, run_provider_json_command
from .requirements import ingest_requirement_source
from .specs import approve_spec, create_draft_spec, require_approved_spec, spec_open_questions, validate_spec_path


@dataclass(frozen=True)
class GoRunResult:
    status: str
    spec_id: str | None
    spec_path: Path
    goal: str | None = None
    open_questions: list[str] | None = None


def prepare_go_run(
    root: Path,
    config: dict[str, Any],
    source: str | None,
    *,
    from_spec: Path | None = None,
    approve: bool = False,
    non_interactive: bool = False,
    approved_by: str = "local-user",
) -> GoRunResult:
    if from_spec is not None:
        if not approve:
            raise ValueError("--from-spec requires --approve before execution")
        from_spec = validate_spec_path(root, config, from_spec)
        if non_interactive:
            require_approved_spec(from_spec)
        else:
            try:
                require_approved_spec(from_spec)
            except ValueError as exc:
                if "spec is not approved" not in str(exc):
                    raise
                approve_spec(from_spec, approved_by=approved_by)
                require_approved_spec(from_spec)
        return GoRunResult(
            status="approved",
            spec_id=from_spec.parent.name,
            spec_path=from_spec,
            goal=from_spec.read_text(encoding="utf-8"),
        )
    if non_interactive:
        raise ValueError("--non-interactive requires --from-spec and --approve")
    if not source:
        raise ValueError("attestflow go requires inline text, a document path, or --from-spec")

    requirement = ingest_requirement_source(root, config, source)
    open_questions = _open_questions_for_requirement(root, config, requirement.text, requirement.evidence_path)
    draft = create_draft_spec(
        root,
        config,
        title=_title_from_source(source, requirement.source_path),
        source_text=requirement.text,
        source_evidence=str(requirement.evidence_path.relative_to(root)),
        open_questions=open_questions,
    )
    return GoRunResult(
        status="needs_approval",
        spec_id=draft.spec_id,
        spec_path=draft.path,
        open_questions=spec_open_questions(draft.path),
    )


def _title_from_source(source: str, source_path: Path | None) -> str:
    if source_path is not None:
        title = source_path.stem.replace("-", " ").replace("_", " ").strip()
        return title or "Requirement Source"
    return source.strip().splitlines()[0][:80] or "Requirement Source"


def _default_open_questions(source_text: str) -> list[str]:
    first_line = source_text.strip().splitlines()[0][:80] if source_text.strip() else "the requested change"
    return [
        f"[Q1] Who is the primary user and exact workflow for: {first_line}?",
        "[Q2] What inputs, data model changes, permissions, security, and privacy boundaries are in scope?",
        "[Q3] What must explicitly stay out of scope for the first implementation?",
        "[Q4] What observable acceptance criteria prove the work is complete?",
        "[Q5] What external services, credentials, migrations, compatibility constraints, or rollout risks must be handled?",
    ]


def _open_questions_for_requirement(root: Path, config: dict[str, Any], source_text: str, evidence_path: Path) -> list[str]:
    requirements = config.get("requirements", {})
    requirements = requirements if isinstance(requirements, dict) else {}
    command = requirements.get("clarifier_command")
    if not command:
        return _default_open_questions(source_text)
    output = _run_clarifier_command(root, config, str(command), source_text, evidence_path, requirements)
    questions = output.get("questions")
    if not isinstance(questions, list) or not all(str(question).strip() for question in questions):
        raise ValueError("requirements clarifier output questions must be a non-empty list of strings")
    max_questions = _positive_int(requirements.get("max_open_questions"), 5)
    return [str(question).strip() for question in questions[:max_questions]]


def _run_clarifier_command(
    root: Path,
    config: dict[str, Any],
    command: str,
    source_text: str,
    evidence_path: Path,
    requirements: dict[str, Any],
) -> dict[str, Any]:
    run_root = _run_root(root, config)
    payload = {
        "schema_version": 1,
        "capability": {"name": "requirements-clarifier"},
        "root": str(root),
        "source": {
            "text": source_text,
            "evidence": _relative(root, evidence_path),
        },
        "max_questions": _positive_int(requirements.get("max_open_questions"), 5),
        "security": config.get("security", {}),
        "provider_options": requirements.get("provider_options", {}),
    }
    return run_provider_json_command(
        root,
        command,
        payload,
        run_root / f"clarifier-{_timestamp()}",
        "requirements clarifier",
        timeout_seconds=provider_timeout_seconds(requirements),
    )


def _run_root(root: Path, config: dict[str, Any]) -> Path:
    paths = config.get("paths", {})
    configured = paths.get("requirement_runs") if isinstance(paths, dict) else None
    path = Path(str(configured or "harness/requirement-runs"))
    return path if path.is_absolute() else root / path


def _positive_int(value: Any, default: int) -> int:
    return int(value) if type(value) is int and value > 0 else default


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()
