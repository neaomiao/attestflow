from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
    draft = create_draft_spec(
        root,
        config,
        title=_title_from_source(source, requirement.source_path),
        source_text=requirement.text,
        source_evidence=str(requirement.evidence_path.relative_to(root)),
        open_questions=_default_open_questions(requirement.text),
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
