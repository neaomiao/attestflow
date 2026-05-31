# Task Schema 契约

日期：2026-05-29
状态：核心本地校验已实现

## 目标

`task schema` 是编程 Agent planner、CI 和 harness runtime 之间的稳定协议。任务内容默认由编程 Agent 生成，Attestflow 负责校验并落盘。任务没有满足 Definition of Ready 前不能执行；任务没有满足 Definition of Done 且没有 evidence 前不能完成。

字段名、状态名和命令名保留英文，原因是它们会被代码、CI 和脚本解析。解释文字使用中文。

任务 JSON 是 runtime 的唯一任务文档格式，不是人工主编辑入口。主路径是 planner JSON 通过 `attestflow task import --from-json` 导入。

## 文件位置

任务文件放在配置的任务根目录：

```text
harness/tasks/<state>/<task-id>.json
```

目录状态必须是下面列出的合法状态之一，并且必须和文件内 `state` 一致。

文件名必须和任务 `id` 一致，例如 `TASK-0001.json` 内的 `id` 必须是 `TASK-0001`。同一个 `id` 不能同时出现在多个状态目录里；这是调度、锁和 evidence 归属的全局不变量。

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

```json
{
  "schema_version": 1,
  "id": "TASK-0001",
  "title": "Short imperative title",
  "state": "proposed",
  "priority": 100,
  "type": "feature",
  "purpose": "",
  "context": [],
  "scope": [],
  "out_of_scope": [],
  "requirements": {
    "confirmed": [],
    "unresolved": [],
    "assumptions": []
  },
  "bdd_scenarios": [],
  "unit_tests": [],
  "acceptance": [],
  "dependencies": [],
  "blocks": [],
  "blockers": [],
  "files": {
    "read": [],
    "write": []
  },
  "agents": {
    "owner": "orchestrator",
    "allowed_roles": []
  },
  "external_inputs": {
    "credentials": [],
    "services": [],
    "user_decisions": []
  },
  "evidence": {
    "session": null,
    "run_id": null,
    "red": null,
    "green": null,
    "verify": null,
    "packet": null
  },
  "links": {
    "issues": [],
    "prs": [],
    "docs": []
  },
  "risks": [],
  "notes": [],
  "created_at": null,
  "updated_at": null
}
```

## 字段规则

`schema_version`：必须是 `1`。

`id`：稳定任务 ID，必须匹配 `TASK-<number>`，创建后不应变更。

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

`priority`：必须是整数；数字越小优先级越高。

`type`：默认值为 `feature`、`bug`、`refactor`、`docs`、`chore`、`spike`，项目可以扩展。

`purpose`：任务存在的原因。只有 `ready` 之前允许为空。

`scope`：本任务会改什么。从 `ready` 开始必须非空。

`out_of_scope`：本任务明确不做什么。从 `ready` 开始必须非空。

`context`、`scope`、`out_of_scope`、`bdd_scenarios`、`unit_tests`、`acceptance`、`blocks`、`risks` 和 `notes`：如果存在，必须是字符串 list；从 `ready` 开始，`scope`、`out_of_scope`、`bdd_scenarios`、`unit_tests` 和 `acceptance` 还必须非空，且每个条目必须是非空字符串。

`requirements.confirmed`、`requirements.unresolved`、`requirements.assumptions`：如果存在，必须是字符串 list。`requirements.unresolved` 从 `ready` 开始必须为空，除非任务类型是 `spike`。

`bdd_scenarios`：实现开始前必须非空，且条目必须是非空字符串。

`unit_tests`：实现开始前必须非空，且条目必须是非空字符串。文档型任务可由项目策略豁免。

`acceptance`：从 `ready` 开始必须非空，且条目必须是非空字符串。

`dependencies`：必须是字符串 list，条目必须是非空字符串。依赖任务必须是有效的 `done` 或 `archived` 任务后，当前任务才能开始；“有效”包括任务文件目录和 `state` 一致、文件名和 `id` 一致、没有重复 `id`，并通过 task schema 校验。

`blocks`：必须是 list。它是反向依赖提示，不替代 `dependencies` 的执行顺序判定。

`files.read`：如果存在，必须是字符串 list。

`files.write`：`start` 前必须非空，用于 Agent 冲突检测；每个条目必须是非空字符串。调度和锁会把路径规范化后比较，因此 `src/a.py` 和 `./src/a.py` 视为同一个写范围。

`agents.owner`：如果存在，必须是非空字符串。`agents.allowed_roles` 如果存在，必须是字符串 list。

`external_inputs`：必须是 mapping，内部 `credentials` / `services` / `user_decisions` 等条目必须是字符串 list。任何必需外部输入缺失时，任务不能保持 `ready`，必须进入 `blocked` 并写入 active blocker。

`blockers`：结构化阻塞记录。`ready`、`in_progress`、`review`、`verified`、`accepted`、`done` 不能有 active blocker；`blocked` 必须至少有一个 active blocker。

