from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError as exc:
        print(json.dumps(_capability_output("failed", f"Invalid JSON input: {exc}")))
        return 0
    if not isinstance(payload, dict):
        print(json.dumps(_capability_output("failed", "Input must be a JSON object.")))
        return 0

    capability = payload.get("capability", {})
    name = str(capability.get("name", "planner")) if isinstance(capability, dict) else "planner"
    root = Path(str(payload.get("root") or ".")).resolve()
    project = payload.get("project", {})
    adapter = str(project.get("adapter", "") if isinstance(project, dict) else "")
    if not adapter:
        adapter = _detect_adapter(root)

    if name == "planner":
        print(json.dumps(_planner_output(payload, adapter), indent=2))
        return 0
    if name == "intake":
        print(json.dumps(_intake_output(payload)))
        return 0
    if name == "bdd":
        _write_bdd(root, adapter)
        print(json.dumps(_bdd_output(payload, adapter)))
        return 0
    if name == "tdd":
        _write_unit_tests(root, adapter)
        print(json.dumps(_tdd_output(payload, adapter)))
        return 0
    if name == "implementer":
        _write_implementation(root, adapter)
        print(json.dumps(_implementer_output(payload, adapter)))
        return 0
    if name == "verifier":
        print(json.dumps(_verifier_output(adapter)))
        return 0
    if name in {"reviewer", "releaser"}:
        print(json.dumps(_capability_output("passed", f"Local {name} check passed.", [f"{name} local check"])))
        return 0

    print(json.dumps(_capability_output("blocked", f"Unsupported local capability: {name}")))
    return 0


def _detect_adapter(root: Path) -> str:
    if (root / "package.json").exists():
        return "node"
    return "python"


def _planner_output(payload: dict[str, Any], adapter: str) -> dict[str, Any]:
    goal = str(payload.get("goal") or "Add greeting support")
    if adapter == "attestflow":
        unit_tests = ["tests/unit/test_attestflow_dogfood.py"]
        files = {
            "read": ["README.md", "harness.yml", "attestflow/context.py"],
            "write": [
                "attestflow/dogfood_marker.py",
                "tests/bdd/test_attestflow_dogfood_behavior.py",
                "tests/unit/test_attestflow_dogfood.py",
            ],
        }
    elif adapter == "node":
        unit_tests = ["tests/unit/greeter.test.mjs"]
        files = {
            "read": ["README.md", "package.json"],
            "write": ["greeter.js", "tests/bdd/greeter.behavior.test.mjs", "tests/unit/greeter.test.mjs"],
        }
    else:
        unit_tests = ["tests/unit/test_greeter.py"]
        files = {
            "read": ["README.md"],
            "write": ["greeter.py", "tests/bdd/test_greeter_behavior.py", "tests/unit/test_greeter.py"],
        }
    return {
        "schema_version": 1,
        "goal": goal,
        "tasks": [
            {
                "key": "greeting_helper",
                "title": "Dogfood Attestflow marker" if adapter == "attestflow" else "Add greeting helper",
                "priority": 10,
                "type": "feature",
                "purpose": (
                    "Demonstrate Attestflow managing its own repository with a deterministic marker change."
                    if adapter == "attestflow"
                    else "Demonstrate the Attestflow local provider loop with a small greeting helper."
                ),
                "context": ["Local example provider writes tests and implementation during capability runs."],
                "scope": (
                    ["Create a dogfood marker module", "Create behavior and unit tests", "Verify locally"]
                    if adapter == "attestflow"
                    else ["Create a greeting helper", "Create behavior and unit tests", "Verify locally"]
                ),
                "out_of_scope": ["External services", "Network access", "Real model calls"],
                "requirements": {
                    "confirmed": (
                        ["The marker returns a stable dogfood status string."]
                        if adapter == "attestflow"
                        else ["The helper returns a stable greeting for a supplied name."]
                    ),
                    "unresolved": [],
                    "assumptions": ["This example is intentionally small so the harness workflow is visible."],
                },
                "bdd_scenarios": (
                    ["Given the dogfood marker, it reports that Attestflow is self-managed."]
                    if adapter == "attestflow"
                    else ["Given a name, the helper returns 'Hello, <name>!'."]
                ),
                "unit_tests": unit_tests,
                "acceptance": ["Autopilot moves the task to done with fresh verification evidence."],
                "dependencies": [],
                "files": files,
            }
        ],
    }


