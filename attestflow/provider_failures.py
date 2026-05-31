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
    "timeout": "retry_or_reduce_scope",
    "network": "retry_later",
    "failed": "inspect_failure",
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
    if "timed out" in combined or "timeout" in combined:
        return "timeout"
    if "rate limit" in combined or "rate_limited" in combined or "too many requests" in combined:
        return "rate_limited"
    if "context too large" in combined or "context length" in combined or "maximum context" in combined:
        return "context_too_large"
    if "permission denied" in combined or "tool denied" in combined or "not allowed" in combined:
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
