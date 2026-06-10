# Attestflow Go Requirement Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `attestflow go` as a foolproof entrypoint that accepts inline text or requirement documents, always converts them into an approved structured spec, then runs the existing planner/autopilot loop.

**Architecture:** Treat every `go` argument as a requirement source, never as direct execution permission. The new source/spec layer writes auditable files under `harness/specs/SPEC-*`, then `go` enforces approval before handing the approved spec to the existing planner and autopilot. Keep generation in provider capabilities and keep Attestflow responsible for parsing, state, approval gates, evidence, and safe orchestration.

**Tech Stack:** Python standard library for Markdown/TXT and CLI flow; optional document extra for `.docx` and text-layer `.pdf`; existing `attestflow` contracts, CLI, config, IO, capability, planner, and autopilot modules.

---

## Requirement Decisions

- `attestflow go <input>` supports inline text and files.
- Supported first-version file formats: `.md`, `.txt`, `.docx`, `.pdf`.
- First-version PDF support is limited to copyable text-layer PDFs. Scanned PDFs and OCR are out of scope.
- All inputs become `harness/specs/SPEC-*/source.*` and `spec.md`.
- `spec.md` is the execution permission boundary. Planner/autopilot must not run until the spec is approved.
- Interactive CLI can approve in-place after user review.
- Non-interactive mode can run only with `--from-spec <path> --approve --non-interactive`.
- PRD documents are summarized into a structured spec first; Attestflow asks only unclear, conflicting, or high-risk questions before approval.
- `go` remains a local CLI-first feature. CI/Bot support in v1 is limited to the non-interactive approved-spec path.

## File Structure

- Create `attestflow/requirements.py`: requirement source detection, inline/file ingestion, source evidence writing, text extraction dispatch.
- Create `attestflow/specs.py`: `SPEC-*` ID allocation, spec directory layout, draft spec rendering, approval metadata, unresolved-question detection.
- Create `attestflow/go.py`: top-level `go` orchestration that ties source ingestion, spec gate, planner, and autopilot together.
- Modify `attestflow/cli.py`: add `go` subcommand and delegate to `attestflow.go`.
- Modify `attestflow/config.py`: add default `paths.specs: harness/specs` and validate it.
- Modify `templates/base/harness.yml` and `attestflow/templates/base/harness.yml`: include `paths.specs`.
- Modify `docs/contracts/planner-output-schema.md` and `.en.md`: state planner input for `go` is an approved spec, not raw goal text.
- Create `docs/contracts/spec-schema.md` and `.en.md`: document spec directory and approval contract.
- Modify `docs/getting-started.md`, `.en.md`, `README.md`, and `README.zh-CN.md`: document `attestflow go`.
- Create `tests/unit/test_requirements_source.py`: source detection and extraction tests.
- Create `tests/unit/test_spec_lifecycle.py`: spec creation, approval, unresolved question gate tests.
- Create `tests/unit/test_go_cli.py`: CLI behavior for inline text, files, interactive/non-interactive gates.
- Add BDD coverage in `tests/bdd/test_go_requirement_loop.py`.

## Task 1: Spec Path Configuration

**Files:**
- Modify: `attestflow/config.py`
- Modify: `templates/base/harness.yml`
- Modify: `attestflow/templates/base/harness.yml`
- Test: `tests/unit/test_config_and_io.py`

- [ ] **Step 1: Write the failing config test**

Add this test to `tests/unit/test_config_and_io.py`:

```python
def test_default_config_includes_specs_path(self) -> None:
    config = load_config(Path("/tmp/attestflow-missing"))

    self.assertEqual(config["paths"]["specs"], "harness/specs")
    self.assertEqual(validate_config(config), [])
```

- [ ] **Step 2: Run the focused test and verify failure**

Run:

```bash
python3 -m unittest tests.unit.test_config_and_io.ConfigAndIoTests.test_default_config_includes_specs_path
```

Expected: failure because `paths.specs` is missing.

- [ ] **Step 3: Add default path**

In `attestflow/config.py`, add `specs: harness/specs` to `DEFAULT_CONFIG["paths"]`.

```python
"specs": "harness/specs",
```

- [ ] **Step 4: Update templates**

Add the same key under `paths:` in both template files:

```yaml
  specs: harness/specs
```

- [ ] **Step 5: Run verification**

Run:

