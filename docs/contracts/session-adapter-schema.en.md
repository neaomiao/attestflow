# Session Adapter Contract

[中文](session-adapter-schema.md)

Date: 2026-05-30
Status: command adapter implemented

Session adapters launch and resume external programming agent sessions. They let Attestflow keep deterministic state while delegating generative implementation work to agent tools.

## Launch Input

Session launch input includes:

- `schema_version`
- task id, run id, role, and prompt path
- repository root or task worktree
- task JSON and declared file scopes
- provider options and timeout
- context and evidence references

## Launch Output

Launch output must include:

```json
{
  "schema_version": 1,
  "status": "started",
  "session_id": "external-session-id",
  "summary": "Agent session started."
}
```

`status` can be `started`, `blocked`, or `failed`.

## Resume Input and Output

Resume input references the existing task, run, session id, latest ledger state, and next action. Resume output reports `resumed`, `completed`, `blocked`, or `failed` and can include usage evidence.

## Write-scope Validation

Before and after launch/resume, Attestflow records filesystem snapshots and validates add/modify/delete/rename/binary drift against `files.write`. Reports are saved as `session-launch-write-scope.json` or `session-resume-write-scope.json`.

## Evidence

Attestflow stores adapter input, output, stdout, stderr, usage, failure, and write-scope reports under the task run directory. `attestflow session resume TASK-*` uses the same contract to continue a session.
