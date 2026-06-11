from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .contract_statuses import (
    CI_STATUSES,
    GIT_STATUSES,
    PR_STATUSES,
    RELEASE_STATUSES,
    SESSION_STATUSES,
    TASK_CAPABILITY_STATUSES,
)
from .config import DEFAULT_CONFIG
from .io import dump_data, load_data
from . import __version__


SUPPORTED_SCHEMA_VERSIONS = (1,)
PROVIDER_CONTRACT_VERSION = 1

SCHEMA_TYPES = (
    "planner-output",
    "capability-output",
    "session-launch-output",
    "session-resume-output",
    "ci-output",
    "git-output",
    "pr-output",
    "release-output",
    "blackboard-event",
    "agent-message",
    "task",
)


def governance_policy() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "attestflow_version": __version__,
        "supported_schema_versions": list(SUPPORTED_SCHEMA_VERSIONS),
        "provider_contract_version": PROVIDER_CONTRACT_VERSION,
        "backward_compatibility": (
            "compatibility is maintained for schema_version 1 documents; older missing-version documents must pass "
            "schema migrate before runtime use, and future versions are rejected until an explicit migrator exists"
        ),
        "stable_release_flow": [
            "run unit and bdd suites",
            "run provider smoke and contract suite for configured providers",
            "run install-smoke across supported install modes before publishing",
            "export evidence bundle and verify manifest hashes",
            "publish release only after schema and contract docs match exported JSON schemas",
        ],
        "pre_1_0_breaking_changes": (
            "pre-1.0 breaking changes require a migration path, documented compatibility impact, regenerated JSON "
            "schema/OpenAPI output, and release notes before merge"
        ),
    }


def migrate_file(path: Path, *, kind: str, write: bool = False) -> dict[str, Any]:
    value = load_data(path)
    result = migrate_document(value, kind=kind)
    if write:
        dump_data(result["output"], path)
    result["path"] = str(path)
    return result


def migrate_document(value: dict[str, Any], *, kind: str) -> dict[str, Any]:
    if kind != "harness-config":
        raise ValueError(f"unsupported migration kind: {kind}")
    if not isinstance(value, dict):
        raise ValueError("schema migration input must be a mapping")
    output = deepcopy(value)
    migrations: list[str] = []
    version = output.get("schema_version")
    if version is None:
        output["schema_version"] = 1
        migrations.append("add schema_version")
    elif version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ValueError(f"unsupported schema_version {version!r}; supported: {', '.join(map(str, SUPPORTED_SCHEMA_VERSIONS))}")

    _merge_missing(output, DEFAULT_CONFIG, "", migrations)
    return {
        "schema_version": 1,
        "kind": kind,
        "changed": bool(migrations),
        "migrations": migrations,
        "output": output,
    }


def _merge_missing(target: dict[str, Any], defaults: dict[str, Any], prefix: str, migrations: list[str]) -> None:
    for key, default in defaults.items():
        path = f"{prefix}.{key}" if prefix else key
        if key not in target:
            target[key] = deepcopy(default)
            migrations.append(f"add {path}")
            continue
        if isinstance(target.get(key), dict) and isinstance(default, dict):
            _merge_missing(target[key], default, path, migrations)


