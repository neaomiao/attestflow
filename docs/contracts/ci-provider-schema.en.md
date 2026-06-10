# CI Provider Contract

Date: 2026-05-30
Status: `ci status`, `ci await`, `ci logs`, `ci artifacts`, `ci rerun`, `ci dispatch`, and the GitHub Actions adapter implemented

CI providers capture external CI state as evidence. They do not replace local `verify --task`; they complement it with remote build, log, annotation, artifact, rerun, and dispatch evidence.

## Input

CI provider input includes:

- `schema_version`
- `action`: `status`, `await`, `logs`, `artifacts`, `rerun`, or `dispatch`
- repository, branch, head SHA, workflow, event, run id, or job filters
- optional task id and existing task evidence
- provider options such as timeout and download directory

## Output

CI provider output must include:

```json
{
  "schema_version": 1,
  "status": "passed",
  "summary": "CI passed for the target commit.",
  "external": {
    "provider": "github-actions",
    "run_id": "123456789",
    "url": "https://example.invalid/actions/runs/123456789"
  },
  "jobs": [],
  "annotations": [],
  "artifacts": []
}
```

`status` is one of `passed`, `failed`, `running`, `queued`, `unknown`, or `blocked`.

## GitHub Actions Preset

The built-in `github-actions` preset maps `gh run` data into this contract. It can filter by branch, head SHA, workflow, event, or run id so evidence attaches to the exact PR or commit being shipped.

## Evidence

Outputs are stored under `harness/ci-runs/`. With `--task TASK-*`, Attestflow writes the evidence reference to `task.evidence.ci`. Failed runs should include job details, annotations, and failed log snippets when available.
