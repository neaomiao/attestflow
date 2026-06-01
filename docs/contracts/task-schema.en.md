# Task Schema Contract

Date: 2026-05-29
Status: core local validation implemented

`task schema` is the stable protocol between programming-agent planners, CI, and the harness runtime. Programming agents generate task content by default; Attestflow validates it and writes runtime task JSON.

Field names, state names, and command names stay in English because code, CI, and scripts parse them.

## Location

Task files live under the configured task root:

```text
harness/tasks/<state>/<task-id>.json
```

The directory state must be legal and must match the file's `state`. The filename must match the task `id`. The same `id` cannot appear in multiple state directories.

Required state directories:

```text
proposed
needs_clarification
ready
in_progress
blocked
review
verified
accepted
done
archived
```

## Required Fields

Runtime task JSON includes:

- `schema_version`
- `id`, `title`, `state`, `priority`, and `type`
- `purpose`, `context`, `scope`, and `out_of_scope`
- `requirements.confirmed`, `requirements.unresolved`, and `requirements.assumptions`
- `bdd_scenarios`, `unit_tests`, and `acceptance`
- `dependencies`, `blocks`, and `blockers`
- `files.read` and `files.write`
- `agents.owner` and `agents.allowed_roles`
- `external_inputs`
- `evidence`
- `links`
- optional `source`
- `risks`, `notes`, `created_at`, and `updated_at`

## State Rules

Definition of Ready must pass before a task can start. Definition of Done and current-run evidence must pass before a task can close. Invalid transitions such as `ready -> done` fail closed.

## Source Evidence

External issues, tickets, PR comments, and CI failures enter through `source import`, which creates source evidence and a `proposed` task. Real executable task boundaries still come from intake/planner.

## Evidence References

Task evidence references session, run, red/green testing, verification, packet, Git, CI, PR, release, and capability evidence. Close requires the references to agree on task id and run id.
