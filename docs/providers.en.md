# Provider Cookbook

Providers are the boundary between Attestflow and programming agents. Attestflow owns input shaping, output validation, state, locks, verification, and evidence. Providers generate planner JSON or complete task-scoped capabilities.

## Command Contract

A provider command must:

- Read a JSON object from stdin.
- Write a JSON object to stdout.
- Write diagnostic logs to stderr.
- Avoid direct edits to `harness/tasks/**/*.json`.
- Respect the declared `files.write` boundary.

Planner provider output is defined in `docs/contracts/planner-output-schema.en.md`. A minimal reviewer capability output looks like:

```json
{
  "schema_version": 1,
  "status": "passed",
  "summary": "Implemented and verified the scoped work.",
  "findings": [],
  "evidence": ["tests passed"]
}
```

`bdd`, `tdd`, `implementer`, and `verifier` must also return their required `artifacts` structure; see `docs/contracts/capability-schema.en.md`. `status` is limited to `passed`, `failed`, or `blocked`. If credentials, services, or business decisions are missing, return `blocked` instead of fabricating success.

## Usage Reporting

Any provider output can include optional `usage`. Attestflow does not estimate token spend; providers fill this only when they know the actual model bill:

```json
{
  "usage": {
    "provider": "codex",
    "model": "gpt-5",
    "input_tokens": 1200,
    "output_tokens": 300,
    "total_tokens": 1500,
    "cached_input_tokens": 0,
    "reasoning_tokens": 0,
    "cost_usd": 0.0123
  }
}
```

Token fields must be non-negative integers and `cost_usd` must be a non-negative number. Successful provider runs save this object as `usage.json`; session launch/resume saves `session-launch-usage.json` or `session-resume-usage.json`. `python -m attestflow usage report --json` aggregates real usage evidence.

## Token Economy Input

Provider input may already be compressed by token economy controls. When budgets are exceeded, `repository_context.documents[]` and `files[]` may contain `summary`, `content_hash`, `cache_key`, and `retrieval` instead of full `content`.

If a provider needs more local context, it should emit dynamic context requests and let the orchestrator run:

```bash
python -m attestflow context resolve --from-json request.json --json
```

Providers should not recursively scan the repository on their own.

## Execution and Safety

Attestflow runs provider commands in argv mode, not through shell expansion. stdout/stderr are captured as evidence and common tokens, secrets, passwords, API keys, and bearer tokens are redacted. Failures write `failure.json` with a classified `type`, `automatic_action`, and `recovery_strategy`.

Security boundary example:

```yaml
security:
  provider_commands:
    allowlist: ["python3", "gh", "glab"]
    max_output_bytes: 1048576
    require_approval_for_irreversible: true
  network:
    mode: provider-owned
  filesystem:
    mode: write-scope-validated
```

Use `allowlist` to restrict executables, `max_output_bytes` to fail closed on oversized output, and approval evidence for irreversible actions. Attestflow does not provide network sandboxing; network policy belongs to the provider CLI, proxy, firewall, or CI environment. The run ledger is hash-chained with `previous_hash` and `hash`.

## Local Debugging

Provider authors can debug output contracts without reading Attestflow source:

```bash
python -m attestflow contract validate planner-output planner-output.json
python -m attestflow contract validate capability-output output.json
python -m attestflow contract validate session-launch-output session-output.json
python -m attestflow contract validate git-output git-output.json
python -m attestflow contract validate ci-output ci-output.json
python -m attestflow contract validate pr-output pr-output.json
python -m attestflow contract validate release-output release-output.json
python -m attestflow provider smoke --provider codex
python -m attestflow provider smoke --provider claude-code
python -m attestflow provider smoke --provider opencode
python -m attestflow provider contract --provider codex
```

Invalid planner JSON or invalid `planner-output` contract output is retried once by default. Each attempt keeps its own capability run directory. Override with `capabilities.planner.provider_options.retry_attempts`.

## Delivery Providers

CI, PR, and release providers share the same evidence path: Attestflow writes `input.json`, stdout/stderr logs, `output.json`, and validates the corresponding contract. Built-in adapters only map common tool JSON into Attestflow contracts; credentials, network, and external permissions remain owned by the underlying CLI.

Built-in CI provider discovery:

```bash
python -m attestflow ci providers
```

Common delivery commands:

```bash
python -m attestflow ci status --task TASK-0001
python -m attestflow ci await --head-sha abc123
python -m attestflow pr ensure TASK-0001
python -m attestflow pr merge TASK-0001
python -m attestflow pr status TASK-0001
python -m attestflow release status
```

Each command writes provider evidence under the configured runtime directories and, when a task id is supplied, writes the evidence reference back to the task.
