# Agent Blackboard Contract Design

Date: 2026-06-11
Status: Approved design for implementation planning

## Goal

Add a formal `agent_messages` / blackboard contract to Attestflow so multiple Agent roles can coordinate through auditable, deterministic runtime state instead of direct private chat.

The first version must preserve Attestflow's core boundary: models generate and discuss work, while Attestflow owns state, locks, provenance, validation, and evidence. Blackboard messages are collaboration evidence, not permission to bypass approved specs, task state transitions, write-scope locks, verification, or close gates.

## Non-Goals

- No realtime socket, pub/sub, or background daemon.
- No direct Agent-to-Agent private transport.
- No consensus, voting, debate engine, or automatic conflict resolver.
- No replacement for task JSON, run metadata, capability output, session packet, or PR/CI/release evidence.
- No remote SaaS storage in v1.

## User-Facing Model

Attestflow exposes an asynchronous, append-only blackboard:

```text
Agent role -> blackboard message -> Attestflow validation/storage -> another Agent role reads it
```

The blackboard supports several collaboration patterns on top of one deterministic communication channel:

- Planner asks implementer/reviewer for task-boundary feedback.
- Reviewer posts a finding for implementer.
- Implementer asks verifier about failing command evidence.
- Releaser records handoff notes or release blockers.
- Orchestrator records coordination decisions that should be visible to later runs.

Messages are scoped by optional `task_id`, `run_id`, and `thread_id`. A task-scoped message belongs to the task lifecycle; a run-scoped message belongs to a specific execution attempt; a thread-scoped message groups multi-turn discussion.

## Runtime Storage

Default paths:

- `harness/blackboard/messages.jsonl`
- optional future summaries under `harness/blackboard/summaries/`

`messages.jsonl` is append-only. Every line is an event. A logical message is the derived state produced by replaying all events with the same `message_id` in file order. Updates such as resolve are represented by appending a terminal event that references the original `message_id`; the original post event is never rewritten.

Writers must serialize through `harness/blackboard/blackboard.lock` before allocating IDs or appending. The write sequence is:

1. acquire the lock;
2. read existing events and compute the next `EVT-####`, `MSG-####`, and `THREAD-####` numbers;
3. validate the new event against the current event log;
4. append exactly one newline-terminated JSON object using a single file append operation;
5. release the lock.

This prevents concurrent CLI processes from allocating duplicate IDs or interleaving event lines.

The config gains:

```yaml
paths:
  blackboard: harness/blackboard
```

## Event Contract

Each line in `messages.jsonl` is a JSON object:

```json
{
  "schema_version": 1,
  "event_id": "EVT-0001",
  "event_type": "post",
  "message_id": "MSG-0001",
  "thread_id": "THREAD-0001",
  "task_id": "TASK-0001",
  "run_id": "run-123",
  "from_role": "reviewer",
  "to_role": "implementer",
  "message_type": "finding",
  "body": "The login task is missing a lockout acceptance criterion.",
  "requires_response": true,
  "status": "open",
  "reply_to": null,
  "evidence_refs": ["harness/capability-runs/reviewer-TASK-0001-.../output.json"],
  "created_at": "2026-06-11T00:00:00+00:00"
}
```

Required fields:

- `schema_version`: must be `1`.
- `event_id`: assigned by Attestflow as `EVT-####`.
- `event_type`: one of `post`, `resolve`, or `supersede`.
- `message_id`: assigned by Attestflow as `MSG-####` for `post`; required target message ID for `resolve` and `supersede`.
- `thread_id`: assigned by Attestflow when omitted as `THREAD-####`; replies inherit or specify a thread.
- `from_role`: non-empty role name.
- `message_type`: one of the supported message types.
- `body`: non-empty text.
- `status`: `open` for `post`, `resolved` for `resolve`, and `superseded` for `supersede`.
- `created_at`: UTC ISO timestamp assigned by Attestflow.

Optional fields:

- `task_id`: must reference an existing task when present.
- `run_id`: string for the relevant run or external provider run.
- `to_role`: role name or `orchestrator`; omitted means broadcast within the scope.
- `reply_to`: must reference an existing `message_id` when present.
- `requires_response`: boolean, defaults to `false`.
- `evidence_refs`: relative paths to existing files under the project root.

Terminal events use the same object shape:

```json
{
  "schema_version": 1,
  "event_id": "EVT-0002",
  "event_type": "resolve",
  "message_id": "MSG-0001",
  "thread_id": "THREAD-0001",
  "from_role": "implementer",
  "to_role": "reviewer",
  "message_type": "answer",
  "body": "Added the missing lockout acceptance criterion.",
  "requires_response": false,
  "status": "resolved",
  "reply_to": "MSG-0001",
  "evidence_refs": ["harness/capability-runs/implementer-TASK-0001-.../output.json"],
  "created_at": "2026-06-11T00:01:00+00:00"
}
```

