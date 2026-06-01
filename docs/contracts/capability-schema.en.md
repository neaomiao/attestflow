# Capability Contract

[中文](capability-schema.md)

Date: 2026-05-30
Status: planner capability and task-scoped capability runner implemented

Capabilities are role-specific provider calls. Attestflow prepares deterministic input, calls the configured provider, validates output, saves evidence, and writes references back to the task.

## Input

All capability providers receive a JSON object containing:

- `schema_version`
- capability name and phase
- task data for task-scoped capabilities
- repository context with tree, documents, focus files, limits, and optional dynamic context
- policy and command hints
- previous evidence relevant to the phase

Providers must treat `files.write` as the write boundary and should request more context through dynamic context instead of scanning the repository.

## Output

Every capability output must include:

```json
{
  "schema_version": 1,
  "status": "passed",
  "summary": "Short outcome summary.",
  "findings": [],
  "evidence": []
}
```

`status` is one of `passed`, `failed`, or `blocked`.

Task-scoped capabilities add phase-specific `artifacts`:

- `bdd`: BDD scenarios and test file proposals.
- `tdd`: failing unit test details.
- `implementer`: changed files and implementation notes.
- `reviewer`: findings, risks, and suggested follow-up.
- `verifier`: verification commands and outcomes.
- `releaser`: release handoff or release status summary.

## Blocked Output

Return `blocked` when execution needs credentials, external service state, user decisions, or unavailable tools. Include blocker details instead of returning `passed` with caveats.

## Evidence

Attestflow saves provider input, output, stdout, stderr, dynamic context, usage, failures, and write-scope reports under `harness/capability-runs/`. Task files store relative evidence paths.