def _write_bdd(root: Path, adapter: str) -> None:
    if adapter == "attestflow":
        path = root / "tests" / "bdd" / "test_attestflow_dogfood_behavior.py"
        _write(
            path,
            """import unittest

from attestflow.dogfood_marker import dogfood_status


class AttestflowDogfoodBehaviorTests(unittest.TestCase):
    def test_reports_self_managed_status(self) -> None:
        self.assertEqual(dogfood_status(), "attestflow-self-managed")


if __name__ == "__main__":
    unittest.main()
""",
        )
        return
    if adapter == "node":
        path = root / "tests" / "bdd" / "greeter.behavior.test.mjs"
        _write(
            path,
            """import test from 'node:test';
import assert from 'node:assert/strict';
import { greet } from '../../greeter.js';

test('greets a named user', () => {
  assert.equal(greet('Attestflow'), 'Hello, Attestflow!');
});
""",
        )
        return
    path = root / "tests" / "bdd" / "test_greeter_behavior.py"
    _write(
        path,
        """import unittest

from greeter import greet


class GreeterBehaviorTests(unittest.TestCase):
    def test_greets_named_user(self) -> None:
        self.assertEqual(greet("Attestflow"), "Hello, Attestflow!")


if __name__ == "__main__":
    unittest.main()
""",
    )


def _write_unit_tests(root: Path, adapter: str) -> None:
    if adapter == "attestflow":
        path = root / "tests" / "unit" / "test_attestflow_dogfood.py"
        _write(
            path,
            """import unittest

from attestflow.dogfood_marker import dogfood_status


class AttestflowDogfoodTests(unittest.TestCase):
    def test_dogfood_status_is_stable(self) -> None:
        self.assertEqual(dogfood_status(), "attestflow-self-managed")


if __name__ == "__main__":
    unittest.main()
""",
        )
        return
    if adapter == "node":
        path = root / "tests" / "unit" / "greeter.test.mjs"
        _write(
            path,
            """import test from 'node:test';
import assert from 'node:assert/strict';
import { greet } from '../../greeter.js';

test('trims a supplied name before greeting', () => {
  assert.equal(greet('  Core  '), 'Hello, Core!');
});

test('falls back when the name is blank', () => {
  assert.equal(greet('   '), 'Hello, there!');
});
""",
        )
        return
    path = root / "tests" / "unit" / "test_greeter.py"
    _write(
        path,
        """import unittest

from greeter import greet


class GreeterUnitTests(unittest.TestCase):
    def test_trims_supplied_name_before_greeting(self) -> None:
        self.assertEqual(greet("  Core  "), "Hello, Core!")

    def test_falls_back_when_name_is_blank(self) -> None:
        self.assertEqual(greet("   "), "Hello, there!")


if __name__ == "__main__":
    unittest.main()
""",
    )


def _write_implementation(root: Path, adapter: str) -> None:
    if adapter == "attestflow":
        _write(
            root / "attestflow" / "dogfood_marker.py",
            '''"""Small deterministic artifact created by the Attestflow dogfood run."""


def dogfood_status() -> str:
    return "attestflow-self-managed"
''',
        )
        return
    if adapter == "node":
        _write(
            root / "greeter.js",
            """export function greet(name) {
  const normalized = String(name ?? '').trim() || 'there';
  return `Hello, ${normalized}!`;
}
""",
        )
        return
    _write(
        root / "greeter.py",
        """def greet(name: str) -> str:
    normalized = str(name).strip() or "there"
    return f"Hello, {normalized}!"
""",
    )


def _capability_output(status: str, summary: str, evidence: list[str] | None = None) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": status,
        "summary": summary,
        "findings": [],
        "evidence": evidence or [],
    }


