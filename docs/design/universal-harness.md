# 通用开发 Harness 设计

日期：2026-05-29
状态：核心本地闭环已实现，AI-first 任务导入和 planner capability 已纳入主路径
来源会话：019e7244-0bad-7970-81c4-af4c4323486c

## 目标

本项目要沉淀一个可复用的开发 harness，让其他项目可以直接采用同一套开发控制流程，而不是复制某个业务项目的临时脚手架。

Harness 不是测试框架。它是开发流程控制系统，把下面这条链路变成明确、可重复、可恢复、可审计的工作流：

```text
intent -> AI planning -> task import -> requirement boundary -> BDD scenario -> unit test -> implementation
-> verification -> evidence -> task state transition -> next executable task
```

来源项目的 harness 验证了正确方向：任务状态机、Definition of Ready、Definition of Done、BDD/TDD 顺序、验证证据包和 Agent 文件所有权。但它的问题是把具体语言栈、项目专用文档、私有发布流程、基础设施和业务红线混进了核心。

本设计的核心原则是：AI 能完成的工作不进入人工主路径，协议内核和项目适配分离。编程 Agent 负责目标拆解、任务草案、BDD 和验收标准；Attestflow 负责确定性校验、ID 分配、状态、锁、验证和证据。

## 非目标

- 不在本仓库实现任何业务产品。
- 不把 Python、Node、Go、Rust 等语言栈写死进核心协议。
- 不强制依赖任何外部 Agent 工具链、私有工作流、GitHub、Docker 或某个 CI 平台。
- 不允许 Agent 编排绕过任务状态、文件所有权或验证证据。
- 不依赖对话记忆作为任务状态或断点恢复的事实来源。
- 不使用手写任务文件作为主路径；任务 runtime 文件统一为 JSON，由 Attestflow 写入。
- 不把某个 AI 产品写死进核心；真实会话启动/恢复通过 `sessions.launch_command` / `sessions.resume_command` 适配。

## 设计原则

1. 协议优先：`task schema`、状态流转、门禁、证据、锁和 run ledger 是稳定接口。
2. AI 优先：目标拆解、任务草案、BDD、验收标准和文件范围默认由编程 Agent 生成。
3. 确定性落盘：编程 Agent 输出 planner JSON，Attestflow 分配 ID、补默认值、校验 schema，再写 runtime task JSON。
4. 适配其次：语言栈、测试命令、CI 平台、Issue 系统、Docker 和工具路由都由项目配置。
5. 没有可执行证明就不实现：新功能必须先有 BDD，再有 unit test，再写 implementation。
6. 没有新鲜证据就不完成：`done` 必须引用当前 run 的命令、时间戳和结果。
7. 从文件恢复，不从记忆恢复：`resume` 读取 task state、lock file 和 append-only ledger。
8. 每任务独立会话：任务进入执行时必须创建独立 `agent_session`、`prompt.md` 和 `session.yml`。
9. 并行必须有所有权：多个 Agent 只能在写入范围不重叠时并行。
10. 默认保守：需求不清进入 `needs_clarification`，外部输入缺失进入 `blocked`。

## 推荐仓库结构

```text
harness/
  pyproject.toml
  README.md
  docs/
    design/
      universal-harness.md
    contracts/
      task-schema.md
      evidence-schema.md
  attestflow/
    __init__.py
    __main__.py
    cli.py
    config.py
    io.py
    tasks.py
    planner.py
    gates.py
    evidence.py
    sessions.py
    runner.py
    locks.py
    resume.py
    secrets.py
  templates/
    base/
      harness.yml
      tasks/
      gates/
      agents/
      .github/workflows/ci.yml
    adapters/
      generic/
      python/
      node/
  tests/
    unit/
    bdd/
```

本轮实现先做一个可运行的标准库 MVP：配置读取、AI planner JSON 导入、任务校验、任务选择、任务启动、证据目录、断点恢复、secret scan、基础模板和测试。

## `harness.yml`

每个接入项目有一个 `harness.yml`：

