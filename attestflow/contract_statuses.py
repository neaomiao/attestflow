from __future__ import annotations


TASK_CAPABILITY_STATUSES = ("passed", "failed", "blocked")
SESSION_STATUSES = {"launch": {"launched", "blocked"}, "resume": {"resumed", "blocked"}}
CI_STATUSES = {"passed", "failed", "running", "queued", "cancelled", "skipped", "blocked", "unknown"}
GIT_STATUSES = {"published", "skipped", "blocked", "failed", "unknown"}
PR_STATUSES = {"merged", "open", "draft", "blocked", "failed", "skipped", "unknown"}
RELEASE_STATUSES = {"released", "skipped", "running", "queued", "blocked", "failed", "unknown"}
BLACKBOARD_MESSAGE_TYPES = {
    "question",
    "answer",
    "finding",
    "decision",
    "handoff",
    "blocker",
    "status",
    "note",
}
BLACKBOARD_EVENT_TYPES = {"post", "resolve", "supersede"}
