from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any

from .io import dump_data, load_data
from .tasks import TaskRecord, iter_tasks, task_root, validate_task


SOURCE_KINDS = {
    "github-issue": "github_issue",
    "github_issue": "github_issue",
    "linear-ticket": "linear_ticket",
    "linear_ticket": "linear_ticket",
    "jira-ticket": "jira_ticket",
    "jira_ticket": "jira_ticket",
    "pr-review-comment": "pr_review_comment",
    "pr_review_comment": "pr_review_comment",
    "ci-failure": "ci_failure",
    "ci_failure": "ci_failure",
}
TASK_ID_PATTERN = re.compile(r"^TASK-(\d+)$")


def import_source(root: Path, config: dict[str, Any], *, kind: str, source_path: Path) -> TaskRecord:
    raw = load_data(source_path)
    normalized = _normalize_source(kind, raw)
    task_id = f"TASK-{_next_task_number(root, config):04d}"
    evidence_path = _write_source_evidence(root, config, normalized, raw)
    task = _source_task(task_id, normalized, evidence_path)
    errors = validate_task(task, directory_state="proposed")
    if errors:
        raise ValueError("; ".join(errors))
    target = task_root(root, config) / "proposed" / f"{task_id}.json"
    if target.exists():
        raise ValueError(f"task file already exists: {target}")
    dump_data(task, target)
    return TaskRecord(path=target, task=task)


def _normalize_source(kind: str, raw: dict[str, Any]) -> dict[str, Any]:
    normalized_kind = SOURCE_KINDS.get(kind)
    if not normalized_kind:
        raise ValueError(f"unsupported source kind: {kind}")
    external_id = _external_id(normalized_kind, raw)
    title = _title(normalized_kind, raw, external_id)
    body = _body(raw)
    url = _url(raw)
    labels = _list(raw.get("labels"))
    priority = _priority(raw, normalized_kind, labels)
    return {
        "kind": normalized_kind,
        "external_id": external_id,
        "title": title,
        "body": body,
        "url": url,
        "labels": labels,
        "priority": priority,
        "status": str(raw.get("state") or raw.get("status") or ""),
        "raw": raw,
    }


def _write_source_evidence(root: Path, config: dict[str, Any], source: dict[str, Any], raw: dict[str, Any]) -> Path:
    sources_root = _sources_root(root, config)
    source_dir = sources_root / _source_slug(source)
    source_dir.mkdir(parents=True, exist_ok=True)
    evidence = {
        "schema_version": 1,
        "kind": source["kind"],
        "external_id": source["external_id"],
        "url": source.get("url"),
        "priority": source["priority"],
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "raw": raw,
        "normalized": {key: value for key, value in source.items() if key != "raw"},
    }
    path = source_dir / "source.json"
    dump_data(evidence, path)
    return path


