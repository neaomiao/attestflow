from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised only on non-POSIX platforms.
    fcntl = None  # type: ignore[assignment]

try:
    import msvcrt
except ImportError:  # pragma: no cover - exercised only on Windows.
    msvcrt = None  # type: ignore[assignment]

from .tasks import iter_tasks


MESSAGE_TYPES = {
    "question",
    "answer",
    "finding",
    "decision",
    "handoff",
    "blocker",
    "status",
    "note",
}
TERMINAL_STATUSES = {"resolved", "superseded"}
EVENT_TYPES = {"post", "resolve", "supersede"}
EVENT_ID_RE = re.compile(r"^EVT-(\d+)$")
MESSAGE_ID_RE = re.compile(r"^MSG-(\d+)$")
THREAD_ID_RE = re.compile(r"^THREAD-(\d+)$")


@dataclass(frozen=True)
class BlackboardMessage:
    message_id: str
    thread_id: str
    status: str
    from_role: str
    to_role: str | None
    message_type: str
    body: str
    requires_response: bool
    task_id: str | None
    run_id: str | None
    reply_to: str | None
    evidence_refs: list[str]
    created_at: str
    updated_at: str
    events: list[dict[str, Any]] | None = None


def blackboard_root(root: Path, config: dict[str, Any]) -> Path:
    return root / config.get("paths", {}).get("blackboard", "harness/blackboard")


def post_blackboard_message(
    root: Path,
    config: dict[str, Any],
    *,
    from_role: str,
    body: str,
    to_role: str | None = None,
    message_type: str = "note",
    requires_response: bool = False,
    task_id: str | None = None,
    run_id: str | None = None,
    thread_id: str | None = None,
    reply_to: str | None = None,
    evidence_refs: list[str] | None = None,
) -> BlackboardMessage:
    _validate_message_inputs(from_role=from_role, body=body, message_type=message_type)
    refs = _validate_evidence_refs(root, evidence_refs or [])
    with _locked_blackboard(root, config):
        events = _read_events(root, config)
        messages = _derive_messages(events, include_events=True)
        _validate_task_id(root, config, task_id)
        if reply_to and reply_to not in messages:
            raise ValueError(f"reply_to message does not exist: {reply_to}")
        resolved_thread_id = thread_id or (messages[reply_to].thread_id if reply_to else _next_id(events, "thread_id", THREAD_ID_RE, "THREAD"))
        now = _utc_now()
        event = {
            "schema_version": 1,
            "event_id": _next_id(events, "event_id", EVENT_ID_RE, "EVT"),
            "event_type": "post",
            "message_id": _next_id(events, "message_id", MESSAGE_ID_RE, "MSG"),
            "thread_id": resolved_thread_id,
            "task_id": task_id,
            "run_id": run_id,
            "from_role": from_role.strip(),
            "to_role": _strip_optional(to_role),
            "message_type": message_type,
            "body": body.strip(),
            "requires_response": bool(requires_response),
            "status": "open",
            "reply_to": reply_to,
            "evidence_refs": refs,
            "created_at": now,
        }
        _append_event(root, config, event)
        return _message_from_event(event)


def resolve_blackboard_message(
    root: Path,
    config: dict[str, Any],
    message_id: str,
    *,
    from_role: str,
    body: str,
    evidence_refs: list[str] | None = None,
) -> BlackboardMessage:
    _validate_message_inputs(from_role=from_role, body=body, message_type="answer")
    refs = _validate_evidence_refs(root, evidence_refs or [])
    with _locked_blackboard(root, config):
        events = _read_events(root, config)
        messages = _derive_messages(events, include_events=True)
        if message_id not in messages:
            raise ValueError(f"message does not exist: {message_id}")
        target = messages[message_id]
        if target.status in TERMINAL_STATUSES:
            raise ValueError(f"message is already terminal: {message_id}")
        now = _utc_now()
        event = {
            "schema_version": 1,
            "event_id": _next_id(events, "event_id", EVENT_ID_RE, "EVT"),
            "event_type": "resolve",
            "message_id": message_id,
            "thread_id": target.thread_id,
            "task_id": target.task_id,
            "run_id": target.run_id,
            "from_role": from_role.strip(),
            "to_role": target.from_role,
            "message_type": "answer",
            "body": body.strip(),
            "requires_response": False,
            "status": "resolved",
            "reply_to": message_id,
            "evidence_refs": refs,
            "created_at": now,
        }
        _append_event(root, config, event)
        updated_events = [*(target.events or []), event]
        return _message_from_terminal_event(target, event, include_events=False, events=updated_events)