```yaml
schema_version: 1
project:
  name: example-project
  default_branch: main

paths:
  tasks: harness/tasks
  runs: harness/runs
  gates: harness/gates
  locks: harness/locks
  capability_runs: harness/capability-runs
  docs: docs

commands:
  bdd: python -m unittest discover tests/bdd
  unit: python -m unittest discover tests/unit
  lint: null
  typecheck: null
  secret_scan: python -m attestflow secret-scan
  project_verify: null

policies:
  require_bdd_before_unit: true
  require_unit_before_implementation: true
  require_fresh_verify_for_done: true
  require_agent_session_for_task: true
  require_disjoint_agent_write_scopes: true
  require_issue_triage_for_linked_issues: true
  docker_required: false

sessions:
  agent_provider: command
  role: worker_agent
  launch_command: null
  resume_command: null
  provider_options: {}
  worktree:
    enabled: false
    path_template: null

capabilities:
  planner:
    agent_provider: command
    command: null
  bdd:
    agent_provider: command
    command: null
  tdd:
    agent_provider: command
    command: null
  implementer:
    agent_provider: command
    command: null
  reviewer:
    agent_provider: command
    command: null
  verifier:
    agent_provider: command
    command: null
  releaser:
    agent_provider: command
    command: null

context:
  enabled: true
  max_tree_entries: 200
  max_file_bytes: 4000
  documents:
    - README.md
    - AGENTS.md
    - harness.yml
    - pyproject.toml
    - package.json
    - docs/contracts/capability-schema.md
    - docs/contracts/planner-output-schema.md
    - docs/contracts/session-adapter-schema.md
    - docs/contracts/task-schema.md
    - docs/design/universal-harness.md

execution:
  docker:
    enabled: false
    compose_service: app

integrations:
  git_provider: optional
  ci_provider: optional
```

核心代码只读取配置，不从历史项目、私有工具或测试框架名称推断行为。

## 内置 Capabilities

Attestflow 不依赖外部 skills，但会吸收成熟 skill 系统的结构：

