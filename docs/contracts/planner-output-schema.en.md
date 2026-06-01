# Planner Output Schema Contract

Date: 2026-05-30
Status: base import implemented

Planner output is the stable bridge between a programming agent and Attestflow runtime tasks. Humans should not hand-write runtime task JSON as the normal path.

## Output Shape

A planner provider returns:

```json
{
  "schema_version": 1,
  "tasks": [
    {
      "title": "Short imperative title",
      "priority": 100,
      "type": "feature",
      "purpose": "Why this task exists.",
      "scope": ["What is included."],
      "out_of_scope": ["What is excluded."],
      "requirements": {
        "confirmed": [],
        "unresolved": [],
        "assumptions": []
      },
      "bdd_scenarios": [],
      "unit_tests": [],
      "acceptance": [],
      "dependencies": [],
      "files": {"read": [], "write": []},
      "risks": []
    }
  ]
}
```

Attestflow assigns `TASK-*` ids, resolves planner-local dependencies, fills defaults, validates Definition of Ready, and writes runtime task JSON.

## Requirements

Each task must have purpose, scope, out-of-scope, BDD scenarios, unit tests, acceptance criteria, and declared `files.write`. Missing executable boundaries are rejected instead of imported as half-ready work.

## Source Intake

External tickets, issues, review comments, and CI failures should first enter through `source import`. Planner output then turns that source evidence into executable task boundaries.

## Retry

Invalid JSON or invalid planner contract output can be retried by provider policy. Failed attempts remain as capability evidence.