def list_blackboard_messages(
    root: Path,
    config: dict[str, Any],
    *,
    task_id: str | None = None,
    thread_id: str | None = None,
    status: str | None = None,
    include_events: bool = False,
) -> list[BlackboardMessage]:
    events = _read_events(root, config)
    messages = _derive_messages(events, include_events=include_events)
    result = list(messages.values())
    if task_id is not None:
        result = [message for message in result if message.task_id == task_id]
    if thread_id is not None:
        result = [message for message in result if message.thread_id == thread_id]
    if status is not None:
        result = [message for message in result if message.status == status]
    return result


def show_blackboard_message(
    root: Path,
    config: dict[str, Any],
    message_id: str,
    *,
    include_events: bool = False,
) -> BlackboardMessage:
    events = _read_events(root, config)
    messages = _derive_messages(events, include_events=include_events)
    if message_id not in messages:
        raise ValueError(f"message does not exist: {message_id}")
    return messages[message_id]


def _read_events(root: Path, config: dict[str, Any]) -> list[dict[str, Any]]:
    path = blackboard_root(root, config) / "messages.jsonl"
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid blackboard event JSON at line {line_number}: {exc}") from exc
        if not isinstance(event, dict):
            raise ValueError(f"invalid blackboard event at line {line_number}: event must be a mapping")
        _validate_event_shape(event, line_number)
        events.append(event)
    return events


def _derive_messages(events: list[dict[str, Any]], *, include_events: bool) -> dict[str, BlackboardMessage]:
    messages: dict[str, BlackboardMessage] = {}
    event_history: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        event_type = str(event["event_type"])
        message_id = str(event["message_id"])
        if event_type == "post":
            if message_id in messages:
                raise ValueError(f"duplicate blackboard message id: {message_id}")
            message = _message_from_event(event, include_events=False)
            messages[message_id] = message
            event_history[message_id] = [event]
            continue
        if message_id not in messages:
            raise ValueError(f"{event_type} event references missing message: {message_id}")
        if messages[message_id].status in TERMINAL_STATUSES:
            raise ValueError(f"message is already terminal: {message_id}")
        event_history[message_id].append(event)
        messages[message_id] = _message_from_terminal_event(
            messages[message_id],
            event,
            include_events=False,
            events=event_history[message_id],
        )
    if include_events:
        return {
            message_id: _replace_events(message, event_history.get(message_id, []))
            for message_id, message in messages.items()
        }
    return messages


def _message_from_event(event: dict[str, Any], *, include_events: bool = False) -> BlackboardMessage:
    return BlackboardMessage(
        message_id=str(event["message_id"]),
        thread_id=str(event["thread_id"]),
        status=str(event.get("status", "open")),
        from_role=str(event["from_role"]),
        to_role=_optional_string(event.get("to_role")),
        message_type=str(event["message_type"]),
        body=str(event["body"]),
        requires_response=bool(event.get("requires_response", False)),
        task_id=_optional_string(event.get("task_id")),
        run_id=_optional_string(event.get("run_id")),
        reply_to=_optional_string(event.get("reply_to")),
        evidence_refs=[str(ref) for ref in event.get("evidence_refs", [])],
        created_at=str(event["created_at"]),
        updated_at=str(event["created_at"]),
        events=[event] if include_events else None,
    )


def _message_from_terminal_event(
    message: BlackboardMessage,
    event: dict[str, Any],
    *,
    include_events: bool,
    events: list[dict[str, Any]],
) -> BlackboardMessage:
    return BlackboardMessage(
        message_id=message.message_id,
        thread_id=message.thread_id,
        status=str(event["status"]),
        from_role=str(event["from_role"]),
        to_role=_optional_string(event.get("to_role")),
        message_type=str(event["message_type"]),
        body=str(event["body"]),
        requires_response=bool(event.get("requires_response", False)),
        task_id=message.task_id,
        run_id=message.run_id,
        reply_to=_optional_string(event.get("reply_to")),
        evidence_refs=[str(ref) for ref in event.get("evidence_refs", [])],
        created_at=message.created_at,
        updated_at=str(event["created_at"]),
        events=events if include_events else None,
    )


