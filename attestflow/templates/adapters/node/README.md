# Node Adapter

[中文](README.zh-CN.md)

Use this adapter for Node projects. `attestflow init --adapter node` detects `pnpm-lock.yaml`, `yarn.lock`, or falls back to `npm`, then maps package scripts into `harness.yml`:

- `test` -> `commands.unit`
- `lint` -> `commands.lint`
- `typecheck` -> `commands.typecheck`
- `build` -> `commands.project_verify`

Projects can still override any command explicitly in `harness.yml`.