Derived message state rules:

- A `post` event creates the logical message and initial derived status `open`.
- A `resolve` event is valid only for an existing non-terminal message and changes derived status to `resolved`.
- A `supersede` event is valid only for an existing non-terminal message and changes derived status to `superseded`.
- Later terminal events for an already terminal message are rejected.
- `list --status` filters on derived status, not the raw event line status.
- `show MSG-#### --json` returns the derived message plus its ordered `events` list.

Supported `message_type` values for v1:

- `question`
- `answer`
- `finding`
- `decision`
- `handoff`
- `blocker`
- `status`
- `note`

## Validation Rules

Attestflow must fail closed when:

- `body`, `from_role`, or `message_type` is empty or invalid.
- `task_id` is present but does not point to a known task.
- `reply_to` is present but the target message does not exist.
- `evidence_refs` are absolute paths, escape the project root, or reference missing files.
- A resolve command targets an unknown or already terminal message.
- A terminal derived status appears without an appended terminal event.

Blackboard validation must not require a provider or model account.

## CLI

First-version commands:

```bash
python -m attestflow blackboard post \
  --task TASK-0001 \
  --from-role reviewer \
  --to-role implementer \
  --type finding \
  --body "Missing lockout acceptance criterion." \
  --requires-response

python -m attestflow blackboard list --task TASK-0001
python -m attestflow blackboard show MSG-0001 --json
python -m attestflow blackboard resolve MSG-0001 --from-role implementer --body "Added acceptance criterion."
```

Default human output should be compact. `--json` should expose the full message object or message list for automation.

## Library API

The CLI must call a shared library layer instead of owning validation itself. Minimum API:

```python
post_blackboard_message(root, config, *, from_role, body, message_type="note",
                        to_role=None, task_id=None, run_id=None,
                        thread_id=None, reply_to=None,
                        requires_response=False, evidence_refs=None) -> BlackboardMessage

list_blackboard_messages(root, config, *, task_id=None, thread_id=None,
                         status=None, include_events=False) -> list[BlackboardMessage]

show_blackboard_message(root, config, message_id, *, include_events=True) -> BlackboardMessage

resolve_blackboard_message(root, config, message_id, *, from_role, body,
                           evidence_refs=None) -> BlackboardMessage
```

`BlackboardMessage` is a derived message view with `message_id`, `thread_id`, scope fields, roles, `message_type`, latest `body`, `requires_response`, derived `status`, `created_at`, `updated_at`, `evidence_refs`, and optional ordered `events`.

All API functions raise `ValueError` for contract violations and `FileNotFoundError` only when required referenced files are missing. CLI catches both and prints `ERROR: ...` without traceback.

## Integration Points

### Planner / Capability Providers

Provider input should include relevant blackboard messages:

- task-scoped capability input includes open messages for that task.
- planner input may include thread-level or recent non-task messages when they are relevant to approved spec planning.
- release handoff may include unresolved release-scoped blockers.

This is read-only context. Providers can suggest messages in their output later, but v1 does not require automatic provider-to-blackboard writes.

### Autopilot

Autopilot may record blackboard summary counts in metadata later, but v1 only needs CLI and library support. Blackboard messages must not change task state by themselves.

### Dispatch / Sessions

Session packet generation may include task-scoped open blackboard messages so external Agent sessions can see reviewer findings, questions, and handoffs.

## Evidence and Recovery

Because the blackboard is append-only, recovery is deterministic:

- Rebuild current derived message status by reading all events in order.
- If two events conflict, the later event wins only for derived status; both remain in evidence.
- Missing or malformed lines should make validation fail rather than silently ignoring coordination evidence.

## Tests

Unit tests should cover:

- posting valid messages assigns `MSG-####` and `THREAD-####`.
- posting task-scoped messages rejects unknown task IDs.
- listing by task/thread/status filters correctly.
- resolving a message appends a resolution event and derived state becomes `resolved`.
- resolving a message twice fails and does not append another event.
- replies reject unknown `reply_to`.
- evidence refs reject absolute paths and `..` escapes.
- concurrent writers cannot allocate duplicate `event_id` or `message_id`.
- CLI returns non-zero with clear errors and no traceback.
- config default includes `paths.blackboard`.

Docs tests should include the new contract in bilingual inventory if a Chinese pair is added. For v1, one English design spec plus product docs is enough; runtime contract docs can be added in both languages during implementation.

## Acceptance Criteria

- Attestflow has a formal blackboard contract and local append-only storage.
- CLI can post, list, show, and resolve messages.
- Existing task/autopilot/session flows remain fail-closed and are not bypassed by messages.
- Relevant tests pass without network access or model credentials.
- Documentation clearly states this is indirect, auditable Agent coordination, not free-form Agent chat.
