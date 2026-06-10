# Release Provider Contract

Date: 2026-05-30
Status: `release status` command provider implemented

Release providers collect external release state after implementation tasks have converged. They are evidence adapters for deployment or release systems; they do not replace task-level verification.

## Input

Release provider input includes:

- `schema_version`
- done or archived task summaries
- task titles, scope, acceptance, and evidence references
- available CI, PR, verify, and release handoff evidence
- provider options such as environment, release id, timeout, or external command

## Output

Release provider output must include:

```json
{
  "schema_version": 1,
  "status": "released",
  "summary": "Release completed.",
  "external": {
    "provider": "command",
    "id": "release-2026-06-01",
    "url": "https://example.invalid/releases/release-2026-06-01"
  }
}
```

`status` can be `released`, `skipped`, `running`, `queued`, `unknown`, `blocked`, or `failed`.

## Autopilot Rules

Autopilot calls release providers only after all tasks are done or archived. `released` and `skipped` are terminal. `running`, `queued`, and `unknown` pause for resume. `blocked` blocks. `failed` can trigger release repair planning when a planner capability is configured.

## Evidence

Outputs are stored under `harness/release-runs/` and summarized in top-level autopilot metadata.
