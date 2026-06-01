# CI Provider Schema 契约

日期：2026-05-30
状态：`ci status`、`ci await`、`ci logs`、`ci artifacts`、`ci rerun`、`ci dispatch` 和 GitHub Actions adapter 已实现

## 目标

CI provider 是 Attestflow 和外部 CI 系统之间的边界。GitHub Actions、Buildkite、CircleCI 或自建 CI 只通过这个 contract 接入；Attestflow core 不依赖任何 CI SDK。

Attestflow 负责确定性工作：构造 provider input、执行命令、保存 `input.json`、`stdout.log`、`stderr.log`、`output.json`，并校验统一 CI JSON。CI provider 只负责读取或请求外部 CI 状态变化并返回机器可校验的 JSON。

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
  "action": "status",
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
  "action": "status",
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
- `action` 可选；内置 GitHub Actions preset 支持 `status`、`await`、`logs`、`artifacts`、`rerun` 和 `dispatch`。
- `status` 只能是 `passed`、`failed`、`running`、`queued`、`cancelled`、`skipped`、`blocked` 或 `unknown`。
- `summary` 必须非空。
- `checks` 如果存在，必须是 list。
- `jobs`、`annotations`、`artifacts` 如果存在，必须是 list。
- `logs` 和 `failure_summary` 是 provider-specific evidence；核心只校验 JSON shape，不解释 GitHub 语义。
- `running`、`queued` 和 `unknown` 表示外部 CI 尚未收敛；autopilot 会记录 evidence，暂停为 `status: paused` / `pause_reason: external_status_pending`，并允许 `--resume` 重新采集状态。
- `blocked` 表示 CI provider 无法读取状态，例如 CLI 缺失、未授权、网络不可达或外部服务不可用。

## GitHub Actions Preset

`provider: github-actions` 的 `status` action 使用内置 adapter 调用：

```bash
gh run list --limit 1 --json databaseId,status,conclusion,workflowName,displayTitle,headBranch,headSha,url,createdAt,updatedAt,event
```

默认不再只信任“最近一次 run”。可用 `provider_options` 精确筛选当前 PR/commit/workflow：

```yaml
integrations:
  ci_provider:
    provider: github-actions
    provider_options:
      command: /opt/bin/gh
      repository: owner/repo
      branch: feature/my-change
      head_sha: abc123
      workflow: ci.yml
      event: pull_request
      status_filter: completed
      status_args:
        - run
        - list
        - --limit
        - "1"
        - --json
        - databaseId,status,conclusion,workflowName,displayTitle,headBranch,headSha,url,createdAt,updatedAt
      timeout_seconds: 30
```

GitHub Actions action 映射：

- `status`：读取匹配 run；失败时 best-effort 采集 failed jobs、annotations 和 `gh run view --log-failed`。
- `await`：按 `max_wait_seconds` / `poll_interval_seconds` 轮询，直到 `passed`、`failed`、`cancelled`、`skipped` 或 `blocked`。
- `logs`：读取指定 `run_id` 或筛选到的 run，并返回 failed log evidence；配置 `repository` 时会 best-effort 通过 check-runs annotations API 补充 annotations。
- `artifacts`：通过 `gh api repos/{owner}/{repo}/actions/runs/{run_id}/artifacts` 列出 artifacts；配置 `download_dir` 或 `download_artifacts` 时再调用 `gh run download`。
- `rerun`：调用 `gh run rerun <run_id>`；`rerun_failed: true` 时只 rerun failed jobs。
- `dispatch`：调用 `gh workflow run <workflow> --ref <ref>`，`inputs` 会转为 `-f KEY=VALUE`。

对应 CLI：

```bash
python -m attestflow ci status --head-sha abc123 --branch feature/my-change --workflow ci.yml
python -m attestflow ci await --head-sha abc123 --max-wait-seconds 600 --poll-interval-seconds 10
python -m attestflow ci logs --run-id 123456789
python -m attestflow ci artifacts --run-id 123456789 --download-dir attestflow-artifacts
python -m attestflow ci rerun --run-id 123456789 --failed
python -m attestflow ci dispatch --workflow ci.yml --ref feature/my-change --input task=TASK-0001
```

## Evidence

每次 `attestflow ci <action>` 会创建：

```text
harness/ci-runs/ci-<timestamp>/
  input.json
  stdout.log
  stderr.log
  output.json
```

CI evidence 是外部状态快照，不替代本地 `verify --task` 的任务完成证据。后续 release gate 可以引用这些 CI runs，但 task close 仍以当前 run 的 DoD evidence 为准。手动接入流程需要把 CI evidence 绑定到任务时，使用 `attestflow ci status --task TASK-*`，它会把 `output.json` 的相对路径写入 `task.evidence.ci`。