`evidence`：`in_progress`、`review`、`verified` 和 `accepted` 必须包含 `evidence.run_id` 和 `evidence.session`，确保活跃任务能恢复到确定的 run 和 agent session；`review`、`verified`、`accepted`、`done` 和 `archived` 还必须引用真实 run packet。外部交付证据使用可选字段记录相对路径：`ci` 指向 CI provider output，`pr_request` 指向 `pr ensure` output，`pr` 指向 `pr status` output，`release` 不写入单任务 evidence，而写入顶层 autopilot metadata。

`links.issues`、`links.prs`、`links.docs`：如果存在，必须是字符串 list。

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
- `evidence.session`
- `evidence.run_id`
- lock reference
- 如果启用 Git，则包含 branch 或 worktree reference

### `blocked`

必须包含：

- 至少一个 `status: active` 的 `blockers[]` 条目
- blocker reason
- unblock condition
- 下一步责任人 `owner`

`blockers[]` 条目格式：

```json
{
  "id": "BLK-0001",
  "type": "credential",
  "reason": "missing API_TOKEN",
  "unblock_condition": "Set API_TOKEN in the target environment.",
  "owner": "user",
  "source": "session:launch",
  "status": "active",
  "created_at": "2026-05-30T00:00:00Z",
  "resolved_at": null
}
```

### `review`

必须包含：

- implementation summary
- changed file list
- test evidence references
- `evidence.run_id`
- `evidence.session`
- `evidence.packet`

### `verified`

必须包含：

- 通过的 project verification run
- command logs 或 CI references
- `evidence.run_id`
- `evidence.session`
- `evidence.packet`

`review -> verified` 和 `verified -> accepted` 不是纯状态改名。Attestflow 会读取当前 run metadata，要求 `verify --task` 已写入所有已配置本地验证命令的通过证据；缺失、失败或不新鲜的验证证据会拒绝 transition。

### `accepted`

必须包含：

- acceptance criteria check results
- unresolved risk list，即使为空
- `evidence.run_id`
- `evidence.session`
- `evidence.packet`

### `done`

必须包含：

- complete evidence packet
- `evidence.run_id`
- `evidence.packet`
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
accepted -> in_progress
accepted -> done
done -> archived
blocked -> needs_clarification
blocked -> ready
```

其他流转默认非法，除非未来 schema version 明确增加。

## 调度规则

`next` 只能选择满足以下条件的单个任务；`dispatch --limit N` 使用同一规则选择一批可并行任务：

- `state` 是 `ready`
- 依赖已完成，且完成任务本身通过 registry 不变量和 schema 校验
- `files.write` 未被锁定
- 没有 active blocker
- `external_inputs` 为空；否则任务必须先进入 `blocked`
- 同一批次内 `files.write` 不重叠
- `priority` 最小
- 优先级相同按 `id` 字典序排序

每个被 dispatch 的 task 必须原子执行：

- 验证任务
- 创建 run id
- 创建 task lock
- 创建 file ownership locks
- 将状态改为 `in_progress`
- 追加第一条 run ledger

## 最小 Ready 示例

```json
{
  "schema_version": 1,
  "id": "TASK-0001",
  "title": "Add task validator",
  "state": "ready",
  "priority": 10,
  "type": "feature",
  "purpose": "Enforce task schema before implementation begins.",
  "context": [
    "The harness must reject incomplete executable tasks."
  ],
  "scope": [
    "Validate required fields.",
    "Validate ready-state requirements."
  ],
  "out_of_scope": [
    "Build CI integration."
  ],
  "requirements": {
    "confirmed": [
      "Ready tasks need BDD and unit test targets."
    ],
    "unresolved": [],
    "assumptions": []
  },
  "bdd_scenarios": [
    "Ready task without BDD is rejected."
  ],
  "unit_tests": [
    "tests/unit/test_task_schema.py"
  ],
  "acceptance": [
    "Invalid ready task exits with non-zero status."
  ],
  "dependencies": [],
  "blocks": [],
  "blockers": [],
  "files": {
    "read": [
      "docs/contracts/task-schema.md"
    ],
    "write": [
      "attestflow/tasks.py"
    ]
  },
  "agents": {
    "owner": "orchestrator",
    "allowed_roles": [
      "worker_agent",
      "test_agent"
    ]
  },
  "external_inputs": {
    "credentials": [],
    "services": [],
    "user_decisions": []
  },
  "evidence": {
    "session": null,
    "run_id": null,
    "red": null,
    "green": null,
    "verify": null,
    "packet": null
  },
  "links": {
    "issues": [],
    "prs": [],
    "docs": [
      "docs/contracts/task-schema.md"
    ]
  },
  "risks": [],
  "notes": [],
  "created_at": "2026-05-29T00:00:00Z",
  "updated_at": "2026-05-29T00:00:00Z"
}
```
