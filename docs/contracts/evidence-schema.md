# Evidence Schema 契约

日期：2026-05-29
状态：核心本地门禁已实现

## 目标

Evidence 是任务正确经过 BDD、unit test、implementation、verification、review 和 closure 的持久证明。任何完成声明都必须引用当前 run 的 evidence。

字段名、事件名和文件名保留英文，解释文字使用中文。

## Run 目录

每次任务执行写一个目录：

```text
harness/runs/<timestamp>-<task-id>/
  metadata.yml
  ledger.jsonl
  evidence.md
  commands/
    bdd.log
    unit.log
    lint.log
    typecheck.log
    secret_scan.log
    project_verify.log
```

如果某个 gate 按项目策略不适用，可以没有对应 log，但 evidence packet 必须说明不适用原因。

## `metadata.yml`

```yaml
schema_version: 1
run_id: 2026-05-29T00-00-00Z-TASK-0001
task_id: TASK-0001
started_at: 2026-05-29T00:00:00Z
ended_at: null
status: in_progress

actor:
  role: orchestrator
  id: local

workspace:
  root: /absolute/path/to/project
  branch: task/TASK-0001-example
  worktree: null
  commit_before: null
  commit_after: null

locks:
  task: harness/locks/tasks/TASK-0001.lock
  files:
    - harness/locks/files/attestflow.tasks.py.lock

commands:
  bdd: null
  unit: null
  lint: null
  typecheck: null
  secret_scan: null
  project_verify: null

result:
  dor_passed: false
  dod_passed: false
  conclusion: null
```

## `ledger.jsonl`

`ledger.jsonl` 是 append-only。每一行是一个 JSON object。

事件最小结构：

```json
{
  "timestamp": "2026-05-29T00:00:00Z",
  "event": "task_started",
  "task_id": "TASK-0001",
  "run_id": "2026-05-29T00-00-00Z-TASK-0001",
  "actor": {
    "role": "orchestrator",
    "id": "local"
  },
  "data": {}
}
```

标准事件名：

```text
task_started
state_changed
lock_acquired
lock_released
command_started
command_finished
gate_passed
gate_failed
evidence_written
blocked
resumed
closed
```

`resume` 从 `metadata.yml` 和 `ledger.jsonl` 重建状态，不能依赖聊天历史。

## `evidence.md`

人可读证据包结构：

```markdown
# Evidence Packet

## Task

- ID:
- Title:
- Run:
- Branch:
- Commit Before:
- Commit After:

## Requirement Boundary

- Purpose:
- Scope:
- Out of Scope:
- Unresolved Requirements:

## BDD

- Command:
- Result:
- Log:
- Scenarios Covered:

## Unit Tests

- Command:
- Result:
- Log:
- Tests Covered:

## Quality Gates

- Lint:
- Typecheck:
- Secret Scan:
- Project Verify:

## Changes

- Files Changed:
- Files Locked:
- Linked Issues:

## Acceptance

- Criteria:
- Result:

## Documentation

- Updated:
- Not Applicable Reason:

## Risks

- Remaining:
- Follow-ups:
```

## Gate Result Object

`metadata.yml` 中的命令结果使用下面结构：

```yaml
command: python -m unittest discover tests/unit
started_at: 2026-05-29T00:00:00Z
ended_at: 2026-05-29T00:00:10Z
exit_code: 0
log: commands/unit.log
fresh: true
ci_url: null
```

`fresh` 表示命令在当前任务进入 `in_progress` 后执行。

## 关闭规则

`close TASK` 只有在以下条件满足时才能把任务移到 `done`：

- task state 是 `accepted`
- evidence packet 存在
- run metadata 的 `task_id` 匹配当前 task
- `harness.yml` 中启用的 verification commands 都有当前 run 的记录
- 启用命令的 `exit_code` 都是 `0`
- 启用命令的 `command` 和当前配置一致
- 启用命令的 `fresh` 是 `true`
- 启用命令引用的 command log 存在
- close 成功后写入 `ended_at`、`status: closed` 和 final ledger event `closed`
- close 成功后释放 task/file locks 并把 task 移到 `done`

## 恢复规则

`resume` 应该：

1. 查找未完成 runs。
2. 如果存在多个 active task locks 且没有指定 task id，则拒绝含糊恢复。
3. 读取最新 ledger event。
4. 输出下一步动作。
5. 不自动重跑破坏性或会修改状态的命令。
6. 项目策略允许时，可以重跑 verification 命令。

恢复示例：

- 最后事件是 BDD 失败的 `command_finished`：下一步是修 BDD 场景或需求边界。
- 最后事件是 unit tests 的 `gate_passed`：下一步按 gate 顺序进入 implementation 或 project verify。
- 最后事件是 `blocked`：下一步是满足 unblock condition，再转回 `ready`。
- state 是 active 但 lock 丢失：下一步是修复状态或经确认后重新获取 lock。

## Evidence 校验

Validator 必须检查：

- run id 匹配 task evidence reference
- task id 匹配 metadata 和 packet
- metadata 引用的 command logs 存在
- passing gates 的 exit code 是 0
- failed gates 不能支撑 `done`
- run 内 timestamps 单调
- 最终状态流转符合 task schema
