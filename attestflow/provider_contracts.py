from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

from .contracts import validate_planner_output, validate_typed_capability_output


@dataclass(frozen=True)
class ProviderContractFixture:
    name: str
    capability: str
    contract: str


PROVIDER_CONTRACT_FIXTURES = [
    ProviderContractFixture(name="intake", capability="intake", contract="capability-output"),
    ProviderContractFixture(name="planner", capability="planner", contract="planner-output"),
    ProviderContractFixture(name="task", capability="implementer", contract="capability-output"),
    ProviderContractFixture(name="reviewer", capability="reviewer", contract="capability-output"),
    ProviderContractFixture(name="verifier", capability="verifier", contract="capability-output"),
    ProviderContractFixture(name="release", capability="releaser", contract="capability-output"),
]


def run_provider_contract_suite(
    root: Path,
    provider: str,
    *,
    command: str | None = None,
) -> dict[str, Any]:
    fixtures = []
    for fixture in PROVIDER_CONTRACT_FIXTURES:
        result = _run_fixture(root, provider, fixture, command=command)
        fixtures.append(result)
    status = "passed" if all(item["status"] == "passed" for item in fixtures) else "failed"
    return {
        "schema_version": 1,
        "provider": provider,
        "status": status,
        "fixtures": fixtures,
    }


def _run_fixture(
    root: Path,
    provider: str,
    fixture: ProviderContractFixture,
    *,
    command: str | None,
) -> dict[str, Any]:
    payload = _fixture_payload(root, provider, fixture, command=command)
    adapter_path = Path(__file__).resolve().parent / "capability_adapters.py"
    completed = subprocess.run(
        [sys.executable, str(adapter_path)],
        cwd=root,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return {
            "name": fixture.name,
            "capability": fixture.capability,
            "status": "failed",
            "errors": [completed.stderr.strip() or f"adapter exited {completed.returncode}"],
        }
    try:
        output = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return {
            "name": fixture.name,
            "capability": fixture.capability,
            "status": "failed",
            "errors": [f"adapter stdout was not JSON: {exc}"],
        }
    errors = _fixture_errors(output, fixture)
    return {
        "name": fixture.name,
        "capability": fixture.capability,
        "status": "passed" if not errors else "failed",
        "errors": errors,
    }


def _fixture_payload(
    root: Path,
    provider: str,
    fixture: ProviderContractFixture,
    *,
    command: str | None,
) -> dict[str, Any]:
    provider_options: dict[str, Any] = {}
    if command:
        provider_options["command"] = command
    task = {
        "schema_version": 1,
        "id": "TASK-0001",
        "title": "Provider contract fixture",
        "state": "in_progress",
        "priority": 1,
        "type": "feature",
        "purpose": "Verify provider contract handling.",
        "scope": ["provider contract"],
        "out_of_scope": ["real project edits"],
        "requirements": {"confirmed": ["fixture must pass"], "unresolved": [], "assumptions": []},
        "bdd_scenarios": ["Given fixture, when provider runs, then contract JSON is returned."],
        "unit_tests": ["tests/unit/test_provider_contracts.py"],
        "acceptance": ["provider contract fixture passes"],
        "dependencies": [],
        "blocks": [],
        "blockers": [],
        "files": {"read": [], "write": ["src/provider_contract.py"]},
        "agents": {"owner": "orchestrator", "allowed_roles": ["worker_agent"]},
        "external_inputs": {"credentials": [], "services": [], "user_decisions": []},
        "evidence": {"run_id": "RUN-0001", "session": "harness/runs/RUN-0001/session.yml"},
        "links": {"issues": [], "prs": [], "docs": []},
        "risks": [],
        "notes": [],
    }
    payload: dict[str, Any] = {
        "schema_version": 1,
        "agent_provider": provider,
        "provider_options": provider_options,
        "root": str(root),
        "control_root": str(root),
        "capability": {"name": fixture.capability},
        "task": task,
        "instructions": ["Return only JSON for this fixed provider contract fixture."],
    }
    if fixture.capability == "planner":
        payload["goal"] = "Create one scoped provider contract task."
    if fixture.capability == "releaser":
        payload["done_tasks"] = ["TASK-0001"]
        payload["tasks"] = [{"id": "TASK-0001", "title": "Provider contract fixture"}]
    return payload


def _fixture_errors(output: dict[str, Any], fixture: ProviderContractFixture) -> list[str]:
    if fixture.capability == "planner":
        return validate_planner_output(output, label="planner output")
    return validate_typed_capability_output(output, fixture.capability, label=f"{fixture.capability} output")