def _intake_output(payload: dict[str, Any]) -> dict[str, Any]:
    goal = str(payload.get("goal") or "").strip()
    vague_goals = {"make it better", "improve this", "fix stuff", "do the thing"}
    if goal.lower() in vague_goals:
        output = _capability_output("blocked", "Goal needs a concrete product or engineering decision.", ["intake decision blocker"])
        output["artifacts"] = {
            "confirmed": [],
            "assumptions": [],
            "decision_blockers": [
                {
                    "id": "DECISION-1",
                    "question": "What user-visible or engineering outcome should this run optimize for?",
                    "owner": "user",
                }
            ],
        }
        return output
    output = _capability_output("passed", "Goal is clear enough to plan.", ["intake brief"])
    output["artifacts"] = {
        "confirmed": [goal or "Run the local Attestflow dogfood task."],
        "assumptions": ["Use the deterministic local provider for dogfood evidence."],
        "decision_blockers": [],
    }
    return output


def _bdd_output(payload: dict[str, Any], adapter: str) -> dict[str, Any]:
    output = _capability_output("passed", f"Wrote {adapter} BDD example.", ["bdd test file"])
    output["artifacts"] = {
        "scenarios": [
            {
                "name": "Greets a named user",
                "given": "a supplied name",
                "when": "the greeting helper runs",
                "then": "it returns Hello, <name>!",
            }
        ],
        "updated_files": _existing_write_files(payload, _bdd_files(adapter)),
        "requirements_mapping": [{"requirement": "stable greeting", "scenarios": ["Greets a named user"]}],
        "uncovered_behaviors": [],
    }
    return output


def _tdd_output(payload: dict[str, Any], adapter: str) -> dict[str, Any]:
    output = _capability_output("passed", f"Wrote {adapter} unit test example.", ["unit test file"])
    output["artifacts"] = {
        "red_log": "Unit tests were written before the implementation.",
        "green_log": "Unit tests pass after implementation in the full autopilot loop.",
        "test_files": _existing_write_files(payload, _unit_test_files(adapter)),
        "failing_tests": [],
        "coverage": {"scope": ["greeting helper", "blank-name fallback"]},
    }
    return output


def _implementer_output(payload: dict[str, Any], adapter: str) -> dict[str, Any]:
    output = _capability_output("passed", f"Wrote {adapter} greeting implementation.", ["implementation file"])
    output["artifacts"] = {
        "diff_summary": "Added greeting helper implementation.",
        "written_files": _existing_write_files(payload, _implementation_files(adapter)),
        "incomplete": [],
        "risks": [],
        "command_results": [],
    }
    return output


def _verifier_output(adapter: str) -> dict[str, Any]:
    output = _capability_output("passed", f"Local verifier check passed for {adapter}.", ["verifier local check"])
    output["artifacts"] = {
        "commands": [{"name": "unit", "command": "local example verification", "status": "passed"}],
        "environment": {"adapter": adapter},
        "duration_seconds": 0,
        "flake": {"detected": False},
        "evidence": ["local verifier"],
    }
    return output


def _bdd_files(adapter: str) -> list[str]:
    if adapter == "attestflow":
        return ["tests/bdd/test_attestflow_dogfood_behavior.py"]
    return ["tests/bdd/greeter.behavior.test.mjs"] if adapter == "node" else ["tests/bdd/test_greeter_behavior.py"]


def _unit_test_files(adapter: str) -> list[str]:
    if adapter == "attestflow":
        return ["tests/unit/test_attestflow_dogfood.py"]
    return ["tests/unit/greeter.test.mjs"] if adapter == "node" else ["tests/unit/test_greeter.py"]


def _implementation_files(adapter: str) -> list[str]:
    if adapter == "attestflow":
        return ["attestflow/dogfood_marker.py"]
    return ["greeter.js"] if adapter == "node" else ["greeter.py"]


def _existing_write_files(payload: dict[str, Any], preferred: list[str]) -> list[str]:
    task = payload.get("task", {})
    files = task.get("files", {}) if isinstance(task, dict) and isinstance(task.get("files"), dict) else {}
    write_files = files.get("write", []) if isinstance(files, dict) else []
    if not isinstance(write_files, list):
        return preferred
    allowed = {str(item) for item in write_files}
    selected = [path for path in preferred if path in allowed]
    return selected or [str(write_files[0])] if write_files else preferred


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
