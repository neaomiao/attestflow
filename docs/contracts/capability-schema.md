# Capability Contract

日期：2026-05-30
状态：planner capability 已实现

## 目标

Capability 是 Attestflow 内部的专业能力合同。它借鉴 [Superpowers](https://github.com/obra/superpowers) 和 [gstack](https://github.com/garrytan/gstack) 的分工方式，但不是外部 skill 依赖。

核心边界：

- 生成性判断交给大模型或 agent provider。
- Attestflow 定义输入、输出、门禁和证据。
- provider 可以是本地命令、模型 CLI、API wrapper 或外部 skill adapter。
- core 不依赖任何具体 provider。

## 字段

每个内置 capability 必须有：

```json
{
  "name": "planner",
  "specialist": "spec planner",
  "phase": "plan",
  "description": "Turn an approved goal into planner JSON that Attestflow can validate and import.",
  "inputs": ["user goal", "harness config", "existing task index", "planner output contract"],
  "outputs": ["planner JSON"],
  "gates": ["planner JSON parses", "runtime tasks satisfy Definition of Ready"],
  "evidence": ["input.json", "output.json", "stderr.log"],
  "external_dependency": false
}
```

规则：

- `name` 是稳定 ID。
- `specialist` 是角色，不是工具名。
- `phase` 必须能映射到开发流程。
- `inputs` 和 `outputs` 是 provider 合同。
- `gates` 是 Attestflow 可审计的完成条件。
- `evidence` 是必须留下的证据文件或记录。
- `external_dependency` 对内置能力必须是 `false`。

## 内置能力

```text
intake       think   requirements partner
planner      plan    spec planner
bdd          plan    behavior spec author
tdd          build   test engineer
implementer  build   implementation worker
reviewer     review  staff engineer reviewer
verifier     test    verification lead
releaser     ship    release engineer
```

## Planner 执行

`attestflow plan "目标"` 是第一个可执行 capability。

流程：

```text
goal -> planner capability input -> command provider -> planner JSON -> task import -> runtime task JSON
```

Provider 要求：

- 从 stdin 读取 JSON object。
- 向 stdout 输出 JSON object。
- 输出必须符合 `docs/contracts/planner-output-schema.md`。
- stderr 会保存到 capability run 证据中。
- 非零退出码会阻止任务导入。

配置：

```yaml
capabilities:
  planner:
    provider: command
    command: null
```

命令行覆盖：

```bash
python -m attestflow plan "实现登录功能" --command "your-model-cli"
```

## Task-scoped Capability 执行

除 `planner` 外，capability 可以绑定到一个 runtime task 执行：

```bash
python -m attestflow capability run reviewer TASK-0001 --command "your-reviewer-cli"
```

流程：

```text
task JSON -> capability input -> command provider -> capability output JSON -> task evidence index
```

Provider 要求：

- 从 stdin 读取 JSON object。
- 输入包含 `capability`、`task`、`task_path`、`project`、`commands` 和 `instructions`。
- 向 stdout 输出 JSON object。
- stderr/stdout 会保存到 `harness/capability-runs/<capability>-<task>-*/`。
- 非零退出码会阻止任务 evidence 更新。

输出建议：

```json
{
  "schema_version": 1,
  "status": "passed",
  "summary": "No blocking issues.",
  "findings": [],
  "evidence": ["review report"]
}
```

Attestflow 会把 `output.json` 的相对路径写回：

```json
{
  "evidence": {
    "capabilities": {
      "reviewer": "harness/capability-runs/reviewer-TASK-0001-.../output.json"
    }
  }
}
```

## 非目标

- 不复制 Superpowers 或 gstack 的外部安装机制。
- 不把外部 skill 名称写成 Attestflow core 的运行前提。
- 不用不可审计 prompt 替代 capability contract。
