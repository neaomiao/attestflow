# Git Provider Schema 契约

日期：2026-05-31
状态：`publish` command provider 已实现

## 目标

Git provider 是 Attestflow 和本地版本控制交付动作之间的边界。它把 `git add`、`git commit`、`git push` 从外部人工习惯提升为可配置、可审计的 harness 步骤；PR provider 仍负责创建/更新 PR 或读取 PR 状态。

Attestflow 负责确定性工作：构造 provider input、执行命令、保存 `input.json`、`stdout.log`、`stderr.log`、`output.json`，并校验统一 Git JSON。Git provider 负责提交并推送当前分支，或者返回阻塞原因。

## 配置

```yaml
paths:
  git_runs: harness/git-runs

integrations:
  git_provider:
    provider: git
    provider_options:
      remote: origin
      push: true
```

`provider: git` 使用内置 adapter；`provider: command` 可接入自定义交付脚本。`command` 从 stdin 读取 JSON object，向 stdout 输出 Git output JSON。stderr/stdout 会保存到 `harness/git-runs/git-*/`。`timeout_seconds` 可放在 provider 顶层或 `provider_options` 中。

## Provider Input

```json
{
  "schema_version": 1,
  "action": "publish",
  "provider": "git",
  "provider_options": {
    "remote": "origin",
    "push": true,
    "stage": "all",
    "commit_message": "TASK-0001: Publish changes"
  },
  "root": "/absolute/project",
  "project": {"name": "example-project", "default_branch": "main"},
  "task_id": "TASK-0001",
  "task": {
    "id": "TASK-0001",
    "title": "Publish changes",
    "state": "accepted",
    "files": {"write": ["README.md"]}
  }
}
```

`action` 当前只能是 `publish`。有 `task_id` 时，内置 adapter 默认只 stage `task.files.write`；没有 `task_id` 时默认 `git add -A`。可用 `provider_options.stage_paths` 显式指定路径，或用 `stage: all` 强制 stage 整个工作区。

内置 adapter 默认拒绝在 `project.default_branch` 上直接 publish，除非设置 `allow_default_branch: true`。

## Provider Output

```json
{
  "schema_version": 1,
  "provider": "git",
  "status": "published",
  "summary": "git publish committed and pushed on codex/publish",
  "branch": "codex/publish",
  "remote": "origin",
  "commit_before": "abc123",
  "commit_after": "def456",
  "pushed": true,
  "changes": ["README.md"]
}
```

字段规则：

- `schema_version` 必须为 `1`。
- `contract_version` 可选；如果出现，必须为 `1`。
- `status` 只能是 `published`、`skipped`、`blocked`、`failed` 或 `unknown`。
- `summary` 必须非空。
- `pushed` 如果存在，必须是 boolean。
- `changes` 如果存在，必须是 list。
- `published` 或 `skipped` 表示 autopilot 可以继续 PR/CI gate。
- `blocked` 表示缺少远端、认证、分支策略或外部状态，autopilot 会停在 blocked。
- `failed` 表示本地 git 命令失败，autopilot 会触发 repair 或失败。

## Evidence

每次 `attestflow publish [--task TASK-*]` 会创建：

```text
harness/git-runs/git-<timestamp>/
  input.json
  stdout.log
  stderr.log
  output.json
```

如果配置了 `integrations.git_provider`，`autopilot --run` / `--resume` 会在 accepted task close 前、`pr ensure` 前执行 `publish`，把 `output.json` 的相对路径写入 `task.evidence.git`。Git evidence 证明提交/推送动作，不替代当前 task run 的本地 verification evidence。
