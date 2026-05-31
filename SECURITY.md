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
- Attestflow validates contracts and records evidence, but it does not sandbox arbitrary provider commands.
- Use `secret-scan` before closing tasks and before publishing evidence.
