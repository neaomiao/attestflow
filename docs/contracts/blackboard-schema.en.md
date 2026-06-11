# Agent Blackboard Contract

Date: 2026-06-11
Status: runtime store and CLI implemented

The agent blackboard is the stable protocol for cross-agent coordination. It is also the concrete `agent_messages` contract: agents do not mutate each other's files or private state to communicate. They append scoped messages to the blackboard and Attestflow derives the current message state from the event log.

Field names, state names, event names, and command names stay in English because code, CI, and provider adapters parse them.

## Location

The blackboard root is configured by `paths.blackboard` and defaults to:

```text
harness/blackboard/
```

Runtime files:

```text
harness/blackboard/messages.jsonl
harness/blackboard/blackboard.lock
```

`messages.jsonl` is append-only. Writers must hold `blackboard.lock` while reading the current event log, allocating IDs, and appending a new event.

## Event Log

Every line in `messages.jsonl` is one JSON object with `schema_version: 1`.

Required fields for all events:

- `event_id`: `EVT-0001`, monotonically allocated from the current log
- `event_type`: `post`, `resolve`, or `supersede`
- `message_id`: `MSG-0001`
- `thread_id`: `THREAD-0001`
- `task_id`: optional `TASK-*`
- `run_id`: optional task run id
- `from_role`: non-empty protocol role
- `to_role`: optional protocol role
- `message_type`: `question`, `answer`, `finding`, `decision`, `handoff`, `blocker`, `status`, or `note`
- `body`: non-empty human-readable content
- `requires_response`: boolean
- `status`: `open`, `resolved`, or `superseded`
- `reply_to`: optional `MSG-*`
- `evidence_refs`: relative paths to existing project files
- `created_at`: UTC timestamp

## Event Semantics

`post` creates a new message and must use `status: open`.

`resolve` appends a terminal event to an existing open message and must use `status: resolved`.

`supersede` appends a terminal event to an existing open message and must use `status: superseded`. It is reserved by the contract; the first runtime CLI exposes `post`, `list`, `show`, and `resolve`.

Reading the blackboard replays events in file order. A terminal message cannot receive another terminal event. Malformed JSONL, duplicate message IDs, missing referenced messages, invalid event types, or invalid statuses fail closed.

## Scope Rules

`task_id` is optional for project-level messages. If present, it must reference an existing runtime task.

`reply_to` must reference an existing message. If a post omits `thread_id` and sets `reply_to`, it inherits the replied message's `thread_id`; otherwise a new thread ID is allocated.

`evidence_refs` must be relative paths that stay under the project root and already exist. This keeps blackboard claims tied to auditable artifacts instead of untracked side channels.

## Library API

The runtime API is:

- `post_blackboard_message(...)`
- `list_blackboard_messages(...)`
- `show_blackboard_message(...)`
- `resolve_blackboard_message(...)`

The API returns derived `BlackboardMessage` records. `show_blackboard_message(..., include_events=True)` includes the underlying event history.

## CLI

```bash
python -m attestflow blackboard post --from-role reviewer --to-role implementer --type finding --body "Missing retry boundary." --requires-response
python -m attestflow blackboard list --status open --json
python -m attestflow blackboard show MSG-0001 --events --json
python -m attestflow blackboard resolve MSG-0001 --from-role implementer --body "Added retry boundary."
```

The CLI returns non-zero with `ERROR:` on invalid input or corrupted blackboard state.

## Control-Plane Boundary

The blackboard is not a replacement for task state, locks, run ledger, or evidence. It is the coordination layer between agents. The orchestrator still owns task transitions, lock enforcement, final integration, and verification.
