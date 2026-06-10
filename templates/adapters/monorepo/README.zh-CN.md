# Monorepo Adapter

用于 JavaScript/TypeScript monorepo。`attestflow init --adapter monorepo` 会检测 `pnpm-workspace.yaml`、`turbo.json` 和 `nx.json`，并把 package scripts 映射成 workspace 命令：

- `test` -> `pnpm -r test`
- `lint` -> `pnpm -r run lint`
- `typecheck` -> `pnpm -r run typecheck`
- `build` -> `pnpm -r run build`

如果 workspace 使用自定义任务名，可以在 `harness.yml` 覆盖命令。
