# Node Adapter

用于 Node 项目。`attestflow init --adapter node` 会检测 `pnpm-lock.yaml`、`yarn.lock`，否则回退到 `npm`，并把 package scripts 映射到 `harness.yml`：

- `test` -> `commands.unit`
- `lint` -> `commands.lint`
- `typecheck` -> `commands.typecheck`
- `build` -> `commands.project_verify`

项目仍可在 `harness.yml` 显式覆盖任意命令。
