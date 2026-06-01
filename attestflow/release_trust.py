from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
import subprocess
import tomllib
from typing import Any

from .io import dump_data


def generate_release_trust(
    root: Path,
    config: dict[str, Any],
    *,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    output_dir = output_dir or root / "harness" / "release-trust"
    output_dir.mkdir(parents=True, exist_ok=True)
    project = _project_metadata(root)
    checks = _release_checks(root)
    sbom = _sbom(project)
    provenance = _provenance(root, config, checks)
    dump_data(sbom, output_dir / "sbom.json")
    dump_data(provenance, output_dir / "provenance.json")
    (output_dir / "checklist.md").write_text(_checklist_markdown(checks), encoding="utf-8")
    status = "failed" if any(check["status"] == "failed" for check in checks) else "passed"
    report = {
        "schema_version": 1,
        "status": status,
        "output_dir": str(output_dir),
        "checks": checks,
        "artifacts": {
            "sbom": str(output_dir / "sbom.json"),
            "provenance": str(output_dir / "provenance.json"),
            "checklist": str(output_dir / "checklist.md"),
            "manifest": str(output_dir / "manifest.json"),
        },
    }
    dump_data(report, output_dir / "report.json")
    dump_data(_artifact_manifest(output_dir, ["sbom.json", "provenance.json", "checklist.md", "report.json"]), output_dir / "manifest.json")
    return report


def _project_metadata(root: Path) -> dict[str, Any]:
    path = root / "pyproject.toml"
    if not path.exists():
        return {"name": root.name, "version": "0.0.0", "dependencies": []}
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    project = data.get("project", {}) if isinstance(data, dict) else {}
    return {
        "name": str(project.get("name") or root.name),
        "version": str(project.get("version") or "0.0.0"),
        "dependencies": [str(item) for item in project.get("dependencies", [])] if isinstance(project.get("dependencies", []), list) else [],
    }


def _sbom(project: dict[str, Any]) -> dict[str, Any]:
    packages = [
        {
            "type": "application",
            "name": project["name"],
            "version": project["version"],
        }
    ]
    for dependency in project["dependencies"]:
        packages.append(
            {
                "type": "library",
                "name": _dependency_name(dependency),
                "version": _dependency_version(dependency),
                "purl": f"pkg:pypi/{_dependency_name(dependency)}",
            }
        )
    return {
        "schema_version": 1,
        "bom_format": "attestflow-local-sbom",
        "generated_at": _now(),
        "packages": packages,
    }


def _release_checks(root: Path) -> list[dict[str, str]]:
    workflow_text = "\n".join(path.read_text(encoding="utf-8") for path in sorted((root / ".github" / "workflows").glob("*.yml")))
    return [
        _check("pyproject", (root / "pyproject.toml").exists(), "pyproject.toml exists"),
        _check("workflow", bool(workflow_text.strip()), "GitHub Actions workflow exists"),
        _check("python_matrix", all(version in workflow_text for version in ("3.11", "3.12", "3.13")), "workflow covers Python 3.11-3.13"),
        _check("build_artifacts", "python -m build" in workflow_text, "workflow builds wheel and sdist"),
        _check("install_smoke", "install-smoke" in workflow_text, "workflow runs attestflow install smoke"),
        _check("artifact_upload", "upload-artifact" in workflow_text, "workflow uploads release evidence artifacts"),
    ]


def _check(name: str, ok: bool, summary: str) -> dict[str, str]:
    return {"name": name, "status": "passed" if ok else "failed", "summary": summary}


def _provenance(root: Path, config: dict[str, Any], checks: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_at": _now(),
        "source_root": str(root),
        "project": config.get("project", {}),
        "git": {
            "commit": _git(root, "rev-parse", "HEAD"),
            "dirty": bool(_git(root, "status", "--porcelain")),
        },
        "checks": checks,
        "materials": _materials(root),
    }


def _materials(root: Path) -> list[dict[str, str]]:
    paths = [root / "pyproject.toml", *sorted((root / ".github" / "workflows").glob("*.yml"))]
    return [{"path": _relative(root, path)} for path in paths if path.exists()]


def _artifact_manifest(output_dir: Path, names: list[str]) -> dict[str, Any]:
    artifacts = []
    for name in names:
        path = output_dir / name
        if not path.exists():
            continue
        artifacts.append({"path": name, "sha256": _sha256(path), "size": path.stat().st_size})
    return {"schema_version": 1, "generated_at": _now(), "artifacts": artifacts}


def _checklist_markdown(checks: list[dict[str, str]]) -> str:
    lines = ["# Release Trust Checklist", ""]
    for check in checks:
        marker = "x" if check["status"] == "passed" else " "
        lines.append(f"- [{marker}] {check['name']}: {check['summary']}")
    lines.append("")
    return "\n".join(lines)


def _dependency_name(specifier: str) -> str:
    match = re.match(r"([A-Za-z0-9_.-]+)", specifier)
    return (match.group(1) if match else specifier).lower()


def _dependency_version(specifier: str) -> str | None:
    match = re.search(r"(==|>=|<=|~=|>|<)\s*([^,;\s]+)", specifier)
    return match.group(2) if match else None


def _git(root: Path, *args: str) -> str:
    try:
        completed = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=False, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _relative(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)
