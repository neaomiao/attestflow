# Security Policy

Attestflow coordinates AI coding agents, so security issues usually involve unsafe execution boundaries, leaked evidence, or provider behavior.

## Supported versions

Until `1.0`, only the current `main` branch is supported.

## Reporting a vulnerability

Open a private report or contact the maintainer directly if the issue could expose secrets, bypass file ownership, forge evidence, or execute commands unexpectedly.

Please include:

- Attestflow version or commit.
- Minimal reproduction steps.
- Whether a provider command, session adapter, CI provider, PR provider, or release provider is involved.
- Relevant `harness/*/metadata.yml` or `ledger.jsonl` snippets with secrets removed.

## Security boundaries

- Provider stdout/stderr is stored as evidence and must not contain secrets.
- Provider commands run with the permissions of the invoking user.
- Attestflow validates contracts and records evidence, but it does not provide an OS-level sandbox for arbitrary provider commands.
- `security.provider_commands.allowlist` can restrict which executables provider commands may launch. Leave it empty only when local operator trust is explicit.
- `security.provider_commands.max_output_bytes` limits combined stdout/stderr before logs are stored; oversized output fails closed and writes truncated evidence.
- `security.provider_commands.sandbox.mode: restricted-env` runs provider commands with a minimal environment plus explicitly listed `allowed_env`; `blocked_env` and `blocked_env_prefixes` are removed before process launch. This reduces accidental credential leakage but is not a process/container jail.
- `security.provider_commands.sandbox.network: disabled` records the network intent in provider input and sets `ATTESTFLOW_NETWORK=disabled` while removing proxy environment variables. It is a local policy signal, not a firewall.
- Provider options with `irreversible: true` require approval evidence before execution. By default Attestflow looks for `harness/approvals/<approval_id>.json` with `approved: true`, or a configured `approval_path`.
- Network access is provider-owned: Attestflow does not firewall provider processes. Configure provider CLIs, credentials, proxies, and network policy outside Attestflow, then verify with `provider smoke`.
- File writes are verified at Attestflow boundaries: task capabilities use write-scope checks and session launch/resume records `session-*-write-scope.json` for add/modify/delete/rename/binary drift.
- Runtime ledgers are hash-chained with `previous_hash` and `hash` fields to make post-hoc evidence tampering detectable.
- `evidence maintain --redact --compact --retention-days N` can redact stored provider logs, compact large local evidence, and garbage-collect old runtime run directories. Run it first without `--apply` when auditing retention impact.
- Use `secret-scan` before closing tasks and before publishing evidence.
