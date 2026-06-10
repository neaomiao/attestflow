from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any

from .io import dump_data, load_data


SPEC_ID_PATTERN = re.compile(r"^SPEC-(\d{4})$")
OPEN_QUESTIONS_HEADING = "## Open Questions"


@dataclass(frozen=True)
class DraftSpec:
    spec_id: str
    path: Path


def create_draft_spec(
    root: Path,
    config: dict[str, Any],
    *,
    title: str,
    source_text: str,
    source_evidence: str | Path,
) -> DraftSpec:
    specs_root = _specs_root(root, config)
    spec_id = _next_spec_id(specs_root)
    spec_dir = specs_root / spec_id
    spec_path = spec_dir / "spec.md"
    spec_dir.mkdir(parents=True, exist_ok=False)
    spec_path.write_text(
        _render_spec(
            spec_id=spec_id,
            title=title,
            source_text=source_text,
            source_evidence=source_evidence,
        ),
        encoding="utf-8",
    )
    dump_data(_approval_payload(spec_id, status="pending"), spec_dir / "approval.json")
    return DraftSpec(spec_id=spec_id, path=spec_path)


def spec_has_unresolved_questions(spec_path: Path) -> bool:
    content = spec_path.read_text(encoding="utf-8")
    section = _section_body(content, OPEN_QUESTIONS_HEADING)
    if section is None:
        return False
    normalized = section.strip()
    if not normalized:
        return False
    return _normalize_empty_marker(normalized) not in {"none", "无"}


def approve_spec(spec_path: Path, *, approved_by: str) -> None:
    if spec_has_unresolved_questions(spec_path):
        raise ValueError("spec still has open questions")
    spec_id = spec_path.parent.name
    dump_data(
        _approval_payload(
            spec_id,
            status="approved",
            approved_by=approved_by,
            approved_at=_now(),
        ),
        spec_path.parent / "approval.json",
    )


def require_approved_spec(spec_path: Path) -> None:
    approval_path = spec_path.parent / "approval.json"
    if not approval_path.exists():
        raise ValueError("spec approval is missing")
    approval = load_data(approval_path)
    if approval.get("status") != "approved":
        raise ValueError("spec is not approved")
    if spec_has_unresolved_questions(spec_path):
        raise ValueError("spec still has open questions")


def _specs_root(root: Path, config: dict[str, Any]) -> Path:
    return root / str(config.get("paths", {}).get("specs", "harness/specs"))


def _next_spec_id(specs_root: Path) -> str:
    max_seen = 0
    if specs_root.exists():
        for child in specs_root.iterdir():
            if not child.is_dir():
                continue
            match = SPEC_ID_PATTERN.match(child.name)
            if match:
                max_seen = max(max_seen, int(match.group(1)))
    return f"SPEC-{max_seen + 1:04d}"


def _render_spec(*, spec_id: str, title: str, source_text: str, source_evidence: str | Path) -> str:
    summary = source_text.strip() or "None"
    return (
        f"# {spec_id}: {title.strip() or spec_id}\n"
        "\n"
        "## Goal\n"
        f"{title.strip() or 'None'}\n"
        "\n"
        "## Source Evidence\n"
        f"- {source_evidence}\n"
        "\n"
        "## Confirmed Requirements\n"
        "- Confirm requirements from source.\n"
        "\n"
        "## Scope\n"
        "- Confirm implementation scope.\n"
        "\n"
        "## Out Of Scope\n"
        "- Confirm excluded work.\n"
        "\n"
        "## Acceptance Criteria\n"
        "- Confirm acceptance criteria.\n"
        "\n"
        "## Open Questions\n"
        "- Confirm approval owner.\n"
        "\n"
        "## Source Summary\n"
        f"{summary}\n"
    )


def _section_body(content: str, heading: str) -> str | None:
    lines = content.splitlines()
    start: int | None = None
    for index, line in enumerate(lines):
        if line.strip() == heading:
            start = index + 1
            break
    if start is None:
        return None
    end = len(lines)
    for index in range(start, len(lines)):
        if lines[index].startswith("## "):
            end = index
            break
    return "\n".join(lines[start:end])


def _normalize_empty_marker(value: str) -> str:
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    normalized = "\n".join(_strip_list_marker(line) for line in lines)
    return normalized.strip().lower()


def _strip_list_marker(line: str) -> str:
    if line.startswith("- "):
        return line[2:].strip()
    return line


def _approval_payload(
    spec_id: str,
    *,
    status: str,
    approved_by: str | None = None,
    approved_at: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "spec_id": spec_id,
        "status": status,
        "approved_by": approved_by,
        "approved_at": approved_at,
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
