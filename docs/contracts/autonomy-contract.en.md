# Autonomy Contract

Attestflow's autonomy boundary is deterministic. The system automatically advances engineering steps that can be proven by configuration, state, locks, and evidence. When credentials, authorization, external state, or business judgment are missing, it writes a structured blocker instead of pretending the work is complete.

## Core Rules

- Autonomous runs may plan, import, dispatch, run capabilities, verify, collect delivery evidence, and close tasks only through Attestflow state transitions.
- The runtime source of truth is on disk: `harness.yml`, task JSON, locks, run metadata, ledgers, and evidence files.
- Conversation memory is never the source of truth for task state or recovery.
- External systems are accessed only through configured provider contracts.
- Ambiguous or unsafe continuation returns `blocked`, `failed`, or `paused` with evidence.

## Allowed Continuous Path

The intended autonomous path is:

```text
requirement source -> draft spec -> approved spec -> planner capability
-> task import -> ready task selection -> dispatch/session
-> bdd -> tdd -> implementer -> reviewer -> verifier -> verify -> accepted
-> publish/pr/ci/release evidence where configured -> close
```

Raw goals and PRDs can only create draft specs through `attestflow go <requirement source>`. Only an approved spec can enter planner/autopilot. Every step appends ledger evidence. Steps that require external status may pause and resume; they do not silently skip evidence.

## Blockers

Use structured blockers for:

- missing credentials or login
- missing user approval
- unavailable CI/PR/release state
- unresolved business decisions
- invalid task boundaries
- provider timeout, invalid output, or denied tool access

Each blocker records reason, unblock condition, owner, source, status, and timestamps. A task can leave `blocked` only when active blockers are resolved.

## Completion

Autonomy can call work complete only when task state, run evidence, verification result, and close evidence all agree. If any part is missing or stale, the run must remain non-terminal or fail closed.
