from __future__ import annotations

from typing import Any

from .tasks import validate_task


def definition_of_ready_errors(task: dict[str, Any]) -> list[str]:
    return validate_task(task, directory_state="ready")


def definition_of_done_errors(task: dict[str, Any]) -> list[str]:
    errors = validate_task(task, directory_state="done")
    evidence = task.get("evidence", {})
    if not isinstance(evidence, dict) or not evidence.get("packet"):
        errors.append("evidence.packet is required when state is done")
    return errors

