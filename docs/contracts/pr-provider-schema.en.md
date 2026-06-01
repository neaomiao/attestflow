# PR Provider Contract

[中文](pr-provider-schema.md)

Date: 2026-05-30
Status: `pr ensure`, `pr merge`, and `pr status` command provider implemented

PR providers create, update, merge, and inspect external pull requests or change requests. Attestflow uses them as evidence adapters; it does not make GitHub a core dependency.

## Input

PR provider input includes:

- `schema_version`
- `action`: `ensure`, `merge`, or `status`
- task id and task summary when task-scoped
- branch, base branch, commit, title, body, labels, or reviewers when available
- provider options such as repository, merge method, auto-merge policy, and timeout

## Output

PR provider output must include:

```json
{
  "schema_version": 1,
  "status": "open",
  "summary": "PR is open.",
  "external": {
    "provider": "github",
    "id": "42",
    "url": "https://example.invalid/pull/42"
  }
}
```

`status` can be `open`, `draft`, `merged`, `closed`, `skipped`, `blocked`, or `failed`.

## Autopilot Rules

Autopilot can call `pr ensure` during the accepted stage. It only calls `pr merge` when `integrations.pr_provider.auto_merge: true` is explicitly configured and CI evidence has passed. `merged` and `skipped` allow close; `open`, `draft`, or `blocked` pause or block.

## Evidence

Outputs are stored under `harness/pr-runs/`. With a task id, Attestflow writes references to `task.evidence.pr_request`, `task.evidence.pr_merge`, or `task.evidence.pr`.
