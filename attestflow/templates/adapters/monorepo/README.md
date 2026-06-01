# Monorepo Adapter

[中文](README.zh-CN.md)

Use this adapter for JavaScript/TypeScript monorepos. `attestflow init --adapter monorepo` detects `pnpm-workspace.yaml`, `turbo.json`, and `nx.json`, then maps package scripts into workspace commands:

- `test` -> `pnpm -r test`
- `lint` -> `pnpm -r run lint`
- `typecheck` -> `pnpm -r run typecheck`
- `build` -> `pnpm -r run build`

Projects can override commands in `harness.yml` when a workspace uses custom task names.
