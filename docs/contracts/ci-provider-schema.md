# CI Provider Schema 契约

日期：2026-05-30
状态：`ci status` 和 GitHub Actions adapter 已实现

## 目标

CI provider 是 Attestflow 和外部 CI 系统之间的边界。GitHub Actions、Buildkite、CircleCI 或自建 CI 只通过这个 contract 接入；Attestflow core 不依赖任何 CI SDK。

Attestflow 负责确定性工作：构造 provider input、执行命令、保存 `input.json`、`stdout.log`、`stderr.log`、`output.json`，并校验统一 CI JSON。CI provider 只负责读取外部 CI 状态并返回机器可校验的 JSON。

## 配置

```yaml
paths:
  ci_runs: harness/ci-runs

integrations:
  ci_provider:
    provider: github-actions
    provider_options:
      repository: owner/repo
```

通用 command provider：

```yaml
integrations:
  ci_provider:
    provider: command
    command: your-ci-status-command
```

`command` 从 stdin 读取 JSON object，向 stdout 输出 CI output JSON。stderr/stdout 会保存到 `harness/ci-runs/ci-*/`。`timeout_seconds` 可放在 provider 顶层或 `provider_options` 中；超时会终止 provider process group，写入 stderr log，并让本次 status 失败。

## Provider Input

```json
{
  "schema_version": 1,
  "provider": "github-actions",
  "provider_options": {"repository": "owner/repo"},
  "root": "/absolute/project",
  "project": {"name": "example-project"}
}
```

## Provider Output

```json
{
  "schema_version": 1,
  "provider": "github-actions",
  "status": "passed",
  "summary": "GitHub Actions CI: passed",
  "external_id": "123456789",
  "url": "https://github.com/owner/repo/actions/runs/123456789",
  "workflow": "CI",
  "title": "main build",
  "branch": "main",
  "commit": "abc123",
  "started_at": "2026-05-30T00:00:00Z",
  "ended_at": "2026-05-30T00:01:00Z",
  "checks": [
    {
      "name": "CI",
      "status": "passed",
      "external_id": "123456789",
      "url": "https://github.com/owner/repo/actions/runs/123456789"
    }
  ]
}
```

字段规则：

- `schema_version` 必须为 `1`。
- `contract_version` 可选；如果出现，必须为 `1`。缺省值兼容现有 provider。
- `status` 只能是 `passed`、`failed`、`running`、`queued`、`cancelled`、`skipped`、`blocked` 或 `unknown`。
- `summary` 必须非空。
- `checks` 如果存在，必须是 list。
- `running`、`queued` 和 `unknown` 表示外部 CI 尚未收敛；autopilot 会记录 evidence，暂停为 `status: paused` / `pause_reason: external_status_pending`，并允许 `--resume` 重新采集状态。
- `blocked` 表示 CI provider 无法读取状态，例如 CLI 缺失、未授权、网络不可达或外部服务不可用。

## GitHub Actions Preset

`provider: github-actions` 使用内置 adapter 调用：

```bash
gh run list --limit 1 --json databaseId,status,conclusion,workflowName,displayTitle,headBranch,headSha,url,createdAt,updatedAt
```

可用 `provider_options` 覆盖：

```yaml
integrations:
  ci_provider:
    provider: github-actions
    provider_options:
      command: /opt/bin/gh
      repository: owner/repo
      status_args:
        - run
        - list
        - --limit
        - "1"
        - --json
        - databaseId,status,conclusion,workflowName,displayTitle,headBranch,headSha,url,createdAt,updatedAt
      timeout_seconds: 30
```

## Evidence

每次 `attestflow ci status` 会创建：

```text
harness/ci-runs/ci-<timestamp>/
  input.json
  stdout.log
  stderr.log
  output.json
```

CI evidence 是外部状态快照，不替代本地 `verify --task` 的任务完成证据。后续 release gate 可以引用这些 CI runs，但 task close 仍以当前 run 的 DoD evidence 为准。手动接入流程需要把 CI evidence 绑定到任务时，使用 `attestflow ci status --task TASK-*`，它会把 `output.json` 的相对路径写入 `task.evidence.ci`。
