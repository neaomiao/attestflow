from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any


FAILURE_TYPES = {
    "auth_missing",
    "rate_limited",
    "context_too_large",
    "invalid_output",
    "tool_denied",
    "approval_required",
    "output_too_large",
    "timeout",
    "network",
    "failed",
}

AUTOMATIC_ACTIONS = {
    "auth_missing": "block_for_credentials",
    "rate_limited": "retry_later",
    "context_too_large": "shrink_context",
    "invalid_output": "fix_provider_output",
    "tool_denied": "block_for_permission",
    "approval_required": "request_approval",
    "output_too_large": "reduce_provider_output",
    "timeout": "retry_or_reduce_scope",
    "network": "retry_later",
    "failed": "inspect_failure",
}

RECOVERY_STRATEGIES = {
    "auth_missing": [
        "Stop automatic execution.",
        "Ask the user to authenticate the provider CLI or configure credentials.",
        "Rerun provider smoke before resuming the task.",
    ],
    "rate_limited": [
        "Retry after the provider cooldown window.",
        "Lower concurrency or switch to a configured alternate provider.",
    ],
    "context_too_large": [
        "Shrink repository context.",
        "Request targeted files or symbols through the dynamic context protocol.",
        "Retry the same task with the smaller context packet.",
    ],
    "invalid_output": [
        "Retry with a stricter contract-only instruction.",
        "If the second output is invalid, block for provider adapter inspection.",
    ],
    "tool_denied": [
        "Stop the task.",
        "Ask for explicit approval or narrow the write scope.",
        "Retry only after the permission boundary is updated.",
    ],
    "approval_required": [
        "Stop before running the provider command.",
        "Record a human approval file under harness/approvals/ or remove the irreversible action flag.",
        "Retry only after the approval evidence is present.",
    ],
    "output_too_large": [
        "Stop consuming provider output.",
        "Reduce provider verbosity or raise security.provider_commands.max_output_bytes deliberately.",
        "Retry after the provider output is bounded.",
    ],
    "timeout": [
        "Retry once with the same input.",
        "If it times out again, reduce task scope or increase the configured timeout.",
    ],
    "network": [
        "Retry after network connectivity recovers.",
        "Use cached local evidence only for read-only checks.",
    ],
    "failed": [
        "Inspect provider stderr and logs.",
        "Classify the failure manually if no automatic pattern matched.",
    ],
}

RETRIABLE_FAILURES = {"rate_limited", "timeout", "network"}

_ASSIGNMENT_SECRET_RE = re.compile(
    r"\b([A-Za-z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASS|API_KEY|KEY|CREDENTIAL)[A-Za-z0-9_]*)=([^\s]+)"
)
_BEARER_SECRET_RE = re.compile(r"(?i)\b(Bearer)\s+([A-Za-z0-9._~+/=-]+)")


def classify_provider_failure(
    label: str,
    *,
    reason: str | None = None,
    returncode: int | None = None,
    stdout: str = "",
    stderr: str = "",
    error: str = "",
) -> dict[str, Any]:
    failure_type = _failure_type(reason=reason, returncode=returncode, stdout=stdout, stderr=stderr, error=error)
    summary_source = error or stderr or stdout or reason or "provider failed"
    return {
        "schema_version": 1,
        "provider": label,
        "type": failure_type,
        "automatic_action": AUTOMATIC_ACTIONS[failure_type],
        "recovery_strategy": RECOVERY_STRATEGIES[failure_type],
        "retriable": failure_type in RETRIABLE_FAILURES,
        "summary": redact_text(" ".join(str(summary_source).split()))[:500],
        "returncode": returncode,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def redact_text(text: str | bytes | None) -> str:
    if text is None:
        return ""
    if isinstance(text, bytes):
        value = text.decode("utf-8", errors="replace")
    else:
        value = str(text)
    value = _ASSIGNMENT_SECRET_RE.sub(lambda match: f"{match.group(1)}=<redacted>", value)
    value = _BEARER_SECRET_RE.sub(lambda match: f"{match.group(1)} <redacted>", value)
    return value


def _failure_type(
    *,
    reason: str | None,
    returncode: int | None,
    stdout: str,
    stderr: str,
    error: str,
) -> str:
    if reason in FAILURE_TYPES:
        return str(reason)
    combined = " ".join([stdout, stderr, error, str(reason or "")]).lower()
    if "approval required" in combined:
        return "approval_required"
    if "output too large" in combined or "output_too_large" in combined:
        return "output_too_large"
    if "timed out" in combined or "timeout" in combined:
        return "timeout"
    if "rate limit" in combined or "rate_limited" in combined or "too many requests" in combined:
        return "rate_limited"
    if "context too large" in combined or "context length" in combined or "maximum context" in combined:
        return "context_too_large"
    if (
        "permission denied" in combined
        or "operation not permitted" in combined
        or "readonly database" in combined
        or "tool denied" in combined
        or "not allowed" in combined
    ):
        return "tool_denied"
    if "unauthorized" in combined or "not authenticated" in combined or "not logged in" in combined:
        return "auth_missing"
    if (
        "0 credentials" in combined
        or "missing credential" in combined
        or "missing api key" in combined
        or "authentication required" in combined
        or "auth missing" in combined
    ):
        return "auth_missing"
    if "network" in combined or "dns" in combined or "connection refused" in combined or "could not resolve" in combined:
        return "network"
    if reason == "invalid_json" or "valid json" in combined or "json object" in combined:
        return "invalid_output"
    if returncode is not None and returncode < 0:
        return "timeout"
    return "failed"
