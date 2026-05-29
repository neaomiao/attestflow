# Planner Output Schema 契约

日期：2026-05-30
状态：已实现基础导入

## 目标

Planner output 是大模型和 Attestflow 之间的边界。大模型负责理解目标、拆解任务、给出 BDD、unit test、验收标准和文件范围；Attestflow 负责确定性校验、分配 task id、解析依赖并写入 task YAML。

核心原则：AI 能完成的工作不进入人工主路径。人不手写任务 YAML；人只处理凭证、业务取舍和无法自动判断的外部决策。

## 输入格式

Planner 必须输出 JSON object：

```json
{
  "schema_version": 1,
  "goal": "Improve the project onboarding flow.",
  "tasks": [
    {
      "key": "planner_contract",
      "title": "Add planner output contract",
      "priority": 10,
      "type": "docs",
      "purpose": "Document the LLM output shape.",
      "scope": ["planner JSON schema"],
      "out_of_scope": ["model provider integration"],
      "requirements": {
        "confirmed": ["AI creates task drafts"],
        "unresolved": [],
        "assumptions": []
      },
      "bdd_scenarios": ["Planner output can be imported."],
      "unit_tests": ["tests/unit/test_planner_import.py"],
      "acceptance": ["planner contract is documented"],
      "dependencies": [],
      "files": {
        "read": ["README.md"],
        "write": ["docs/contracts/planner-output-schema.md"]
      }
    }
  ]
}
```

## 字段规则

- `schema_version`：当前为 `1`。
- `goal`：原始目标摘要，用于审计，不写入 task 必填字段。
- `tasks`：非空数组。
- `key`：planner 内部稳定引用。Attestflow 不信任模型生成的 `TASK-*`，而是用 `key` 解析任务间依赖。
- `title`：任务标题。
- `priority`：数字越小越优先。
- `type`：默认 `feature`。
- `purpose`、`scope`、`out_of_scope`、`bdd_scenarios`、`unit_tests`、`acceptance`、`files.write`：ready 任务必填。
- `requirements.unresolved`：ready 非 spike 任务必须为空。
- `dependencies`：可以引用同一 planner output 里的 `key`，导入后会转换为真实 `TASK-*`。

## 导入规则

`attestflow task import --from-json PLAN` 必须：

- 分配递增的 `TASK-*` ID
- 忽略或覆盖大模型提供的 task id
- 把 planner `key` 依赖解析为真实 task id
- 补齐 `agents`、`external_inputs`、`evidence`、`links`、`risks`、`notes`、时间戳等默认字段
- 对每个任务执行 task schema 校验
- 任一任务不合法时拒绝整个导入
- 全部任务通过校验后才写入任务文件

## 非目标

- 不在基础 runtime 中绑定具体模型 provider。
- 不让模型直接写 `harness/tasks/**/*.yml`。
- 不把交互式人工填表作为任务创建主路径。