```bash
python3 -m unittest tests.unit.test_config_and_io.ConfigAndIoTests.test_default_config_includes_specs_path
python3 -m attestflow install-smoke --offline --check-template-mirror --skip-path-check
```

Expected: both pass.

- [ ] **Step 6: Commit**

```bash
git add attestflow/config.py templates/base/harness.yml attestflow/templates/base/harness.yml tests/unit/test_config_and_io.py
git commit -m "Add specs path to harness config"
```

## Task 2: Requirement Source Ingestion

**Files:**
- Create: `attestflow/requirements.py`
- Test: `tests/unit/test_requirements_source.py`

- [ ] **Step 1: Write failing source tests**

Create `tests/unit/test_requirements_source.py`:

```python
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from attestflow.requirements import ingest_requirement_source
from attestflow.io import load_data


class RequirementSourceTests(unittest.TestCase):
    def test_ingests_inline_text_as_source_evidence(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = ingest_requirement_source(root, {"paths": {"specs": "harness/specs"}}, "实现登录功能")

            self.assertEqual(result.kind, "inline_text")
            self.assertEqual(result.text, "实现登录功能")
            source = load_data(result.evidence_path)
            self.assertEqual(source["kind"], "inline_text")
            self.assertEqual(source["original"], "实现登录功能")
            self.assertTrue(source["content_hash"])

    def test_ingests_markdown_file_and_preserves_path(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            prd = root / "PRD.md"
            prd.write_text("# Login\n\nUsers sign in with email.\n", encoding="utf-8")

            result = ingest_requirement_source(root, {"paths": {"specs": "harness/specs"}}, str(prd))

            self.assertEqual(result.kind, "file")
            self.assertIn("Users sign in with email.", result.text)
            source = load_data(result.evidence_path)
            self.assertEqual(source["path"], str(prd))
            self.assertEqual(source["format"], "md")

    def test_rejects_scanned_pdf_without_text_layer(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / "scan.pdf"
            pdf.write_bytes(b"%PDF-1.4\n% no text extractor can read this fixture\n")

            with self.assertRaisesRegex(ValueError, "PDF text layer could not be extracted"):
                ingest_requirement_source(root, {"paths": {"specs": "harness/specs"}}, str(pdf))
```

- [ ] **Step 2: Run tests and verify import failure**

Run:

```bash
python3 -m unittest tests.unit.test_requirements_source
```

Expected: failure because `attestflow.requirements` does not exist.

- [ ] **Step 3: Implement source ingestion**

Create `attestflow/requirements.py` with:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import hashlib
from typing import Any

from .io import dump_data


SUPPORTED_TEXT_FORMATS = {"md", "markdown", "txt"}
SUPPORTED_DOCUMENT_FORMATS = {"docx", "pdf"}


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
    maybe_path = Path(raw)
    if maybe_path.exists():
        return _ingest_file(root, config, maybe_path)
    return _ingest_inline_text(root, config, raw)


