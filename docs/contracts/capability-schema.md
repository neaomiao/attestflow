# Capability Contract

日期：2026-05-30
状态：planner capability 和 task-scoped capability runner 已实现

## 目标

Capability 是 Attestflow 内部的专业能力合同。它借鉴 [Superpowers](https://github.com/obra/superpowers) 和 [gstack](https://github.com/garrytan/gstack) 的分工方式，但不是外部 skill 依赖。

核心边界：

- 生成性判断交给编程 Agent provider。
- Attestflow 定义输入、输出、门禁和证据。
- provider 可以是 Codex、Claude Code、OpenCode、本地 agent 命令、API wrapper 或外部 skill adapter。
- core 不依赖任何具体编程 Agent provider。

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
  "programming_agent_provider": "optional",
  "external_dependency": false
}
```

规则：

- `name` 是稳定 ID。
- `specialist` 是角色，不是工具名。
- `phase` 必须能映射到开发流程。
- `inputs` 和 `outputs` 是编程 Agent provider 合同。
- `gates` 是 Attestflow 可审计的完成条件。
- `evidence` 是必须留下的证据文件或记录。
- `programming_agent_provider` 表示该 capability 可由编程 Agent provider 执行，但不是 core 依赖。
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
goal -> planner capability input -> programming agent provider -> planner JSON -> task import -> runtime task JSON
```

Programming Agent Provider 要求：

- 从 stdin 读取 JSON object。
- 输入包含 `repository_context`。
- 向 stdout 输出 JSON object。
- 输出必须符合 `docs/contracts/planner-output-schema.md`。
- stderr 会保存到 capability run 证据中。
- 非零退出码会阻止任务导入。

配置：

```yaml
capabilities:
  planner:
    agent_provider: command
    command: null
```

命令行覆盖：

```bash
python -m attestflow plan "实现登录功能" --command "codex exec --json"
```

## Task-scoped Capability 执行

除 `planner` 外，capability 可以绑定到一个 runtime task 执行：

```bash
python -m attestflow capability run reviewer TASK-0001 --command "your-reviewer-cli"
```

流程：

```text
task JSON -> capability input -> programming agent provider -> capability output JSON -> task evidence index
```

Programming Agent Provider 要求：

- 从 stdin 读取 JSON object。
- 输入包含 `capability`、`task`、`task_path`、`project`、`commands`、`repository_context` 和 `instructions`。
- 向 stdout 输出 JSON object。
- stderr/stdout 会保存到 `harness/capability-runs/<capability>-<task>-*/`。
- 非零退出码会阻止任务 evidence 更新。
- stdout 必须满足 capability output schema。

输出 schema：

```json
{
  "schema_version": 1,
  "status": "passed",
  "summary": "No blocking issues.",
  "findings": [],
  "evidence": ["review report"]
}
```

字段规则：

- `schema_version` 必须为 `1`。
- `status` 必须是 `passed`、`failed` 或 `blocked`。
- `summary` 必须是非空字符串。
- `findings` 必须是数组。
- `evidence` 必须是数组。

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

## Repository Context

Capability input 的 `repository_context` 由 Attestflow 确定性生成：

```json
{
  "enabled": true,
  "tree": ["README.md", "attestflow/capabilities.py"],
  "documents": [{"path": "README.md", "content": "...", "truncated": false}],
  "files": [{"path": "attestflow/capabilities.py", "content": "...", "truncated": true}],
  "limits": {"max_tree_entries": 200, "max_file_bytes": 4000}
}
```

规则：

- `tree` 是受限文件树。
- `documents` 来自 `context.documents`。
- `files` 来自 task `files.read` / `files.write` 和 `context.focus_files`。
- 二进制文件会被跳过。
- `.git`、`node_modules`、`__pycache__`、`harness/runs`、`harness/capability-runs` 默认排除。
- provider 不应自行递归扫描仓库；需要更多上下文时应通过 capability output 声明缺口。

## 非目标

- 不复制 Superpowers 或 gstack 的外部安装机制。
- 不把外部 skill 名称写成 Attestflow core 的运行前提。
- 不用不可审计 prompt 替代 capability contract。
