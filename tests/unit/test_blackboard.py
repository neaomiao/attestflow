from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from attestflow.blackboard import (
    list_blackboard_messages,
    post_blackboard_message,
    resolve_blackboard_message,
    show_blackboard_message,
)
from attestflow.config import DEFAULT_CONFIG
from attestflow.io import dump_data


class BlackboardTests(unittest.TestCase):
    def test_post_assigns_ids_and_list_returns_derived_message(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _config()

            message = post_blackboard_message(
                root,
                config,
                from_role="reviewer",
                to_role="implementer",
                message_type="finding",
                body="Missing lockout criterion.",
                requires_response=True,
            )

            self.assertEqual(message.message_id, "MSG-0001")
            self.assertEqual(message.thread_id, "THREAD-0001")
            self.assertEqual(message.status, "open")
            self.assertEqual(message.from_role, "reviewer")
            self.assertEqual(message.to_role, "implementer")
            self.assertEqual(message.body, "Missing lockout criterion.")
            self.assertEqual(message.requires_response, True)
            self.assertEqual(message.events, None)

            messages = list_blackboard_messages(root, config)
            self.assertEqual([item.message_id for item in messages], ["MSG-0001"])

            events = _events(root)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["event_id"], "EVT-0001")
            self.assertEqual(events[0]["event_type"], "post")
            self.assertEqual(events[0]["message_id"], "MSG-0001")

    def test_resolve_appends_terminal_event_and_show_includes_events(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _config()
            post_blackboard_message(root, config, from_role="reviewer", message_type="question", body="Need proof?")

            resolved = resolve_blackboard_message(
                root,
                config,
                "MSG-0001",
                from_role="implementer",
                body="Added proof.",
            )

            self.assertEqual(resolved.status, "resolved")
            self.assertEqual(resolved.body, "Added proof.")
            shown = show_blackboard_message(root, config, "MSG-0001", include_events=True)
            self.assertEqual(shown.status, "resolved")
            self.assertEqual(len(shown.events or []), 2)
            self.assertEqual((shown.events or [])[1]["event_type"], "resolve")
            self.assertEqual((shown.events or [])[1]["event_id"], "EVT-0002")

    def test_filters_by_task_thread_and_status(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _config()
            _write_task(root, "TASK-0001")
            first = post_blackboard_message(
                root,
                config,
                task_id="TASK-0001",
                from_role="reviewer",
                thread_id="THREAD-0042",
                message_type="finding",
                body="Task scoped.",
            )
            post_blackboard_message(root, config, from_role="planner", message_type="note", body="Global note.")
            resolve_blackboard_message(root, config, first.message_id, from_role="implementer", body="Fixed.")

            self.assertEqual(
                [item.message_id for item in list_blackboard_messages(root, config, task_id="TASK-0001")],
                ["MSG-0001"],
            )
            self.assertEqual(
                [item.message_id for item in list_blackboard_messages(root, config, thread_id="THREAD-0042")],
                ["MSG-0001"],
            )
            self.assertEqual(
                [item.message_id for item in list_blackboard_messages(root, config, status="open")],
                ["MSG-0002"],
            )
            self.assertEqual(
                [item.message_id for item in list_blackboard_messages(root, config, status="resolved")],
                ["MSG-0001"],
            )

    def test_rejects_unknown_task(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "unknown task id: TASK-9999"):
                post_blackboard_message(
                    Path(tmp),
                    _config(),
                    task_id="TASK-9999",
                    from_role="reviewer",
                    body="Bad task.",
                )

    def test_rejects_unknown_reply_to(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "reply_to message does not exist: MSG-9999"):
                post_blackboard_message(
                    Path(tmp),
                    _config(),
                    from_role="reviewer",
                    body="Reply.",
                    reply_to="MSG-9999",
                )

    def test_rejects_invalid_message_inputs(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _config()
            with self.assertRaisesRegex(ValueError, "from_role is required"):
                post_blackboard_message(root, config, from_role="", body="Body.")
            with self.assertRaisesRegex(ValueError, "body is required"):
                post_blackboard_message(root, config, from_role="reviewer", body=" ")
            with self.assertRaisesRegex(ValueError, "invalid message_type"):
                post_blackboard_message(root, config, from_role="reviewer", message_type="chat", body="Body.")

    def test_evidence_refs_must_be_existing_relative_project_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _config()
            evidence = root / "harness/capability-runs/reviewer/output.json"
            evidence.parent.mkdir(parents=True)
            evidence.write_text("{}\n", encoding="utf-8")

            message = post_blackboard_message(
                root,
                config,
                from_role="reviewer",
                body="See evidence.",
                evidence_refs=["harness/capability-runs/reviewer/output.json"],
            )

            self.assertEqual(message.evidence_refs, ["harness/capability-runs/reviewer/output.json"])
            with self.assertRaisesRegex(ValueError, "evidence_refs must be relative"):
                post_blackboard_message(root, config, from_role="reviewer", body="Bad.", evidence_refs=[str(evidence)])
            with self.assertRaisesRegex(ValueError, "evidence_refs must stay under project root"):
                post_blackboard_message(root, config, from_role="reviewer", body="Bad.", evidence_refs=["../outside.json"])
            with self.assertRaisesRegex(FileNotFoundError, "evidence ref does not exist"):
                post_blackboard_message(root, config, from_role="reviewer", body="Bad.", evidence_refs=["missing.json"])

    def test_resolving_unknown_or_terminal_message_fails_without_appending(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _config()
            post_blackboard_message(root, config, from_role="reviewer", body="Question.")
            resolve_blackboard_message(root, config, "MSG-0001", from_role="implementer", body="Resolved.")

            with self.assertRaisesRegex(ValueError, "message is already terminal: MSG-0001"):
                resolve_blackboard_message(root, config, "MSG-0001", from_role="implementer", body="Again.")
            with self.assertRaisesRegex(ValueError, "message does not exist: MSG-9999"):
                resolve_blackboard_message(root, config, "MSG-9999", from_role="implementer", body="Nope.")
            self.assertEqual(len(_events(root)), 2)

    def test_malformed_event_log_fails_closed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            messages = root / "harness/blackboard/messages.jsonl"
            messages.parent.mkdir(parents=True)
            messages.write_text("{not json}\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "invalid blackboard event JSON"):
                list_blackboard_messages(root, _config())


def _config() -> dict:
    return DEFAULT_CONFIG.copy()


def _events(root: Path) -> list[dict]:
    path = root / "harness/blackboard/messages.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _write_task(root: Path, task_id: str) -> None:
    task_path = root / "harness/tasks/ready" / f"{task_id}.json"
    dump_data(
        {
            "schema_version": 1,
            "id": task_id,
            "title": "Task",
            "state": "ready",
            "priority": 1,
            "type": "feature",
            "purpose": "Exercise blackboard task validation.",
            "context": [],
            "scope": ["blackboard"],
            "out_of_scope": [],
            "requirements": {"confirmed": ["task exists"], "unresolved": [], "assumptions": []},
            "bdd_scenarios": ["Message can reference a task."],
            "unit_tests": ["tests/unit/test_blackboard.py"],
            "acceptance": ["task-scoped message is accepted"],
            "dependencies": [],
            "blocks": [],
            "blockers": [],
            "files": {"read": [], "write": ["attestflow/blackboard.py"]},
            "agents": {"owner": "orchestrator", "allowed_roles": []},
            "external_inputs": {"credentials": [], "services": [], "user_decisions": []},
            "evidence": {"session": None, "run_id": None, "red": None, "green": None, "verify": None, "packet": None},
            "links": {"issues": [], "prs": [], "docs": []},
            "risks": [],
            "notes": [],
            "created_at": "2026-06-11T00:00:00+00:00",
            "updated_at": "2026-06-11T00:00:00+00:00",
        },
        task_path,
    )


if __name__ == "__main__":
    unittest.main()