def _source_task(task_id: str, source: dict[str, Any], evidence_path: Path) -> dict[str, Any]:
    timestamp = datetime.now(timezone.utc).isoformat()
    evidence_ref = _relative_evidence_path(evidence_path)
    links = _links(source)
    title = str(source["title"]).strip() or f"{source['kind']} {source['external_id']}"
    return {
        "schema_version": 1,
        "id": task_id,
        "title": title,
        "state": "proposed",
        "priority": int(source["priority"]),
        "type": _task_type(source),
        "purpose": "Convert external source evidence into intake/planner work.",
        "context": [_context_summary(source)],
        "scope": [],
        "out_of_scope": [],
        "requirements": {
            "confirmed": [],
            "unresolved": ["Run intake/planner to decompose this external source into ready tasks."],
            "assumptions": [],
        },
        "bdd_scenarios": [],
        "unit_tests": [],
        "acceptance": [],
        "dependencies": [],
        "blocks": [],
        "blockers": [],
        "files": {"read": [], "write": []},
        "agents": {"owner": "orchestrator", "allowed_roles": []},
        "external_inputs": {"credentials": [], "services": [], "user_decisions": []},
        "evidence": {"session": None, "run_id": None, "red": None, "green": None, "verify": None, "packet": None},
        "links": links,
        "source": {
            "kind": source["kind"],
            "external_id": source["external_id"],
            "url": source.get("url"),
            "status": source.get("status"),
            "priority": source["priority"],
            "evidence": evidence_ref,
        },
        "risks": [],
        "notes": ["Imported from external source; keep proposed until intake/planner confirms the work boundary."],
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def _external_id(kind: str, raw: dict[str, Any]) -> str:
    candidates = {
        "github_issue": ("number", "id", "node_id"),
        "linear_ticket": ("identifier", "id", "key"),
        "jira_ticket": ("key", "id", "issue_key"),
        "pr_review_comment": ("id", "comment_id", "review_comment_id"),
        "ci_failure": ("run_id", "id", "job_id"),
    }[kind]
    for key in candidates:
        value = raw.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    raise ValueError(f"{kind} source is missing an external id")


def _title(kind: str, raw: dict[str, Any], external_id: str) -> str:
    for key in ("title", "summary", "name"):
        value = raw.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    if kind == "pr_review_comment":
        return f"Address PR review comment {external_id}"
    if kind == "ci_failure":
        return f"Fix CI failure {external_id}"
    return f"Import {kind} {external_id}"


def _body(raw: dict[str, Any]) -> str:
    for key in ("body", "description", "summary", "message"):
        value = raw.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _url(raw: dict[str, Any]) -> str | None:
    for key in ("url", "html_url", "web_url", "log_url"):
        value = raw.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _priority(raw: dict[str, Any], kind: str, labels: list[str]) -> int:
    explicit = raw.get("priority")
    if isinstance(explicit, int):
        return explicit
    if explicit is not None:
        mapped = _priority_label(str(explicit))
        if mapped is not None:
            return mapped
    severity = raw.get("severity")
    if severity is not None:
        mapped = _priority_label(str(severity))
        if mapped is not None:
            return mapped
    for label in labels:
        mapped = _priority_label(label)
        if mapped is not None:
            return mapped
    if kind == "ci_failure":
        return 20
    if kind == "pr_review_comment":
        return 40
    return 50


def _priority_label(value: str) -> int | None:
    normalized = value.strip().lower().replace("_", "-")
    for separator in (":", "/"):
        if separator in normalized:
            normalized = normalized.split(separator, 1)[1].strip()
            break
    if normalized in {"p0", "blocker", "critical", "urgent"}:
        return 5
    if normalized in {"p1", "high", "major"}:
        return 20
    if normalized in {"p2", "medium", "normal"}:
        return 50
    if normalized in {"p3", "low", "minor"}:
        return 80
    return None


def _task_type(source: dict[str, Any]) -> str:
    labels = {label.lower() for label in _list(source.get("labels"))}
    kind = str(source["kind"])
    if kind == "ci_failure" or "bug" in labels:
        return "bug"
    if kind == "pr_review_comment":
        return "refactor"
    return "feature"


def _context_summary(source: dict[str, Any]) -> str:
    parts = [f"source={source['kind']}:{source['external_id']}"]
    if source.get("url"):
        parts.append(f"url={source['url']}")
    if source.get("body"):
        parts.append(str(source["body"]))
    return " | ".join(parts)


def _links(source: dict[str, Any]) -> dict[str, list[str]]:
    issues: list[str] = []
    prs: list[str] = []
    docs: list[str] = []
    url = source.get("url")
    fallback = f"{source['kind']}:{source['external_id']}"
    if source["kind"] in {"github_issue", "linear_ticket", "jira_ticket"}:
        issues.append(str(url or fallback))
    elif source["kind"] == "pr_review_comment":
        prs.append(str(url or fallback))
    elif source["kind"] == "ci_failure":
        docs.append(str(url or fallback))
    return {"issues": issues, "prs": prs, "docs": docs}


def _next_task_number(root: Path, config: dict[str, Any]) -> int:
    highest = 0
    for record in iter_tasks(root, config):
        match = TASK_ID_PATTERN.match(str(record.task.get("id", "")))
        if match:
            highest = max(highest, int(match.group(1)))
    return highest + 1


def _sources_root(root: Path, config: dict[str, Any]) -> Path:
    paths = config.get("paths", {}) if isinstance(config.get("paths"), dict) else {}
    value = Path(str(paths.get("sources", "harness/sources")))
    return value if value.is_absolute() else root / value


def _source_slug(source: dict[str, Any]) -> str:
    return f"{str(source['kind']).replace('_', '-')}-{_safe_path_part(str(source['external_id']))}"


def _safe_path_part(value: str) -> str:
    safe = [ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in value]
    return "".join(safe).strip("-") or "source"


def _relative_evidence_path(path: Path) -> str:
    parts = path.parts
    if "harness" in parts:
        index = parts.index("harness")
        return str(Path(*parts[index:]))
    return str(path)


def _list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]
