# Release Provider Schema 契约

日期：2026-05-30
状态：`release status` command provider 已实现

## 目标

Release provider 是 Attestflow 和外部发布系统之间的边界。包仓库、部署平台、release note 生成器或自建发布脚本只通过这个 contract 接入；Attestflow core 不依赖任何发布 SDK。

Attestflow 负责确定性工作：构造 provider input、汇总已完成任务和交付 evidence、执行命令、保存 `input.json`、`stdout.log`、`stderr.log`、`output.json`，并校验统一 release JSON。Release provider 只负责执行或读取发布状态并返回机器可校验的 JSON。

## 配置

```yaml
paths:
  release_runs: harness/release-runs

integrations:
  release_provider:
    provider: command
    command: your-release-command
    timeout_seconds: 60
```

`command` 从 stdin 读取 JSON object，向 stdout 输出 release output JSON。stderr/stdout 会保存到 `harness/release-runs/release-*/`。`timeout_seconds` 可放在 provider 顶层或 `provider_options` 中；超时会终止 provider process group，写入 stderr log，并让本次 release status 失败。

## Provider Input

```json
{
  "schema_version": 1,
  "provider": "command",
  "provider_options": {},
  "root": "/absolute/project",
  "project": {"name": "example-project"},
  "done_tasks": ["TASK-0001"],
  "release_handoff": {
    "path": "harness/capability-runs/releaser-.../output.json",
    "exists": true,
    "tasks": ["TASK-0001"],
    "output": {
      "schema_version": 1,
      "status": "passed",
      "summary": "Release handoff ready",
      "findings": [],
      "evidence": ["release notes drafted"]
    }
  },
  "tasks": [
    {
      "id": "TASK-0001",
      "title": "Ship login",
      "state": "done",
      "type": "feature",
      "purpose": "Release notes need task context.",
      "scope": ["login flow"],
      "acceptance": ["login released"],
      "links": {"issues": [], "prs": [], "docs": []},
      "evidence": {
        "run_id": "2026-05-30T00-00-00Z-TASK-0001",
        "packet": {"path": "harness/runs/.../evidence.md", "exists": true},
        "ci": {
          "path": "harness/ci-runs/ci-.../output.json",
          "exists": true,
          "output": {"schema_version": 1, "status": "passed", "summary": "ci passed"}
        },
        "pr": {
          "path": "harness/pr-runs/pr-.../output.json",
          "exists": true,
          "output": {"schema_version": 1, "status": "merged", "summary": "PR merged"}
        }
      }
    }
  ]
}
```

`done_tasks` 保持为兼容的 task id 索引。`tasks` 是 release provider 的主输入：它只包含通过 registry 不变量和 schema 校验的 `done` / `archived` 任务，且会按 `done_tasks` 顺序过滤。`done_tasks` 里声明的任务必须出现在 `tasks` 摘要中，否则 Attestflow 会在创建 release run 前失败。配置了 `capabilities.releaser` 时，autopilot 会先运行 releaser capability，并把 `release_handoff` 传给 release provider；`release_handoff.tasks` 是生成该 handoff 时覆盖的 done task ids。`evidence.*.path` 和 `release_handoff.path` 是项目内相对路径或绝对路径；当任务 evidence 路径指向 JSON/YAML 文件时，Attestflow 必须能解析并放入 `output`，否则会 fail closed，避免发布输入隐藏损坏证据。

## Provider Output

```json
{
  "schema_version": 1,
  "provider": "internal-release",
  "status": "released",
  "summary": "Release completed",
  "external_id": "rel-123",
  "url": "https://release.example/rel-123",
  "artifacts": [
    {"name": "package", "url": "https://release.example/pkg"}
  ]
}
```

字段规则：

- `schema_version` 必须为 `1`。
- `status` 只能是 `released`、`skipped`、`running`、`queued`、`blocked`、`failed` 或 `unknown`。
- `summary` 必须非空。
- `artifacts` 如果存在，必须是 list。
- `released` 表示发布已完成。
- `skipped` 表示项目策略明确不需要发布。
- `running`、`queued` 和 `unknown` 表示发布系统尚未收敛；autopilot 会记录 evidence，暂停为 `status: paused` / `pause_reason: external_status_pending`，并允许 `--resume` 重新采集状态。
- `blocked` 表示发布系统缺少外部输入、授权或服务状态，autopilot 会停在 blocked。
- `failed` 表示发布系统返回了明确失败；如果 planner capability 已配置，autopilot 会把失败摘要和 release evidence 交给 planner 生成修复任务，否则本轮标记为 failed。

## Evidence

每次 `attestflow release status` 会创建：

```text
harness/release-runs/release-<timestamp>/
  input.json
  stdout.log
  stderr.log
  output.json
```

如果配置了 `integrations.release_provider`，当所有任务都已 `done` 或 `archived` 且没有新的可执行任务时，`autopilot --run` / `--resume` 会把已完成任务摘要和 PR/CI evidence 汇总给 release provider，采集 release evidence，并把 `output.json` 的相对路径写入 autopilot `metadata.json.release`，把 provider `status` 写入 `metadata.json.release_status`。如果同时配置了 `capabilities.releaser`，autopilot 会先保存 `metadata.json.releaser` 和 `metadata.json.releaser_tasks`，然后再调用 release provider。只有 `released` 或 `skipped` 会让 release gate 收敛；`running`、`queued` 或 `unknown` 会让本轮 paused，下一次 `autopilot --resume` 重新采集；`blocked` 会让本轮 blocked；`failed` 会触发 release repair planning，planner goal 会包含 release evidence 和可选 release handoff summary，成功导入修复任务时写入 `metadata.json.release_repair_planner` 并回到普通 task loop，planner 不可用或失败时本轮 failed。
