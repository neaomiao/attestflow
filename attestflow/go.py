from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

from .requirements import ingest_requirement_source
from .specs import create_draft_spec, require_approved_spec


SPEC_PATH_ID_PATTERN = re.compile(r"^SPEC-\d{4}$")


@dataclass(frozen=True)
class GoRunResult:
    status: str
    spec_id: str | None
    spec_path: Path
    goal: str | None = None


def prepare_go_run(
    root: Path,
    config: dict[str, Any],
    source: str | None,
    *,
    from_spec: Path | None = None,
    approve: bool = False,
    non_interactive: bool = False,
) -> GoRunResult:
    if from_spec is not None:
        if not approve:
            raise ValueError("--from-spec requires --approve before execution")
        _validate_from_spec_path(root, config, from_spec)
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
    )
    return GoRunResult(status="needs_approval", spec_id=draft.spec_id, spec_path=draft.path)


def _title_from_source(source: str, source_path: Path | None) -> str:
    if source_path is not None:
        title = source_path.stem.replace("-", " ").replace("_", " ").strip()
        return title or "Requirement Source"
    return source.strip().splitlines()[0][:80] or "Requirement Source"


def _validate_from_spec_path(root: Path, config: dict[str, Any], from_spec: Path) -> None:
    specs_root = (root / str(config.get("paths", {}).get("specs", "harness/specs"))).resolve()
    spec_path = from_spec.resolve()
    try:
        spec_path.relative_to(specs_root)
    except ValueError as exc:
        raise ValueError("spec path must be under configured specs directory") from exc
    if spec_path.name != "spec.md" or not SPEC_PATH_ID_PATTERN.match(spec_path.parent.name):
        raise ValueError("spec path must be SPEC-####/spec.md")
