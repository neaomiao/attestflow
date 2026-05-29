# Task Schema 契约

日期：2026-05-29
状态：核心本地校验已实现

## 目标

`task schema` 是 LLM planner、Agent、CI 和 harness runtime 之间的稳定协议。任务内容默认由大模型生成，Attestflow 负责校验并落盘。任务没有满足 Definition of Ready 前不能执行；任务没有满足 Definition of Done 且没有 evidence 前不能完成。

字段名、状态名和命令名保留英文，原因是它们会被代码、CI 和脚本解析。解释文字使用中文。

任务 YAML 是 runtime 的持久格式，不是人工主编辑入口。主路径是 planner JSON 通过 `attestflow task import --from-json` 导入。

## 文件位置

任务文件放在配置的任务根目录：

```text
harness/tasks/<state>/<task-id>.yml
```

目录状态必须和文件内 `state` 一致。

必须支持的状态目录：

```text
harness/tasks/
  proposed/
  needs_clarification/
  ready/
  in_progress/
  blocked/
  review/
  verified/
  accepted/
  done/
  archived/
```

## 必填字段

```yaml
schema_version: 1
id: TASK-0001
title: Short imperative title
state: proposed
priority: 100
type: feature

purpose: ""
context: []
scope: []
out_of_scope: []

requirements:
  confirmed: []
  unresolved: []
  assumptions: []

bdd_scenarios: []
unit_tests: []
acceptance: []

dependencies: []
blocks: []
files:
  read: []
  write: []

agents:
  owner: orchestrator
  allowed_roles: []

external_inputs:
  credentials: []
  services: []
  user_decisions: []

evidence:
  run_id: null
  red: null
  green: null
  verify: null
  packet: null

links:
  issues: []
  prs: []
  docs: []

risks: []
notes: []
created_at: null
updated_at: null
```

## 字段规则

`id`：稳定任务 ID，创建后不应变更。

`state`：必须是以下之一：

```text
proposed
needs_clarification
ready
in_progress
blocked
review
verified
accepted
done
archived
```

`priority`：数字越小优先级越高。

`type`：默认值为 `feature`、`bug`、`refactor`、`docs`、`chore`、`spike`，项目可以扩展。

`purpose`：任务存在的原因。只有 `ready` 之前允许为空。

`scope`：本任务会改什么。从 `ready` 开始必须非空。

`out_of_scope`：本任务明确不做什么。从 `ready` 开始必须非空。

`requirements.unresolved`：从 `ready` 开始必须为空，除非任务类型是 `spike`。

`bdd_scenarios`：实现开始前必须非空。

`unit_tests`：实现开始前必须非空。文档型任务可由项目策略豁免。

`acceptance`：从 `ready` 开始必须非空。

`dependencies`：依赖任务必须是 `done` 或 `archived` 后，当前任务才能开始。

`files.write`：`start` 前必须非空，用于 Agent 冲突检测。

`external_inputs`：任何必需外部输入缺失时，任务必须进入 `blocked`。

`evidence`：进入 `done` 前必须引用真实 run packet。

## 分状态要求

### `proposed`

最小字段：

- `schema_version`
- `id`
- `title`
- `state`
- `priority`
- `type`

### `needs_clarification`

必须包含：

- 至少一个未解决需求或用户决策
- 为什么不能开始执行

### `ready`

必须包含：

- `purpose`
- `scope`
- `out_of_scope`
- 空的 `requirements.unresolved`
- `bdd_scenarios`
- `unit_tests`
- `acceptance`
- `dependencies`，即使为空
- `files.write`

### `in_progress`

必须包含：

- owner agent
- run id
- lock reference
- 如果启用 Git，则包含 branch 或 worktree reference

### `blocked`

必须包含：

- blocker reason
- unblock condition
- 下一步责任人

### `review`

必须包含：

- implementation summary
- changed file list
- test evidence references

### `verified`

必须包含：

- 通过的 project verification run
- command logs 或 CI references

### `accepted`

必须包含：

- acceptance criteria check results
- unresolved risk list，即使为空

### `done`

必须包含：

- complete evidence packet
- valid DoD result
- closed locks
- linked issue disposition

### `archived`

只有 `done` 任务能进入 `archived`。

## 状态流转

```text
proposed -> needs_clarification
proposed -> ready
needs_clarification -> ready
needs_clarification -> blocked
ready -> in_progress
in_progress -> blocked
in_progress -> review
review -> in_progress
review -> verified
verified -> accepted
accepted -> done
done -> archived
blocked -> needs_clarification
blocked -> ready
```

其他流转默认非法，除非未来 schema version 明确增加。

## 调度规则

`next` 只能选择满足以下条件的任务：

- `state` 是 `ready`
- 依赖已完成
- `files.write` 未被锁定
- 必需外部输入已存在
- `priority` 最小
- 优先级相同按 `id` 字典序排序

`start` 必须原子执行：

- 验证任务
- 创建 run id
- 创建 task lock
- 创建 file ownership locks
- 将状态改为 `in_progress`
- 追加第一条 run ledger

## 最小 Ready 示例

```yaml
schema_version: 1
id: TASK-0001
title: Add task validator
state: ready
priority: 10
type: feature
purpose: Enforce task schema before implementation begins.
context:
  - The harness must reject incomplete executable tasks.
scope:
  - Validate required fields.
  - Validate ready-state requirements.
out_of_scope:
  - Build CI integration.
requirements:
  confirmed:
    - Ready tasks need BDD and unit test targets.
  unresolved: []
  assumptions: []
bdd_scenarios:
  - Ready task without BDD is rejected.
unit_tests:
  - tests/unit/test_task_schema.py
acceptance:
  - Invalid ready task exits with non-zero status.
dependencies: []
blocks: []
files:
  read:
    - docs/contracts/task-schema.md
  write:
    - attestflow/tasks.py
agents:
  owner: orchestrator
  allowed_roles:
    - worker_agent
    - test_agent
external_inputs:
  credentials: []
  services: []
  user_decisions: []
evidence:
  run_id: null
  red: null
  green: null
  verify: null
  packet: null
links:
  issues: []
  prs: []
  docs:
    - docs/contracts/task-schema.md
risks: []
notes: []
created_at: 2026-05-29T00:00:00Z
updated_at: 2026-05-29T00:00:00Z
```