def _replace_events(message: BlackboardMessage, events: list[dict[str, Any]]) -> BlackboardMessage:
    return BlackboardMessage(
        message_id=message.message_id,
        thread_id=message.thread_id,
        status=message.status,
        from_role=message.from_role,
        to_role=message.to_role,
        message_type=message.message_type,
        body=message.body,
        requires_response=message.requires_response,
        task_id=message.task_id,
        run_id=message.run_id,
        reply_to=message.reply_to,
        evidence_refs=message.evidence_refs,
        created_at=message.created_at,
        updated_at=message.updated_at,
        events=events,
    )


def _append_event(root: Path, config: dict[str, Any], event: dict[str, Any]) -> None:
    path = blackboard_root(root, config) / "messages.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def _validate_message_inputs(*, from_role: str, body: str, message_type: str) -> None:
    if not from_role.strip():
        raise ValueError("from_role is required")
    if not body.strip():
        raise ValueError("body is required")
    if message_type not in MESSAGE_TYPES:
        raise ValueError(f"invalid message_type: {message_type}")


def _validate_task_id(root: Path, config: dict[str, Any], task_id: str | None) -> None:
    if task_id is None:
        return
    task_ids = {str(record.task.get("id")) for record in iter_tasks(root, config)}
    if task_id not in task_ids:
        raise ValueError(f"unknown task id: {task_id}")


def _validate_evidence_refs(root: Path, refs: list[str]) -> list[str]:
    valid_refs: list[str] = []
    root_resolved = root.resolve()
    for ref in refs:
        path = Path(ref)
        if path.is_absolute():
            raise ValueError("evidence_refs must be relative")
        resolved = (root / path).resolve()
        if not _is_relative_to(resolved, root_resolved):
            raise ValueError("evidence_refs must stay under project root")
        if not resolved.is_file():
            raise FileNotFoundError(f"evidence ref does not exist: {ref}")
        valid_refs.append(str(path))
    return valid_refs


def _validate_event_shape(event: dict[str, Any], line_number: int) -> None:
    required = {"schema_version", "event_id", "event_type", "message_id", "thread_id", "from_role", "message_type", "body", "status", "created_at"}
    missing = sorted(required - set(event))
    if missing:
        raise ValueError(f"invalid blackboard event at line {line_number}: missing {', '.join(missing)}")
    if event.get("schema_version") != 1:
        raise ValueError(f"invalid blackboard event at line {line_number}: schema_version must be 1")
    if event.get("event_type") not in EVENT_TYPES:
        raise ValueError(f"invalid blackboard event at line {line_number}: invalid event_type")
    if event.get("message_type") not in MESSAGE_TYPES:
        raise ValueError(f"invalid blackboard event at line {line_number}: invalid message_type")
    if event.get("event_type") == "post" and event.get("status") != "open":
        raise ValueError(f"invalid blackboard event at line {line_number}: post status must be open")
    if event.get("event_type") == "resolve" and event.get("status") != "resolved":
        raise ValueError(f"invalid blackboard event at line {line_number}: resolve status must be resolved")
    if event.get("event_type") == "supersede" and event.get("status") != "superseded":
        raise ValueError(f"invalid blackboard event at line {line_number}: supersede status must be superseded")


def _next_id(events: list[dict[str, Any]], key: str, pattern: re.Pattern[str], prefix: str) -> str:
    max_value = 0
    for event in events:
        value = event.get(key)
        if value is None:
            continue
        match = pattern.match(str(value))
        if match:
            max_value = max(max_value, int(match.group(1)))
    return f"{prefix}-{max_value + 1:04d}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _strip_optional(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


@contextmanager
def _locked_blackboard(root: Path, config: dict[str, Any]) -> Iterator[None]:
    lock_path = blackboard_root(root, config) / "blackboard.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        elif msvcrt is not None:  # pragma: no cover - Windows-only branch.
            handle.write("\0")
            handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            elif msvcrt is not None:  # pragma: no cover - Windows-only branch.
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
