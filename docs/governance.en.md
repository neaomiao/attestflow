# Governance and Versioning

Date: 2026-05-31
Status: P3 baseline governance implemented

Attestflow governance is deterministic: schema versions, provider contracts, plugin registration, and release policy must be machine-readable. They cannot live only in prose.

## Schema Migration

The current runtime schema version is `1`. Older `harness.yml` or JSON config files without `schema_version` must be migrated before use:

```bash
python -m attestflow schema migrate --kind harness-config --from-json harness.yml --write
```

Migration preserves existing values and fills missing defaults such as `paths.runs`, `paths.sources`, `plugins.directories`, and security policy. If `schema_version: 2` is introduced later, Attestflow must add an explicit migrator first. The current runtime rejects unknown future versions to avoid silent misreads.

## Provider Contract Version

Provider output still requires `schema_version: 1`. `contract_version` is optional. If a provider emits it, the current accepted value is:

```json
{"contract_version": 1}
```

Omitting `contract_version` preserves compatibility with existing providers. An explicit wrong version is rejected by `contract validate`.

## JSON Schema and OpenAPI

Machine-readable schema can be exported through the CLI:

```bash
python -m attestflow schema export --type task --json
python -m attestflow schema export --type ci-output --json
python -m attestflow schema export --type capability-output --strict --json
python -m attestflow schema openapi --json
python -m attestflow schema openapi --strict --json
```

OpenAPI output describes contract component schemas only. It does not declare a network API. Default schema export stays compatibility-oriented and allows extra provider fields. `--strict` closes `additionalProperties` at the top level and in `usage`, so CI or release gates can catch typo fields and contract drift when a project is ready for that enforcement.

## Plugin Registry

Plugin registration scans `plugin.json` files from `plugins.directories`; the default directory is `harness/plugins`:

```json
{
  "schema_version": 1,
  "name": "demo-plugin",
  "version": "0.1.0",
  "capabilities": ["planner"],
  "providers": {"session": ["demo-agent"]},
  "adapters": ["python"]
}
```

Inspect registered plugins with:

```bash
python -m attestflow plugin list --json
```

The current registry only discovers and validates manifests. Execution still goes through capability, provider, and adapter contracts.

## Release Policy

Before a stable release, at minimum:

- Unit and BDD tests pass.
- Configured provider smoke and contract suites pass.
- Install paths pass `install-smoke`; source releases also run template mirror validation.
- Top-level evidence bundles can be exported and verified by manifest.
- Docs, JSON Schema, and OpenAPI output match the actual contracts.

View the machine-readable policy:

```bash
python -m attestflow governance policy --json
```

## Pre-1.0 Breaking Changes

Breaking changes are allowed before `1.0`, but they must meet four conditions:

- Provide a migration path or an explicit error for old versions.
- Document compatibility impact.
- Update JSON Schema and OpenAPI output.
- Record the required user action in release notes.
