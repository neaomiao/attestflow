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

Planner capability 只消费 approved spec 内容，或内部受控修复上下文。Raw goal、PRD 或 source evidence 不能直接调用 planner。

流程：

```text
approved spec -> planner capability input -> programming agent provider -> planner JSON -> task import -> runtime task JSON
```

Programming Agent Provider 要求：

- 从 stdin 读取 JSON object。
- 输入包含 `repository_context`。
- 向 stdout 输出 JSON object。
- 输出必须符合 `docs/contracts/planner-output-schema.md`。
- stderr 会保存到 capability run 证据中。
- 非零退出码会阻止任务导入。
- provider 超时会阻止任务导入，并保留 `input.json`、`stdout.log`、`stderr.log`。

配置：

```yaml
capabilities:
  planner:
    agent_provider: codex
    command: null
    provider_options:
      timeout_seconds: 300
```

当 `agent_provider` 是 `codex`、`claude-code` 或 `opencode` 且 `command` 为 `null` 时，Attestflow 会自动使用内置 capability adapter。Adapter 会把 capability input 转成编程 Agent prompt，调用对应 CLI，并从 stdout 中抽取符合 contract 的 JSON；它会处理流式 JSON 行、日志噪声、嵌套 JSON 和 JSON 字符串。显式 `--command` 或 `capabilities.<name>.command` 优先级更高。`timeout_seconds` 可放在 capability 顶层或 `provider_options` 中；超时会终止 provider process group，写入 stderr log，并让本次 capability 失败。

Provider contract suite：

```bash
python -m attestflow provider contract --provider codex
```

该命令用固定夹具验证 provider 能返回 `planner`、task/`implementer`、`reviewer`、`verifier` 和 release/`releaser` 五类合同 JSON。

用户入口：

```bash
python -m attestflow go "实现登录功能"
python -m attestflow go --from-spec harness/specs/SPEC-0001/spec.md --approve
```

## Task-scoped Capability 执行

除 `planner` 外，capability 可以绑定到一个 runtime task 执行：

```bash
python -m attestflow capability run reviewer TASK-0001
```

流程：

```text
task JSON -> capability input -> programming agent provider -> capability output JSON -> task evidence index
```

Programming Agent Provider 要求：

- 从 stdin 读取 JSON object。
- 输入包含 `capability`、`agent_provider`、`provider_options`、`task`、`task_path`、`project`、`commands`、`repository_context`、`incremental_context` 和 `instructions`。
- 向 stdout 输出 JSON object。
- stderr/stdout 会保存到 `harness/capability-runs/<capability>-<task>-*/`。
- 非零退出码会阻止任务 evidence 更新。
- provider 超时会阻止任务 evidence 更新，并保留 `input.json`、`stdout.log`、`stderr.log`。
- stdout 必须满足 capability output schema。

输出 schema：

```json
{
  "schema_version": 1,
  "status": "passed",
  "summary": "No blocking issues.",
  "findings": [],
  "evidence": ["review report"],
  "usage": {
    "provider": "codex",
    "model": "gpt-5",
    "input_tokens": 1200,
    "output_tokens": 300,
    "total_tokens": 1500
  }
}
```

字段规则：

- `schema_version` 必须为 `1`。
- `contract_version` 可选；如果出现，必须为 `1`。缺省值兼容现有 provider。
- `status` 必须是 `passed`、`failed` 或 `blocked`。
- `summary` 必须是非空字符串。
- `findings` 必须是数组。
- `evidence` 必须是数组。
- `usage` 可选；如果 provider 能拿到真实模型消耗，必须用非负整数填写 `input_tokens`、`output_tokens`、`total_tokens`、`cached_input_tokens` 或 `reasoning_tokens`，可用非负数字填写 `cost_usd`。Attestflow 会保留原始 `output.json`，并把 `usage` 单独写为 `usage.json`，方便后续成本审计。
- 当 `token_economy.provider_cache.enabled: true` 且 capability input 规范化后命中缓存，Attestflow 会复用成功 provider output，写出新的 `input.json`、`output.json`、`usage.json` 和 `cache.json`，不会再次调用外部模型 provider。
- 如果 provider 因缺少局部上下文返回 `artifacts.context_requests[]` 或顶层 `context_requests[]`，且 `token_economy.dynamic_context.auto_resolve: true`，Attestflow 会按 `context resolve` 同一协议本地解析请求，写出 `dynamic-context.json` 和 `output.context-request.json`，把 `resolved_dynamic_context` 注入下一次 provider input，并自动重试一次。重试后仍 `blocked` 时按普通 blocked capability 处理。

Task-scoped typed artifact 规则：