def _ingest_inline_text(root: Path, config: dict[str, Any], text: str) -> RequirementSource:
    evidence_path = _source_evidence_path(root, config, _content_hash(text), "inline")
    dump_data(
        {
            "schema_version": 1,
            "kind": "inline_text",
            "original": text,
            "content_hash": _content_hash(text),
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
    if not text.strip():
        raise ValueError(f"requirement source has no readable text: {path}")
    evidence_path = _source_evidence_path(root, config, _content_hash(text), suffix)
    dump_data(
        {
            "schema_version": 1,
            "kind": "file",
            "path": str(path),
            "format": suffix,
            "content_hash": _content_hash(text),
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
    reader = PdfReader(str(path))
    pages = [(page.extract_text() or "").strip() for page in reader.pages]
    text = "\n\n".join(page for page in pages if page)
    if not text.strip():
        raise ValueError("PDF text layer could not be extracted; scanned PDFs are not supported in v1")
    return text


def _source_evidence_path(root: Path, config: dict[str, Any], content_hash: str, suffix: str) -> Path:
    specs_root = root / str(config.get("paths", {}).get("specs", "harness/specs"))
    path = specs_root / "sources" / f"{content_hash[:12]}-{suffix}" / "source.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
python3 -m unittest tests.unit.test_requirements_source
```

Expected: inline and Markdown pass. The PDF fixture may raise a parser-specific error; normalize any parser exception in `_extract_pdf_text` to the planned `ValueError`.

- [ ] **Step 5: Commit**

```bash
git add attestflow/requirements.py tests/unit/test_requirements_source.py
git commit -m "Add requirement source ingestion"
```

## Task 3: Structured Spec Lifecycle

**Files:**
- Create: `attestflow/specs.py`
- Test: `tests/unit/test_spec_lifecycle.py`

- [ ] **Step 1: Write failing spec lifecycle tests**

Create `tests/unit/test_spec_lifecycle.py`:

```python
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from attestflow.specs import create_draft_spec, approve_spec, spec_has_unresolved_questions
from attestflow.io import load_data


class SpecLifecycleTests(unittest.TestCase):
    def test_creates_draft_spec_from_inline_source(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = create_draft_spec(
                root,
                {"paths": {"specs": "harness/specs"}},
                title="实现登录功能",
                source_text="实现登录功能",
                source_evidence="harness/specs/sources/abc/source.json",
            )

            self.assertEqual(spec.spec_id, "SPEC-0001")
            self.assertTrue(spec.path.exists())
            content = spec.path.read_text(encoding="utf-8")
            self.assertIn("## Goal", content)
            self.assertIn("## Open Questions", content)
            approval = load_data(spec.path.parent / "approval.json")
            self.assertEqual(approval["status"], "pending")

    def test_detects_unresolved_questions(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "spec.md"
            path.write_text("## Open Questions\n\n- Which auth method?\n", encoding="utf-8")

            self.assertTrue(spec_has_unresolved_questions(path))

    def test_approval_fails_when_questions_are_open(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = create_draft_spec(
                root,
                {"paths": {"specs": "harness/specs"}},
                title="Login",
                source_text="Login",
                source_evidence="harness/specs/sources/abc/source.json",
            )

            with self.assertRaisesRegex(ValueError, "spec still has open questions"):
                approve_spec(spec.path, approved_by="user")
```

- [ ] **Step 2: Run tests and verify import failure**

Run:

```bash
python3 -m unittest tests.unit.test_spec_lifecycle
```

Expected: failure because `attestflow.specs` does not exist.

- [ ] **Step 3: Implement spec lifecycle**

Create `attestflow/specs.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any

from .io import dump_data, load_data


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
    source_evidence: str,
) -> DraftSpec:
    spec_id = _next_spec_id(root, config)
    spec_dir = _specs_root(root, config) / spec_id
    spec_dir.mkdir(parents=True, exist_ok=False)
    spec_path = spec_dir / "spec.md"
    spec_path.write_text(_render_draft_spec(spec_id, title, source_text, source_evidence), encoding="utf-8")
    dump_data(
        {
            "schema_version": 1,
            "spec_id": spec_id,
            "status": "pending",
            "approved_by": None,
            "approved_at": None,
        },
        spec_dir / "approval.json",
    )
    return DraftSpec(spec_id=spec_id, path=spec_path)


def approve_spec(spec_path: Path, *, approved_by: str) -> dict[str, Any]:
    if spec_has_unresolved_questions(spec_path):
        raise ValueError("spec still has open questions")
    approval_path = spec_path.parent / "approval.json"
    approval = load_data(approval_path) if approval_path.exists() else {"schema_version": 1}
    approval.update(
        {
            "status": "approved",
            "approved_by": approved_by,
            "approved_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    dump_data(approval, approval_path)
    return approval


def require_approved_spec(spec_path: Path) -> None:
    approval_path = spec_path.parent / "approval.json"
    if not approval_path.exists():
        raise ValueError("spec approval is missing")
    approval = load_data(approval_path)
    if approval.get("status") != "approved":
        raise ValueError("spec is not approved")
    if spec_has_unresolved_questions(spec_path):
        raise ValueError("spec still has open questions")


def spec_has_unresolved_questions(spec_path: Path) -> bool:
    text = spec_path.read_text(encoding="utf-8")
    match = re.search(r"^## Open Questions\s*(.*?)(?:\n## |\Z)", text, flags=re.MULTILINE | re.DOTALL)
    if not match:
        return False
    section = match.group(1).strip()
    return bool(section and section not in {"None", "- None", "无", "- 无"})


def _render_draft_spec(spec_id: str, title: str, source_text: str, source_evidence: str) -> str:
    return f"""# {spec_id} {title}

## Goal

{title}

## Source Evidence

- {source_evidence}

## Confirmed Requirements

- Derived source needs human review before execution.

## Scope

- Define the implementation scope during intake review.

## Out Of Scope

- Work not approved in this spec.

## Acceptance Criteria

- Approved spec is converted into planner tasks.
- Planner tasks satisfy Attestflow Definition of Ready.

## Open Questions

- Confirm scope, boundaries, and acceptance criteria before execution.

## Source Summary

{source_text.strip()[:4000]}
"""


def _next_spec_id(root: Path, config: dict[str, Any]) -> str:
    specs_root = _specs_root(root, config)
    specs_root.mkdir(parents=True, exist_ok=True)
    numbers = []
    for path in specs_root.glob("SPEC-*"):
        if path.is_dir():
            try:
                numbers.append(int(path.name.split("-", 1)[1]))
            except (IndexError, ValueError):
                continue
    return f"SPEC-{max(numbers, default=0) + 1:04d}"


def _specs_root(root: Path, config: dict[str, Any]) -> Path:
    return root / str(config.get("paths", {}).get("specs", "harness/specs"))
```

- [ ] **Step 4: Add a passing approval test**

Add:

```python
def test_approves_spec_when_open_questions_are_none(self) -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        spec = create_draft_spec(
            root,
            {"paths": {"specs": "harness/specs"}},
            title="Login",
            source_text="Login",
            source_evidence="harness/specs/sources/abc/source.json",
        )
        content = spec.path.read_text(encoding="utf-8").replace(
            "- Confirm scope, boundaries, and acceptance criteria before execution.",
            "- None",
        )
        spec.path.write_text(content, encoding="utf-8")

        approval = approve_spec(spec.path, approved_by="user")

        self.assertEqual(approval["status"], "approved")
```

- [ ] **Step 5: Run tests**

Run:

```bash
python3 -m unittest tests.unit.test_spec_lifecycle
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add attestflow/specs.py tests/unit/test_spec_lifecycle.py
git commit -m "Add structured spec lifecycle"
```

## Task 4: Go Orchestrator Without Planner Execution

**Files:**
- Create: `attestflow/go.py`
- Test: `tests/unit/test_go_cli.py`

- [ ] **Step 1: Write failing orchestration tests**

Create `tests/unit/test_go_cli.py`:

```python
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from attestflow.go import prepare_go_run


class GoCliTests(unittest.TestCase):
    def test_prepare_go_run_creates_spec_for_inline_text(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = prepare_go_run(root, {"paths": {"specs": "harness/specs"}}, "实现登录功能")

            self.assertEqual(result.status, "needs_approval")
            self.assertEqual(result.spec_id, "SPEC-0001")
            self.assertTrue(result.spec_path.exists())

    def test_non_interactive_requires_approved_spec(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = root / "spec.md"
            spec.write_text("## Open Questions\n\n- None\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "spec approval is missing"):
                prepare_go_run(
                    root,
                    {"paths": {"specs": "harness/specs"}},
                    None,
                    from_spec=spec,
                    approve=True,
                    non_interactive=True,
                )
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
python3 -m unittest tests.unit.test_go_cli
```

Expected: failure because `attestflow.go` does not exist.

- [ ] **Step 3: Implement prepare phase**

Create `attestflow/go.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .requirements import ingest_requirement_source
from .specs import create_draft_spec, require_approved_spec


@dataclass(frozen=True)
class GoRunResult:
    status: str
    spec_id: str | None
    spec_path: Path


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
        require_approved_spec(from_spec)
        return GoRunResult(status="approved", spec_id=from_spec.parent.name, spec_path=from_spec)
    if non_interactive:
        raise ValueError("--non-interactive requires --from-spec and --approve")
    if not source:
        raise ValueError("attestflow go requires inline text, a document path, or --from-spec")
    requirement = ingest_requirement_source(root, config, source)
    title = _title_from_source(source, requirement.text)
    draft = create_draft_spec(
        root,
        config,
        title=title,
        source_text=requirement.text,
        source_evidence=str(requirement.evidence_path.relative_to(root)),
    )
    return GoRunResult(status="needs_approval", spec_id=draft.spec_id, spec_path=draft.path)


def _title_from_source(source: str, text: str) -> str:
    candidate = Path(source)
    if candidate.exists():
        return candidate.stem.replace("-", " ").replace("_", " ").strip() or "Requirement Source"
    return text.strip().splitlines()[0][:80] or "Requirement Source"
```

- [ ] **Step 4: Run tests**

Run:

```bash
python3 -m unittest tests.unit.test_go_cli tests.unit.test_requirements_source tests.unit.test_spec_lifecycle
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add attestflow/go.py tests/unit/test_go_cli.py
git commit -m "Add go requirement preparation"
```

## Task 5: CLI Entry Point and Approval Gate

**Files:**
- Modify: `attestflow/cli.py`
- Test: `tests/unit/test_go_cli.py`

- [ ] **Step 1: Add failing CLI tests**

Add to `tests/unit/test_go_cli.py`:

```python
from contextlib import redirect_stderr, redirect_stdout
import io

import attestflow.cli as cli


def test_cli_go_inline_text_writes_spec_and_stops_for_approval(self) -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        original_root = cli.ROOT
        cli.ROOT = root
        try:
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = cli.main(["go", "实现登录功能"])
        finally:
            cli.ROOT = original_root

        self.assertEqual(exit_code, 2)
        self.assertIn("spec approval required", output.getvalue())
        self.assertTrue((root / "harness" / "specs" / "SPEC-0001" / "spec.md").exists())


def test_cli_go_non_interactive_rejects_raw_text(self) -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        original_root = cli.ROOT
        cli.ROOT = root
        try:
            error = io.StringIO()
            with redirect_stderr(error):
                exit_code = cli.main(["go", "实现登录功能", "--non-interactive"])
        finally:
            cli.ROOT = original_root

        self.assertEqual(exit_code, 1)
        self.assertIn("--non-interactive requires --from-spec and --approve", error.getvalue())
```

- [ ] **Step 2: Run tests and verify parser failure**

Run:

```bash
python3 -m unittest tests.unit.test_go_cli
```

Expected: failure because `go` subcommand is unknown.

- [ ] **Step 3: Add CLI parser**

In `attestflow/cli.py`, import `prepare_go_run`, then add:

```python
def cmd_go(args: argparse.Namespace) -> int:
    try:
        result = prepare_go_run(
            ROOT,
            load_config(ROOT),
            args.source,
            from_spec=Path(args.from_spec) if args.from_spec else None,
            approve=args.approve,
            non_interactive=args.non_interactive,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if result.status == "needs_approval":
        print(f"spec approval required: {result.spec_path}")
        print("Review the spec, resolve open questions, then rerun with --from-spec and --approve.")
        return 2
    print(f"spec approved: {result.spec_path}")
    return 0
```

Register parser near other top-level subcommands:

```python
go = subparsers.add_parser("go")
go.add_argument("source", nargs="?")
go.add_argument("--from-spec")
go.add_argument("--approve", action="store_true")
go.add_argument("--non-interactive", action="store_true")
go.set_defaults(func=cmd_go)
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
python3 -m unittest tests.unit.test_go_cli
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add attestflow/cli.py tests/unit/test_go_cli.py
git commit -m "Add go CLI approval gate"
```

## Task 6: Planner and Autopilot Handoff From Approved Spec

**Files:**
- Modify: `attestflow/go.py`
- Modify: `attestflow/cli.py`
- Test: `tests/unit/test_go_cli.py`

- [ ] **Step 1: Add failing approved-spec execution test**

Add:

```python
def test_cli_go_approved_spec_starts_autopilot_goal(self) -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        spec_dir = root / "harness" / "specs" / "SPEC-0001"
        spec_dir.mkdir(parents=True)
        spec_path = spec_dir / "spec.md"
        spec_path.write_text(
            "# SPEC-0001 Login\n\n## Goal\n\nImplement login.\n\n## Open Questions\n\n- None\n",
            encoding="utf-8",
        )
        from attestflow.io import dump_data

        dump_data({"schema_version": 1, "status": "approved"}, spec_dir / "approval.json")

        original_root = cli.ROOT
        cli.ROOT = root
        try:
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = cli.main(["go", "--from-spec", str(spec_path), "--approve", "--non-interactive"])
        finally:
            cli.ROOT = original_root

        self.assertEqual(exit_code, 0)
        self.assertIn("spec approved", output.getvalue())
```

- [ ] **Step 2: Add orchestration seam**

Extend `GoRunResult` with an optional `goal` field:

```python
goal: str | None = None
```

When `from_spec` is approved, set `goal` to the spec content:

```python
return GoRunResult(status="approved", spec_id=from_spec.parent.name, spec_path=from_spec, goal=from_spec.read_text(encoding="utf-8"))
```

- [ ] **Step 3: Wire execution through existing autopilot**

In `cmd_go`, after an approved result, call existing `run_autopilot`. In v1, `--from-spec <path> --approve` is execution permission; do not add a second execution switch.

```python
result_run = run_autopilot(
    ROOT,
    load_config(ROOT),
    limit=_autopilot_limit(load_config(ROOT), args.limit),
    max_steps=_autopilot_max_steps(load_config(ROOT), args.max_steps),
    actor_role="orchestrator",
    goal=result.goal,
)
_print_autopilot_run_result(result_run)
return 1 if result_run.failed or result_run.blocked or result_run.cancelled else 0
```

Add parser options:

```python
go.add_argument("--limit", type=int)
go.add_argument("--max-steps", type=int)
```

- [ ] **Step 4: Keep the unit test deterministic**

For the focused CLI unit test, monkeypatch `cli.run_autopilot` to a fake result object rather than running provider code:

```python
class FakeResult:
    run_id = "autopilot-1"
    path = root / "harness" / "autopilot-runs" / "autopilot-1"
    status = "finished"
    pause_reason = None
    dispatched = []
    actions = []
    failed = []
    blocked = []
    cancelled = []
    planned = []
    skipped = []
    steps = 0
    limit = 1
    planner = None
    release = None
    release_status = None
    release_repair_planner = None
    releaser = None
    releaser_tasks = []
    intake = None
    intake_status = None
    batch_executions = []
```

- [ ] **Step 5: Run tests**

Run:

```bash
python3 -m unittest tests.unit.test_go_cli tests.unit.test_spec_lifecycle
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add attestflow/go.py attestflow/cli.py tests/unit/test_go_cli.py
git commit -m "Run autopilot from approved go specs"
```

## Task 7: Document Parser Packaging

**Files:**
- Modify: `pyproject.toml`
- Modify: `docs/getting-started.md`
- Modify: `docs/getting-started.en.md`
- Test: `tests/unit/test_requirements_source.py`

- [ ] **Step 1: Add optional dependency extra**

In `pyproject.toml`, add:

```toml
[project.optional-dependencies]
documents = [
  "python-docx>=1.1.0",
  "pypdf>=4.0.0",
]
```

If `[project.optional-dependencies]` already exists, merge this `documents` entry into it.

- [ ] **Step 2: Add missing dependency tests**

The existing tests should assert the error strings:

```python
"DOCX support requires installing attestflow[documents]"
"PDF support requires installing attestflow[documents]"
```

Use `unittest.mock.patch.dict("sys.modules", {"docx": None})` only if the local environment happens to have the dependency installed.

- [ ] **Step 3: Document v1 PDF boundary**

Add to docs:

```markdown
`attestflow go` supports Markdown, TXT, DOCX, and copyable text-layer PDF files. Scanned PDFs and OCR are not supported in v1; convert them to Markdown, TXT, or DOCX before running `go`.
```

- [ ] **Step 4: Run tests**

Run:

```bash
python3 -m unittest tests.unit.test_requirements_source
python3 -m compileall -q attestflow
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml docs/getting-started.md docs/getting-started.en.md tests/unit/test_requirements_source.py
git commit -m "Document optional requirement parsers"
```

## Task 8: BDD and End-to-End Local Flow

**Files:**
- Create: `tests/bdd/test_go_requirement_loop.py`
- Modify: `README.md`
- Modify: `README.zh-CN.md`

- [ ] **Step 1: Write BDD test**

Create `tests/bdd/test_go_requirement_loop.py`:

```python
from contextlib import redirect_stdout
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import attestflow.cli as cli


class GoRequirementLoopBehaviorTests(unittest.TestCase):
    def test_inline_goal_creates_spec_before_execution(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(["go", "实现登录功能"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 2)
            self.assertIn("spec approval required", output.getvalue())
            self.assertTrue((root / "harness" / "specs" / "SPEC-0001" / "spec.md").exists())
```

- [ ] **Step 2: Run BDD test**

Run:

```bash
python3 -m unittest tests.bdd.test_go_requirement_loop
```

Expected: pass after previous tasks.

- [ ] **Step 3: Update README commands**

Add concise examples:

```markdown
python3 -m attestflow go "Implement login"
python3 -m attestflow go docs/PRD.md
python3 -m attestflow go --from-spec harness/specs/SPEC-0001/spec.md --approve --non-interactive
```

Explain that raw sources always stop at spec approval first.

- [ ] **Step 4: Run full verification**

Run:

```bash
python3 -m unittest discover -s tests/unit
python3 -m unittest discover -s tests/bdd
python3 -m attestflow verify
python3 -m attestflow doctor
python3 -m attestflow secret-scan
python3 -m compileall -q attestflow tests
git diff --check
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add tests/bdd/test_go_requirement_loop.py README.md README.zh-CN.md
git commit -m "Document go requirement loop"
```

## Task 9: Final Contract and Governance Docs

**Files:**
- Create: `docs/contracts/spec-schema.md`
- Create: `docs/contracts/spec-schema.en.md`
- Modify: `docs/design/universal-harness.md`
- Modify: `docs/design/universal-harness.en.md`
- Modify: `docs/contracts/planner-output-schema.md`
- Modify: `docs/contracts/planner-output-schema.en.md`

- [ ] **Step 1: Write spec contract**

Create `docs/contracts/spec-schema.md` with:

```markdown
# Spec Schema 契约

`attestflow go` 的任何输入都是 requirement source，不是执行许可。执行许可只能来自 approved spec。

## Runtime Layout

```text
harness/specs/SPEC-0001/
  spec.md
  approval.json
  source.json
  source.txt
```

## Approval Rules

- `approval.json.status` 必须是 `approved` 才能进入 planner。
- `spec.md` 的 `Open Questions` 必须为 `None`、`无` 或空。
- 非交互模式必须显式传入 `--from-spec`、`--approve` 和 `--non-interactive`。
- 文档解析失败、扫描 PDF、缺少 DOCX/PDF 可选依赖都不能进入 planner。
```

Create the English version with the same rules.

- [ ] **Step 2: Update design docs**

Add the new front of the workflow:

```text
requirement source -> draft spec -> clarification -> approved spec -> planner JSON -> task import -> autopilot
```

- [ ] **Step 3: Update planner contract**

State that `go` passes the approved spec content as planner goal/context. Planner providers must not infer approval from raw user text or raw PRD content.

- [ ] **Step 4: Run documentation checks**

Run:

```bash
python3 -m attestflow install-smoke --offline --check-template-mirror --skip-path-check
git diff --check
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add docs/contracts/spec-schema.md docs/contracts/spec-schema.en.md docs/design/universal-harness.md docs/design/universal-harness.en.md docs/contracts/planner-output-schema.md docs/contracts/planner-output-schema.en.md
git commit -m "Define go spec approval contract"
```

## Final Verification

Run the complete validation chain:

```bash
python3 -m unittest discover -s tests/unit
python3 -m unittest discover -s tests/bdd
python3 -m attestflow install-smoke --offline --check-template-mirror --skip-path-check
python3 -m attestflow autonomy doctor --json
python3 -m attestflow verify
python3 -m attestflow doctor
python3 -m attestflow secret-scan
python3 -m compileall -q attestflow examples tests
git diff --check
```

Expected final state:

- `attestflow go "实现登录功能"` creates `harness/specs/SPEC-0001/spec.md` and exits before execution with approval required.
- `attestflow go PRD.md` creates a draft spec from the Markdown source and exits before execution with approval required.
- `.txt` behaves like Markdown without Markdown structure.
- `.docx` and text-layer `.pdf` work only when `attestflow[documents]` dependencies are installed.
- Scanned PDF produces a clear blocked/error message and does not run planner/autopilot.
- `attestflow go --from-spec harness/specs/SPEC-0001/spec.md --approve --non-interactive` refuses unapproved specs and open questions.
- Approved specs can enter the existing planner/autopilot path.

## Self-Review Notes

- Spec coverage: inline text, Markdown/TXT/DOCX/PDF sources, copyable-PDF-only boundary, draft spec, approval, interactive default, non-interactive approved-spec path, and planner/autopilot handoff are each mapped to tasks.
- Placeholder scan: no implementation step depends on undefined future work; OCR, scanned PDF, CI/Bot interaction, and SaaS control plane are explicitly out of v1.
- Type consistency: source ingestion returns `RequirementSource`; spec lifecycle returns `DraftSpec`; go preparation returns `GoRunResult`.
