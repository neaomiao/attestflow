# Governance and Versioning

日期：2026-05-31
状态：P3 基础治理已实现

Attestflow 的治理边界是确定性的：schema、provider contract、插件注册和 release policy 必须能被机器读取，不能只存在于说明文字里。

## Schema Migration

当前 runtime schema 版本是 `1`。缺少 `schema_version` 的旧 `harness.yml` / JSON 配置必须先迁移：

```bash
python -m attestflow schema migrate --kind harness-config --from-json harness.yml --write
```

迁移会保留已有值，只补齐缺失的默认字段，例如 `paths.runs`、`paths.sources`、`plugins.directories` 和安全策略。未来如果引入 `schema_version: 2`，必须先新增显式 migrator；当前 runtime 会拒绝未知未来版本，避免静默误读。

## Provider Contract Version

Provider 输出仍要求 `schema_version: 1`。`contract_version` 是可选字段；如果 provider 输出了该字段，当前必须是：

```json
{"contract_version": 1}
```

缺省 `contract_version` 兼容现有 provider；显式错误版本会被 `contract validate` 拒绝。

## JSON Schema and OpenAPI

机器可读 schema 由 CLI 导出：

```bash
python -m attestflow schema export --type task --json
python -m attestflow schema export --type ci-output --json
python -m attestflow schema openapi --json
```

OpenAPI 输出只描述 contract component schema，不声明网络 API。它用于 provider 作者、CI 和发布流程对齐字段要求。

## Plugin Registry

插件注册从 `plugins.directories` 扫描 `plugin.json`，默认目录是 `harness/plugins`：

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

查看注册结果：

```bash
python -m attestflow plugin list --json
```

当前 registry 只负责发现和校验，不执行插件代码。执行能力仍必须通过现有 capability、provider 和 adapter contract 接入。

## Release Policy

稳定发布前至少需要：

- unit 和 BDD 测试通过。
- 配置的 provider smoke 和 contract suite 通过。
- 安装路径通过 `install-smoke`，源码发布还要跑模板镜像校验。
- 顶层 evidence bundle 可以导出并通过 manifest 校验。
- 文档、JSON Schema 和 OpenAPI 输出与实际 contract 一致。

查看机器可读策略：

```bash
python -m attestflow governance policy --json
```

## Pre-1.0 Breaking Changes

`1.0` 前允许破坏性变更，但必须满足四个条件：

- 有迁移路径或明确拒绝旧版本的错误。
- 写清兼容性影响。
- 更新 JSON Schema / OpenAPI 输出。
- 发布说明记录用户需要做什么。
