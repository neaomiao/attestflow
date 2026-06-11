# Git Provider Contract

Date: 2026-05-31
Status: `publish` command provider implemented

Git providers publish local work and record delivery evidence. The built-in `git` provider uses local Git commands and fails closed on unsafe default-branch pushes unless explicitly allowed.

## Input

Git provider input contains:

- `schema_version`
- action, currently `publish`
- task id when publishing task-scoped work
- repository root and branch
- changed file summary
- provider options such as remote, allow-default-branch-push, commit message, and timeout

## Output

Git provider output must include:

```json
{
  "schema_version": 1,
  "status": "published",
  "summary": "Committed and pushed branch.",
  "branch": "feature/my-change",
  "commit": "abc123",
  "remote": "origin",
  "url": "https://example.invalid/repo/commit/abc123"
}
```

`status` can be `published`, `skipped`, `blocked`, `failed`, or `unknown`.

## Safety

The provider must not silently push default branches. It should preserve evidence for commit before/after, branch, remote, stdout/stderr, and any reason it skipped or blocked.

## Evidence

Outputs are stored under `harness/git-runs/`. With `--task TASK-*`, Attestflow writes the evidence reference to `task.evidence.git`.
