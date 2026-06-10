# Planner Output Schema 契约

日期：2026-05-30
状态：已实现基础导入

## 目标

Planner output 是编程 Agent provider 和 Attestflow 之间的边界。编程 Agent 负责理解目标、拆解任务、给出 BDD、unit test、验收标准和文件范围；Attestflow 负责确定性校验、分配 task id、解析依赖并写入 runtime task JSON。

核心原则：AI 能完成的工作不进入人工主路径。人不手写任务文档；人只处理凭证、业务取舍和无法自动判断的外部决策。

## 输入格式

`attestflow go` 调用 planner 时，输入必须是 approved spec 的内容和上下文，不是 raw user text、raw PRD 或 source evidence。Planner provider 只负责把已经批准的 spec 拆成可导入的 task JSON；它不应从原始来源推断 approval，也不能把原始来源当成已澄清的执行边界。

Planner 必须输出 JSON object：

```json
{
  "schema_version": 1,
  "goal": "Improve the project onboarding flow.",
  "usage": {
    "provider": "codex",
    "model": "gpt-5",
    "input_tokens": 1200,
    "output_tokens": 300,
    "total_tokens": 1500
  },
  "tasks": [
    {
      "key": "planner_contract",
      "title": "Add planner output contract",
      "priority": 10,
      "type": "docs",
      "purpose": "Document the programming agent output shape.",
      "scope": ["planner JSON schema"],
      "out_of_scope": ["programming agent provider presets"],
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
- `contract_version`：可选；如果出现，必须为 `1`。缺省值兼容现有 provider。
- `goal`：原始目标摘要，用于审计，不写入 task 必填字段。
- `usage`：可选；记录 provider 报告的真实模型消耗。token 字段必须是非负整数，`cost_usd` 必须是非负数字。Attestflow 会把它另存为 capability run 的 `usage.json`。
- `tasks`：非空数组。
- `key`：planner 内部稳定引用。Attestflow 不信任编程 Agent 生成的 `TASK-*`，而是用 `key` 解析任务间依赖。
- `title`：任务标题。
- `priority`：数字越小越优先。
- `type`：默认 `feature`。
- `purpose`、`scope`、`out_of_scope`、`bdd_scenarios`、`unit_tests`、`acceptance`、`files.write`：ready 任务必填。
- `requirements.unresolved`：ready 非 spike 任务必须为空。
- `dependencies`：可以引用同一 planner output 里的 `key`，导入后会转换为真实 `TASK-*`。
- `external_inputs`：如果需要凭证、服务或业务决策，planner 必须显式写出；这类任务不能作为 `ready` 导入，除非外部输入已经由项目配置证明存在。
- `blockers`：当 planner 判断任务应进入 `blocked` 时，必须提供 active blocker；Attestflow 会保留该结构并执行 task schema 校验。

## 导入规则

`attestflow task import --from-json PLAN` 和 planner capability 输出必须最终走同一套导入规则。二者只能消费 approved spec 派生出的上下文，或已经由外部流程批准边界的 planner JSON；raw 文本、raw PRD 或未批准 source evidence 必须先走 `attestflow go <requirement source>` 生成并批准 spec。

- 分配递增的 `TASK-*` ID
- 忽略或覆盖编程 Agent 提供的 task id
- 把 planner `key` 依赖解析为真实 task id
- 补齐 `agents`、`external_inputs`、`blockers`、`evidence`、`links`、`risks`、`notes`、时间戳等默认字段
- 对每个任务执行 task schema 校验
- 任一任务不合法时拒绝整个导入
- 全部任务通过校验后才写入任务文件

Planner capability 额外负责：

- 构造 planner capability input
- 调用 `capabilities.planner.command` 或 `--command`
- 保存 `harness/capability-runs/planner-*/input.json`
- 保存 programming agent provider stdout/stderr
- 将 programming agent provider stdout 解析为 planner JSON
- 保存 `output.json`
- 复用 `task import` 的确定性校验和落盘

## 非目标

- 不在基础 runtime 中绑定具体编程 Agent provider。
- 不让编程 Agent 直接写 `harness/tasks/**/*.json`。
- 不把交互式人工填表作为任务创建主路径。