- 借鉴 [Superpowers](https://github.com/obra/superpowers)：技能按触发条件和流程门禁组织，强调设计先行、TDD、审查和验证证据。
- 借鉴 [gstack](https://github.com/garrytan/gstack)：能力按专业角色组织，串成 `Think -> Plan -> Build -> Review -> Test -> Ship -> Reflect`。

这两者在 Attestflow 中落为内置 capability contract，而不是运行时依赖。每个 capability 都必须声明：

- `name`
- `specialist`
- `phase`
- `inputs`
- `outputs`
- `gates`
- `evidence`
- `programming_agent_provider`
- `external_dependency: false`

第一批内置能力：

```text
intake       requirements partner
planner      spec planner
bdd          behavior spec author
tdd          test engineer
implementer  implementation worker
reviewer     staff engineer reviewer
verifier     verification lead
releaser     release engineer
```

`capability list/show` 展示合同；`plan` 执行目标级 planner capability；`capability run <name> <task>` 执行任务级 capability。Codex、Claude Code、OpenCode、外部 skill 或其他编程 Agent CLI 只通过 agent provider adapter 接入，不能成为 Attestflow core 的前置条件。

Provider input 包含 `repository_context`，由 Attestflow 确定性生成：

- `tree`：受限文件树
- `documents`：`README.md`、`harness.yml`、核心 contract/design 文档等
- `files`：任务 `files.read` / `files.write` 指向的文本片段
- `limits`：实际使用的上下文限制

默认排除 `.git`、`node_modules`、`__pycache__`、`harness/runs` 和 `harness/capability-runs`，避免把运行证据、缓存和依赖目录传给编程 Agent。

## 每任务独立会话

`dispatch TASK` 是 AI-first 执行入口：

```text
ready task -> dispatch -> run -> agent_session -> prompt packet -> external AI session
```

Dispatch 必须原子完成：

- 校验 task 处于 `ready`
- 创建 task/file locks
- 创建 `harness/runs/<run_id>/`
- 写入 `metadata.yml`、`ledger.jsonl`、`evidence.md`
- 写入 `prompt.md`，包含任务边界、写文件范围、BDD、unit test、验收标准和验证命令
- 写入 `session.yml`，包含 `session_id`、`agent_provider`、role、状态、prompt packet、启动命令和恢复命令
- 将 `agent_session` 写回 run metadata
- 将 `evidence.session` 写回 task
- 如果配置了 `sessions.launch_command`，按 `docs/contracts/session-adapter-schema.md` 执行 command adapter，写入 `session-adapter-input.json`、`session-adapter-output.json` 和 stdout/stderr logs

核心不绑定 Codex、Claude Code、OpenCode 或其他平台。项目可以用 `sessions.launch_command` / `sessions.resume_command` 适配任意编程 Agent CLI。没有配置启动命令时，dispatch 至少生成独立 session packet；接入层可以读取 packet 后启动会话。

当 `sessions.agent_provider` 是 `codex`、`claude-code` 或 `opencode` 时，Attestflow 会使用内置 provider preset 生成 adapter command。项目可以通过 `sessions.provider_options.command`、`launch_args`、`resume_args` 覆盖底层 CLI。

## AI Planning 和任务落盘

任务产生分两层：

```text
programming agent provider -> planner JSON -> attestflow task import -> task JSON
```

编程 Agent 负责判断和拆解，不直接写 `harness/tasks/**/*.json`。现在有两条等价入口：

```bash
python -m attestflow plan "目标描述"
python -m attestflow task import --from-json PLAN.json
```

`plan` 会构造标准 capability input，调用 `capabilities.planner.command` 或 `--command` 指定的编程 Agent provider，将 stdout 作为 planner JSON，再复用 `task import`。Attestflow 接收 planner JSON 后执行确定性处理：

- 分配递增的 `TASK-*` ID
- 解析 planner 内部 `key` 依赖
- 补齐默认字段
- 校验 task schema 和 ready 门禁
- 拒绝缺少 BDD、unit tests、acceptance 或写文件范围的任务
- 只在全部任务可通过校验后写入 runtime task JSON
- 保存 capability evidence：`input.json`、`stdout.log`、`stderr.log`、`output.json`

任务 JSON 是 runtime 的事实来源，不是人工主编辑界面。人工只负责不可自动判断的目标取舍、凭证授权和外部业务决策。

## Task-scoped Capability 执行

`bdd`、`tdd`、`implementer`、`reviewer`、`verifier` 和 `releaser` 共享一个任务级执行入口：

```bash
python -m attestflow capability run reviewer TASK-0001 --command "your-reviewer-cli"
```

执行规则：

- 加载 runtime task JSON
- 构造 capability input，包含 task、project、commands、repository_context、capability contract 和固定 instructions
- 调用 `--command` 或 `capabilities.<name>.command`
- 保存 `input.json`、`stdout.log`、`stderr.log` 和 `output.json`
- provider 非零退出、stdout 不是 JSON object 或 capability output schema 不合法时失败
- 成功后把 `output.json` 的相对路径写入 `task.evidence.capabilities.<name>`

这一步让内部 skills 不再只是文档合同，而是有统一执行、证据和任务回写机制。

## 任务存储

任务是 JSON 文件，放在配置指定的任务目录下：

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

目录状态和文件内 `state` 必须一致。不一致时 `validate-task` 失败。

## 状态机

合法状态：

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

合法流转：

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

禁止流转：

- `proposed -> in_progress`：绕过 DoR。
- `ready -> done`：绕过实现和证据。
- `in_progress -> done`：绕过 review、verification 和 acceptance。
- 非 `done` 状态进入 `archived`：隐藏未完成工作。

## 门禁

Definition of Ready 判断任务能否开始：

- 有明确 `purpose`
- 有 `scope`
- 有 `out_of_scope`
- 可执行范围内没有未解决占位
- 已声明 `bdd_scenarios`
- 已声明 `unit_tests`
- 已声明 `acceptance`
- `dependencies` 已满足
- 已声明 `files.write`
- 所需凭证和外部输入存在，否则任务必须是 `blocked`

Definition of Done 判断任务能否关闭：

- BDD 命令通过
- Unit 命令通过
- Project verify 命令通过或按策略不适用
- lint/typecheck 通过或按策略不适用
- docs/changelog 已更新或说明不适用
- linked issues 已处理
- secret scan 通过
- evidence packet 存在且引用当前 task/run
- 最终状态流转合法

## CLI

稳定 CLI 表面：

```bash
python -m attestflow init
python -m attestflow doctor
python -m attestflow validate-config
python -m attestflow validate-task TASK
python -m attestflow task import --from-json PLAN.json
python -m attestflow tasks
python -m attestflow next
python -m attestflow dispatch TASK
python -m attestflow start TASK
python -m attestflow block TASK --reason REASON
python -m attestflow evidence TASK
python -m attestflow verify
python -m attestflow verify --task TASK
python -m attestflow close TASK
python -m attestflow resume
python -m attestflow secret-scan
```

命令职责：

- `init`：在目标项目生成模板文件。
- `doctor`：检查工具、配置和目录一致性。
- `validate-config`：验证 `harness.yml`。
- `validate-task`：验证 schema、状态、目录、依赖和门禁。
- `task import --from-json`：导入编程 Agent 输出的 planner JSON，校验后写入 runtime task JSON。
- `tasks`：按状态和优先级列出任务。
- `next`：返回最高优先级、依赖已完成、文件未锁定的 `ready` 任务。
- `dispatch`：AI-first 执行入口，创建 run、locks、独立 agent session、prompt packet，并按配置启动外部 AI 会话。
- `start`：低层生命周期入口，仍会创建 session packet，保留给脚本和兼容场景。
- `block`：记录阻塞原因并移动到 `blocked`。
- `evidence`：写入或验证 evidence packet。
- `verify`：执行配置的质量门禁，用于临时或 CI 验证。
- `verify --task`：执行配置的质量门禁，并把命令结果写入当前 task run。
- `close`：校验当前 run 的 DoD evidence，释放锁，写最终证据并移动到 `done`。
- `resume`：读取未完成 run，输出下一步动作。
- `secret-scan`：扫描已跟踪或项目文件中的明显密钥。

## Run Ledger

每次任务执行写入一个 run 目录：

```text
harness/runs/
  2026-05-29T20-00-00Z-TASK-0001/
    metadata.yml
    ledger.jsonl
    evidence.md
    session.yml
    prompt.md
    session-adapter-input.json
    session-adapter-output.json
    session-launch.stdout.log
    session-launch.stderr.log
    commands/
      bdd.log
      unit.log
      lint.log
      typecheck.log
      secret_scan.log
      project_verify.log
```

`ledger.jsonl` 只追加，不重写。`resume` 必须能回答：

- 当前 task
- 当前 state
- owner agent
- agent session id
- prompt packet
- branch/worktree
- locked files
- 最近通过的 gate
- 最近失败的 gate
- 下一步动作
- 是否可以继续

## 多 Agent 编排

Agent 角色是协议角色，不是业务身份：

```text
orchestrator        owns task state, locks, final integration
requirements_agent  owns requirement intake and BDD scenario drafts
test_agent          owns unit/regression tests
worker_agent        owns assigned implementation files only
review_agent        owns spec and quality review
ci_agent            owns CI logs and failing check diagnosis
research_agent      owns external research notes, not production code
```

并行执行条件：

- 每个 Agent 有明确 `task_id`
- 每个 Agent 有明确 `files.write`
- 写入范围不重叠
- 共享文件由 orchestrator 锁定
- 每个 Agent 写自己的 evidence
- orchestrator 做最终集成和验证

不满足这些条件时，任务必须串行。

## 项目适配器

Adapter 提供默认文件，不改变核心协议：

- `generic`：只提供 shell 命令和标准目录。
- `python`：提供 `unittest`/可选 pytest、lint/typecheck 默认项。
- `node`：提供 package manager 检测和 test/lint/typecheck 默认项。

Adapter 生成的文件可以被项目修改；最终事实来源仍然是 `harness.yml`。

## CI

CI 应调用和本地一致的入口：

```bash
python -m attestflow verify
```

如果项目策略要求 Docker，则 CI 在 Docker 中运行；否则直接运行配置命令。GitHub Actions 只是模板，不是核心依赖。

## Secrets

内置 secret scan 是最低防线，不替代专用扫描器。它应该：

- 默认扫描项目文件
- 忽略 `.env.example` 等模板路径
- 拒绝高置信度的 key、token、password、private key
- 不打印密钥值
- 支持项目级 allow/deny 规则

## 新项目接入流程

1. 运行 `python -m attestflow init --adapter generic`。
2. 让 Agent 审核生成的 `harness.yml` 和项目命令，只有凭证或业务取舍需要人工确认。
3. 让编程 Agent 根据目标和仓库上下文输出 planner JSON。
4. 运行 `python -m attestflow task import --from-json plan.json`。
5. 用 `python -m attestflow next` 选择下一个 ready 任务。
6. 运行 `python -m attestflow dispatch TASK-*`，自动创建独立 agent session。
7. Agent 按 BDD -> unit -> implementation 执行。
8. 运行 `python -m attestflow transition TASK-* review`。
9. 运行 `python -m attestflow verify --task TASK-*`，把验证结果绑定到当前 run。
10. 运行 `python -m attestflow transition TASK-* verified` 和 `python -m attestflow transition TASK-* accepted`。
11. 运行 `python -m attestflow close TASK-*`。
12. 重复 `next -> dispatch -> verify --task -> close`。

## 验收标准

- 核心协议和项目适配层分离。
- 保留来源会话中 harness 的本质：需求收敛、BDD/TDD、状态推进、证据和恢复。
- 明确定义状态流转、门禁、证据、恢复和多 Agent 所有权。
- 不硬依赖历史项目、外部 Agent 工具链、Docker、GitHub 或 Python-only 工作流。
- 可从本设计直接推导出实现计划，不需要再猜主要行为。
