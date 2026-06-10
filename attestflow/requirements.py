from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import hashlib
from typing import Any

from .io import dump_data


SUPPORTED_TEXT_FORMATS = {"md", "markdown", "txt"}


@dataclass(frozen=True)
class RequirementSource:
    kind: str
    text: str
    evidence_path: Path
    source_path: Path | None = None
    format: str | None = None


def ingest_requirement_source(root: Path, config: dict[str, Any], value: str) -> RequirementSource:
    raw = str(value).strip()
    if not raw:
        raise ValueError("requirement source cannot be empty")

    source_path = _resolve_existing_path(root, raw)
    if source_path is not None:
        return _ingest_file(root, config, source_path)
    return _ingest_inline_text(root, config, raw)


def _ingest_inline_text(root: Path, config: dict[str, Any], text: str) -> RequirementSource:
    content_hash = _content_hash(text)
    evidence_path = _source_evidence_path(root, config, content_hash, "inline")
    dump_data(
        {
            "schema_version": 1,
            "kind": "inline_text",
            "original": text,
            "content_hash": content_hash,
            "received_at": _now(),
        },
        evidence_path,
    )
    return RequirementSource(kind="inline_text", text=text, evidence_path=evidence_path)


def _ingest_file(root: Path, config: dict[str, Any], path: Path) -> RequirementSource:
    suffix = path.suffix.lower().lstrip(".")
    if suffix in SUPPORTED_TEXT_FORMATS:
        text = path.read_text(encoding="utf-8")
    elif suffix == "docx":
        text = _extract_docx_text(path)
    elif suffix == "pdf":
        text = _extract_pdf_text(path)
    else:
        raise ValueError(f"unsupported requirement source format: {suffix or '<none>'}")

    if suffix == "pdf" and not text.strip():
        raise ValueError("PDF text layer could not be extracted; scanned PDFs are not supported in v1")
    if not text.strip():
        raise ValueError(f"requirement source has no readable text: {path}")

    content_hash = _content_hash(text)
    evidence_path = _source_evidence_path(root, config, content_hash, suffix)
    dump_data(
        {
            "schema_version": 1,
            "kind": "file",
            "path": str(path),
            "format": suffix,
            "content_hash": content_hash,
            "received_at": _now(),
        },
        evidence_path,
    )
    (evidence_path.parent / "source.txt").write_text(text, encoding="utf-8")
    return RequirementSource(kind="file", text=text, evidence_path=evidence_path, source_path=path, format=suffix)


def _extract_docx_text(path: Path) -> str:
    try:
        from docx import Document  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ValueError("DOCX support requires installing attestflow[documents]") from exc

    document = Document(str(path))
    paragraphs = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                paragraphs.append(" | ".join(cells))
    return "\n".join(paragraphs)


def _extract_pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ValueError("PDF support requires installing attestflow[documents]") from exc

    try:
        reader = PdfReader(str(path))
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
    except Exception as exc:
        raise ValueError("PDF text layer could not be extracted; scanned PDFs are not supported in v1") from exc
    text = "\n\n".join(page for page in pages if page)
    if not text.strip():
        raise ValueError("PDF text layer could not be extracted; scanned PDFs are not supported in v1")
    return text


def _resolve_existing_path(root: Path, raw: str) -> Path | None:
    path = Path(raw)
    if path.exists():
        return path
    rooted = root / path
    if rooted.exists():
        return rooted
    return None


def _source_evidence_path(root: Path, config: dict[str, Any], content_hash: str, suffix: str) -> Path:
    specs_root = root / str(config.get("paths", {}).get("specs", "harness/specs"))
    path = specs_root / "sources" / f"{content_hash[:12]}-{suffix}" / "source.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