- `bdd.artifacts` 必须包含 `scenarios`、`updated_files`、`requirements_mapping` 和 `uncovered_behaviors`。
- `tdd.artifacts` 必须包含 `red_log`、`green_log`、`test_files`、`failing_tests` 和 `coverage`。
- `implementer.artifacts` 必须包含 `diff_summary`、`written_files`、`incomplete`、`risks` 和 `command_results`。
- `reviewer.findings[]` 必须是对象，至少包含 `severity`、`blocking` 和 `summary`；`severity` 只能是 `blocker`、`major`、`minor` 或 `info`。
- `verifier.artifacts` 必须包含 `commands`、`environment`、`duration_seconds`、`flake.detected` 和 `evidence`。

`updated_files`、`test_files` 和 `written_files` 必须落在 task 的 `files.write` 范围内。若 capability 在 git workspace 中产生了新的实际文件变更，Attestflow 会把 provider 前后的 `git status --porcelain --untracked-files=all` 做差，并拒绝任何越过 `files.write` 的写入。

当 task-scoped capability 返回 `blocked` 时，Attestflow 会先保存 `output.json` 并写回 `evidence.capabilities.<name>`，再把任务移入 `blocked`，追加 `type: capability`、`source: capability:<name>` 的 active blocker。Capability 不直接编辑 runtime task JSON。

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

## Release Capability 执行

`releaser` 是 top-level release gate capability，不绑定单个 task。配置 `capabilities.releaser.command` 后，autopilot 会在 release provider 前运行它：

```text
done task summaries -> releaser capability input -> release handoff JSON -> release provider input
```

输入包含 `done_tasks`、已完成任务摘要、交付 evidence、repository context 和 release instructions。输出沿用 capability output schema；`status: passed` 会写入 autopilot metadata 的 `releaser` / `releaser_tasks`，并作为 `release_handoff` 传给 release provider。若 release provider 后续返回 `failed`，release repair planner 的 goal 也会包含该 handoff 路径和 summary，避免修复任务只看到 provider 失败摘要。`blocked` 会让 top-level run blocked；`failed` 会让本轮 failed。

## Repository Context

Capability input 的 `repository_context` 由 Attestflow 确定性生成：

```json
{
  "enabled": true,
  "tree": ["README.md", "attestflow/capabilities.py"],
  "documents": [{"path": "README.md", "content": "...", "truncated": false}],
  "files": [{"path": "attestflow/capabilities.py", "content": "...", "truncated": true}],
  "dynamic_context": {"allowed_requests": ["file_slice", "symbol_lookup", "semantic_search"]},
  "limits": {"max_tree_entries": 200, "max_file_bytes": 4000}
}
```

规则：

- `tree` 是受限文件树。
- `documents` 来自 `context.documents`。
- `files` 来自 task `files.read` / `files.write` 和 `context.focus_files`。
- 二进制文件会被跳过。
- `.git`、`node_modules`、`__pycache__`、`harness/tasks`、`harness/runs`、`harness/capability-runs`、`harness/ci-runs`、`harness/git-runs`、`harness/pr-runs`、`harness/release-runs`、`harness/context-cache` 和 `harness/provider-cache` 默认排除。
- provider 不应自行递归扫描仓库；需要更多上下文时应通过 capability output 声明缺口。
- `python -m attestflow context resolve --from-json request.json --json` 可按动态上下文协议返回 `file_slice`、`symbol_lookup`、`dependency_neighbors`、`semantic_search`、`change_history` 或 `test_mapping` 片段，避免为了一个局部问题重发全仓上下文。
- 当估算输入超过 `token_economy.budgets.<capability>_input_tokens` 或 `default_input_tokens`，Attestflow 会把 `documents` / `files` 的全文替换为摘要、hash 和 `cache_key`，原摘要记录写入 `harness/context-cache/`，并在 input 的 `token_economy` 字段记录预算、估算 token 和节省量。
- `incremental_context` 只携带当前 task 的 focus files 和已有 capability output 摘要，避免 reviewer/verifier/releaser 反复读完整历史 evidence。

Task-scoped capability input 的 `root` 是执行 cwd。启用 `sessions.worktree.enabled` 时，`root` 指向任务 worktree，`control_root` 指向保存 `harness/` 状态和 evidence 的原项目目录，`workspace` 携带 worktree、branch、`commit_before` 和 `commit_after` 快照。Provider 只能把代码变更写到 `root`，不能直接修改 runtime task JSON；close 阶段由 Attestflow 把 worktree 变更提交并 ff-only merge 回 `control_root`。

## 非目标

- 不复制 Superpowers 或 gstack 的外部安装机制。
- 不把外部 skill 名称写成 Attestflow core 的运行前提。
- 不用不可审计 prompt 替代 capability contract。
