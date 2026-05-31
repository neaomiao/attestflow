# PR Provider Schema 契约

日期：2026-05-30
状态：`pr ensure` / `pr status` command provider 已实现

## 目标

PR provider 是 Attestflow 和外部代码托管 / pull request 系统之间的边界。GitHub、GitLab、自建代码平台或本地发布系统只通过这个 contract 接入；Attestflow core 不依赖任何代码托管 SDK。

Attestflow 负责确定性工作：构造 provider input、执行命令、保存 `input.json`、`stdout.log`、`stderr.log`、`output.json`，并校验统一 PR JSON。PR provider 负责按 `action` 创建/更新外部 PR/change，或读取外部 PR/change 状态，并返回机器可校验的 JSON。

## 配置

```yaml
paths:
  pr_runs: harness/pr-runs

integrations:
  pr_provider:
    provider: command
    command: your-pr-command
    timeout_seconds: 30
```

`command` 从 stdin 读取 JSON object，向 stdout 输出 PR output JSON。stderr/stdout 会保存到 `harness/pr-runs/pr-*/`。`timeout_seconds` 可放在 provider 顶层或 `provider_options` 中；超时会终止 provider process group，写入 stderr log，并让本次 PR action 失败。

## Provider Input

```json
{
  "schema_version": 1,
  "action": "ensure",
  "provider": "command",
  "provider_options": {
    "repository": "org/repo",
    "ensure_args": [],
    "status_args": []
  },
  "root": "/absolute/project",
  "project": {"name": "example-project"},
  "task_id": "TASK-0001"
}
```

`action` 只能是：

- `ensure`：创建或更新当前 task 的 PR/change request。provider 应尽量幂等；重复调用同一个 `task_id` 不应创建多个重复 PR。
- `status`：查询当前 task 或当前分支对应的 PR/change 状态。

`task_id` 可以为 `null`，表示人工调用 `pr status` 且没有指定任务。autopilot 调用 `ensure` 和 `status` 时都会传入当前 task id。

## Provider Output

```json
{
  "schema_version": 1,
  "provider": "github",
  "status": "merged",
  "summary": "PR #42 merged",
  "external_id": "42",
  "url": "https://github.com/org/repo/pull/42",
  "branch": "feature/task-0001",
  "target_branch": "main",
  "commit": "abc123",
  "checks": [
    {"name": "review", "status": "passed"}
  ]
}
```

字段规则：

- `schema_version` 必须为 `1`。
- `status` 只能是 `merged`、`open`、`draft`、`blocked`、`failed`、`skipped` 或 `unknown`。
- `summary` 必须非空。
- `checks` 如果存在，必须是 list。
- `merged` 表示 PR/change 已合入目标分支。
- `skipped` 表示项目策略明确不需要 PR，例如本地-only 任务或 release 外部化。
- `ensure` 返回 `open`、`draft`、`merged` 或 `skipped` 表示 PR/change request 已经存在或被策略跳过，autopilot 会继续后续 CI/PR status gate；`blocked` 会让 autopilot 停在 blocked。
- `status` 返回 `merged` 或 `skipped` 才允许 autopilot 继续 close；`unknown` 会让 autopilot 暂停为 `status: paused` / `pause_reason: external_status_pending`，等待 `--resume` 重新采集；`open`、`draft` 或 `blocked` 会让 autopilot 停在 blocked，等待外部系统状态变化或人工决策。

## Evidence

每次 `attestflow pr ensure [TASK-*]` 或 `attestflow pr status [TASK-*]` 会创建：

```text
harness/pr-runs/pr-<timestamp>/
  input.json
  stdout.log
  stderr.log
  output.json
```

如果配置了 `integrations.pr_provider`，`autopilot --run` / `--resume` 会在 accepted task close 前先执行 `pr ensure`，把 `output.json` 的相对路径写入 `task.evidence.pr_request`；随后执行 `pr status`，把 `output.json` 的相对路径写入 `task.evidence.pr`。手动运行 `attestflow pr ensure TASK-*` 或 `attestflow pr status TASK-*` 时也会写回对应 task evidence。PR evidence 不替代本地 `verify --task` 的 DoD evidence。