def json_schema_for(schema_type: str, *, strict: bool = False) -> dict[str, Any]:
    if schema_type not in SCHEMA_TYPES:
        raise ValueError(f"unknown schema type: {schema_type}")
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"https://attestflow.local/schemas/{schema_type}.schema.json",
        "title": f"Attestflow {schema_type}",
        "type": "object",
        "additionalProperties": not strict,
        "required": ["schema_version"],
        "properties": {
            "schema_version": {"const": 1},
        },
    }
    if schema_type != "task":
        schema["properties"]["contract_version"] = {"const": PROVIDER_CONTRACT_VERSION}
        schema["properties"]["usage"] = _usage_schema(strict=strict)
    status_enum = _schema_status_enum(schema_type)
    if status_enum is not None:
        schema["required"].extend(["status", "summary"])
        schema["properties"]["status"] = {"type": "string", "enum": status_enum}
        schema["properties"]["summary"] = {"type": "string", "minLength": 1}
    if schema_type == "planner-output":
        schema["required"].append("tasks")
        schema["properties"]["tasks"] = {"type": "array", "minItems": 1, "items": {"type": "object"}}
    if schema_type == "task":
        schema["required"].extend(["id", "title", "state", "priority", "type"])
        schema["properties"].update(
            {
                "id": {"type": "string", "pattern": "^TASK-[0-9]+$"},
                "title": {"type": "string"},
                "state": {"type": "string"},
                "priority": {"type": "integer"},
                "type": {"type": "string"},
                "source": {"type": "object"},
            }
        )
    if schema_type in {"blackboard-event", "agent-message"}:
        schema["required"].extend(
            ["event_id", "event_type", "message_id", "thread_id", "from_role", "message_type", "body", "status", "created_at"]
        )
        schema["properties"].update(
            {
                "event_id": {"type": "string", "pattern": "^EVT-[0-9]+$"},
                "event_type": {"type": "string", "enum": ["post", "resolve", "supersede"]},
                "message_id": {"type": "string", "pattern": "^MSG-[0-9]+$"},
                "thread_id": {"type": "string", "pattern": "^THREAD-[0-9]+$"},
                "task_id": {"type": ["string", "null"], "pattern": "^TASK-[0-9]+$"},
                "run_id": {"type": ["string", "null"]},
                "from_role": {"type": "string", "minLength": 1},
                "to_role": {"type": ["string", "null"]},
                "message_type": {
                    "type": "string",
                    "enum": ["answer", "blocker", "decision", "finding", "handoff", "note", "question", "status"],
                },
                "body": {"type": "string", "minLength": 1},
                "status": {"type": "string", "enum": ["open", "resolved", "superseded"]},
                "requires_response": {"type": "boolean"},
                "reply_to": {"type": ["string", "null"], "pattern": "^MSG-[0-9]+$"},
                "evidence_refs": {"type": "array", "items": {"type": "string"}},
                "created_at": {"type": "string"},
            }
        )
    return schema


def _schema_status_enum(schema_type: str) -> list[str] | None:
    if schema_type == "capability-output":
        return sorted(TASK_CAPABILITY_STATUSES)
    if schema_type == "session-launch-output":
        return sorted(SESSION_STATUSES["launch"])
    if schema_type == "session-resume-output":
        return sorted(SESSION_STATUSES["resume"])
    if schema_type == "ci-output":
        return sorted(CI_STATUSES)
    if schema_type == "git-output":
        return sorted(GIT_STATUSES)
    if schema_type == "pr-output":
        return sorted(PR_STATUSES)
    if schema_type == "release-output":
        return sorted(RELEASE_STATUSES)
    return None


def _usage_schema(*, strict: bool = False) -> dict[str, Any]:
    non_negative_integer = {"type": "integer", "minimum": 0}
    return {
        "type": "object",
        "additionalProperties": not strict,
        "properties": {
            "provider": {"type": "string", "minLength": 1},
            "model": {"type": "string", "minLength": 1},
            "input_tokens": non_negative_integer,
            "output_tokens": non_negative_integer,
            "total_tokens": non_negative_integer,
            "cached_input_tokens": non_negative_integer,
            "reasoning_tokens": non_negative_integer,
            "cost_usd": {"type": "number", "minimum": 0},
        },
    }


def openapi_document(*, strict: bool = False) -> dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Attestflow provider and runtime contracts",
            "version": __version__,
        },
        "paths": {},
        "components": {
            "schemas": {schema_type: json_schema_for(schema_type, strict=strict) for schema_type in SCHEMA_TYPES},
        },
    }
