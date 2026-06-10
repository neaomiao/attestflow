# Spec Schema Contract

Date: 2026-06-10
Status: `attestflow go` approval boundary implemented

## Goal

Every input to `attestflow go` is a requirement source, not execution permission. Inline text, Markdown, TXT, DOCX, and PDF inputs only mean that a requirement source needs to be turned into a reviewable spec. Execution permission can only come from an approved spec.

This boundary prevents raw PRDs, issues, review comments, or one-line goals from becoming ready tasks directly. `go` must first converge the source into `spec.md`, resolve open questions, record approval in `approval.json`, and only then pass the approved spec to the planner.

## Runtime Layout

The spec runtime root is configured by `paths.specs`; the default is:

```text
harness/specs/
  SPEC-0001/
    spec.md
    approval.json
  sources/
    <content-hash>-<format>/
      source.json
      source.txt
```

`harness/specs/SPEC-0001/spec.md` is the reviewable and approvable requirement spec. `approval.json` stores approval metadata for that spec.

Source evidence is written under `harness/specs/sources/...`. `source.json` stores the source type, format, path, hash, and received time. `source.txt` stores extracted text for file sources. Inline text sources only require `source.json`, which preserves the original input.

## Spec Content

The v1 deterministic minimum required headings are only the structures currently hard-checked by `require_approved_spec`:

- `Goal`: the approved goal.
- `Acceptance Criteria`: approved acceptance criteria.
- `Open Questions`: unresolved questions.

`Open Questions` is an execution gate. It must be `None`, `无`, or empty before the spec can enter the planner.

Generated draft spec templates also include recommended review sections that help humans and agents inspect the source and converge the boundary:

- `Source Evidence`: a pointer to `harness/specs/sources/.../source.json`.
- `Confirmed Requirements`: requirements that are already confirmed.
- `Scope`: work included in this run.
- `Out Of Scope`: work explicitly excluded.
- `Source Summary`: a summary or excerpt of the source text.

These recommended sections belong to the draft template and review workflow; they are not v1 deterministic hard gates in `require_approved_spec`. Projects may keep, extend, or rewrite them before approval. The hard gate remains the three-heading minimum above, valid `approval.json`, and resolved `Open Questions`.

## Approval Rules

- `approval.json.status` must be `approved` before the spec can enter the planner.
- `approval.json.schema_version` must be `1`.
- `approval.json.spec_id` must match the spec directory name, for example `SPEC-0001`.
- `approval.json.approved_by` and `approval.json.approved_at` must be non-empty strings.
- `spec.md` `Open Questions` must be `None`, `无`, or empty.
- Non-interactive mode must pass `--from-spec`, `--approve`, and `--non-interactive` explicitly.
- `--from-spec` must live under the configured specs directory and must have the shape `SPEC-####/spec.md`.
- The configured specs directory must live under the project root.
- Document parse failures, scanned PDFs, missing DOCX/PDF optional dependencies, unreadable sources, and empty sources must not enter the planner.

## Planner Boundary

`go` passes approved spec content and context to the planner, not raw user text, raw PRD content, or source evidence. Planner providers must not infer approval from a raw source, and must not treat a raw source as a clarified execution boundary.

If approval is missing, invalid, or the spec still has open questions, Attestflow must fail closed: it must not create ready tasks and must not start autopilot.
