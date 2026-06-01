# Evidence Schema Contract

Date: 2026-05-29
Status: core local gates implemented

Evidence is the audit layer that allows Attestflow to prove what happened. Field names, event names, and filenames stay in English so code and CI can parse them.

## Evidence Types

Core evidence includes:

- task JSON snapshots
- run metadata
- append-only `ledger.jsonl`
- command results for BDD, unit, lint, typecheck, secret scan, and project verify
- capability provider input/output/logs
- session adapter input/output/logs
- Git, CI, PR, and release provider evidence
- close evidence packet
- exported bundle manifest and audit report

## Run Metadata

Run metadata records task id, run id, state, timestamps, workspace, command results, capability runs, provider references, close status, and failure or blocker summary.

## Ledger

`ledger.jsonl` is append-only. Each event includes an event name, timestamp, payload, `previous_hash`, and `hash`. This makes post-hoc evidence tampering detectable.

## Close Evidence

A task can close only when current-run evidence proves the configured Definition of Done. Close evidence must agree with:

- task id
- run id
- task state
- verification result
- required command results
- linked capability and provider evidence

Mismatched task/run/evidence identifiers fail closed.

## Exported Bundles

`evidence export` and `evidence bundle` copy task, run, ledger, provider output, release evidence, PR comment artifacts, manifest, and audit report into an output directory. `evidence verify --check-source` verifies hashes, sizes, and source-evidence drift.

## Retention

`evidence maintain` can redact secrets, compact oversized logs, and garbage-collect old runtime evidence according to retention policy. It should be run without `--apply` first when auditing impact.
