# 通用开发 Harness 设计

日期：2026-05-29
状态：核心本地闭环已实现，AI-first 任务导入和 planner capability 已纳入主路径
来源会话：019e7244-0bad-7970-81c4-af4c4323486c

## 目标

本项目要沉淀一个可复用的开发 harness，让其他项目可以直接采用同一套开发控制流程，而不是复制某个业务项目的临时脚手架。

Harness 不是测试框架。它是开发流程控制系统，把下面这条链路变成明确、可重复、可恢复、可审计的工作流：

```text
requirement source -> draft spec -> clarification -> approved spec -> AI planning -> task import
-> requirement boundary -> BDD scenario -> unit test -> implementation
-> verification -> evidence -> task state transition -> next executable task
```

Raw source 只能生成 draft spec，不能直接生成 ready task。只有 approved spec 才能进入 AI planning；planner JSON 仍然必须经过 `task import` 的确定性校验后，才可能进入 autopilot 或 dispatch。

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
      ci-provider-schema.md
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
    ci.py
    ci_adapters.py
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

当前实现保持标准库优先，覆盖配置读取、AI planner JSON 导入、任务校验、任务选择、任务启动、capability 运行、顶层 autopilot、PR/CI/release provider、证据目录、断点恢复、secret scan、项目 adapter、基础模板和测试。

## `harness.yml`

每个接入项目有一个 `harness.yml`：

```yaml
schema_version: 1
project:
  name: example-project
  default_branch: main
  language: zh-CN

paths:
  tasks: harness/tasks
  runs: harness/runs
  gates: harness/gates
  locks: harness/locks
  capability_runs: harness/capability-runs
  ci_runs: harness/ci-runs
  pr_runs: harness/pr-runs
  release_runs: harness/release-runs
  sources: harness/sources
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
    path_template: ../.attestflow-worktrees/{project}/{task_id}-{run_id}

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
    - README.zh-CN.md
    - AGENTS.md
    - harness.yml
    - pyproject.toml
    - package.json
    - docs/contracts/capability-schema.md
    - docs/contracts/ci-provider-schema.md
    - docs/contracts/planner-output-schema.md
    - docs/contracts/pr-provider-schema.md
    - docs/contracts/release-provider-schema.md
    - docs/contracts/session-adapter-schema.md
    - docs/contracts/task-schema.md
    - docs/design/universal-harness.md

token_economy:
  enabled: true
  budgets:
    default_input_tokens: 24000
    planner_input_tokens: 32000
    releaser_input_tokens: 32000
  context_cache:
    enabled: true
    path: harness/context-cache
    max_summary_bytes: 800
  provider_cache:
    enabled: false
    path: harness/provider-cache
  incremental_context:
    enabled: true
  evidence_summary:
    enabled: true
    max_output_bytes: 2000

execution:
  docker:
    enabled: false
    compose_service: app

integrations:
  git_provider: optional
  ci_provider: optional
  pr_provider: optional
  release_provider: optional
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

`capability list/show` 展示合同；`plan` 执行目标级 planner capability；`capability run <name> <task>` 执行任务级 capability。Codex、Claude Code、OpenCode、外部 skill 或其他编程 Agent CLI 只通过 agent provider adapter 接入，不能成为 Attestflow core 的前置条件。内置 provider preset 会自动接线 `plan` 和 `capability run`；显式 `--command` 仍作为覆盖入口。Capability command 支持 `timeout_seconds`，避免 AI provider 卡住时顶层自治 loop 无限等待。

如果启用 `sessions.worktree.enabled`，任务进入 `in_progress` 时会先从当前 Git HEAD 创建独立 worktree。`session adapter`、task-scoped capability 和 `verify --task` 都以该 worktree 为 cwd；`harness/runs`、`harness/capability-runs` 和任务状态文件仍写在控制项目中。`close` 会先把 worktree dirty state 提交成 task commit，再用 `git merge --ff-only` 合回控制仓库；控制仓库如果已经漂移则 close 失败，任务保持 `accepted`。run metadata 的 `workspace` 记录控制根、worktree path、`commit_before`、`commit_after` 和 `applied_to_control`。

Provider input 包含 `repository_context`，由 Attestflow 确定性生成：

- `tree`：受限文件树
- `documents`：`README.md`、`harness.yml`、核心 contract/design 文档等
- `files`：任务 `files.read` / `files.write` 指向的文本片段
- `dynamic_context`：允许 provider 后续请求 `file_slice`、`symbol_lookup`、`dependency_neighbors`、`semantic_search`、`change_history` 或 `test_mapping`
- `limits`：实际使用的上下文限制

默认排除 `.git`、`node_modules`、`__pycache__`、`harness/tasks`、`harness/runs`、`harness/capability-runs`、`harness/ci-runs`、`harness/pr-runs`、`harness/release-runs`、`harness/context-cache` 和 `harness/provider-cache`，避免把运行证据、缓存和依赖目录传给编程 Agent。

`token_economy` 是确定性省 token 层：预算门会在 provider 调用前估算 input token，超预算时把全文 context 替换为摘要、hash 和 cache key；`context resolve` 负责按需取片；`incremental_context` 只传已有 capability output 摘要；release handoff 默认摘要化大型 evidence；`usage report` 聚合真实 provider usage。`provider_cache.enabled` 默认关闭，因为它会复用上一次成功模型输出，适合 deterministic prompt 或 CI 中的重复验证场景。

## 每任务独立会话

`dispatch TASK` 是 AI-first 执行入口；`dispatch --limit N` 是批量入口：

```text
ready task -> dispatch -> run -> agent_session -> prompt packet -> external AI session
```

单个 task dispatch 必须原子完成：

- 校验 task 处于 `ready`
- 创建 task/file locks
- 可选创建 per-task git worktree，并把 worktree 写入 run metadata
- 创建 `harness/runs/<run_id>/`
- 写入 `metadata.yml`、`ledger.jsonl`、`evidence.md`
- 写入 `prompt.md`，包含任务边界、写文件范围、BDD、unit test、验收标准和验证命令
- 写入 `session.yml`，包含 `session_id`、`agent_provider`、role、状态、prompt packet、启动命令和恢复命令
- 将 `agent_session` 写回 run metadata
- 将 `evidence.session` 写回 task
- 如果配置了 `sessions.launch_command`，按 `docs/contracts/session-adapter-schema.md` 执行 command adapter，写入 `session-adapter-input.json`、`session-adapter-output.json` 和 stdout/stderr logs；`provider_options.timeout_seconds` 会终止卡住的 launch/resume adapter 并写失败 session

任务 close 时，如果 run metadata 指向 worktree，Attestflow 会执行：

```text
git add -A
git commit -m "attestflow TASK-*"
git merge --ff-only <task-commit>
```

这让新文件、删除和修改都能进入控制仓库，同时避免在控制仓库已前进时自动制造隐式冲突。

核心不绑定 Codex、Claude Code、OpenCode 或其他平台。项目可以用 `sessions.launch_command` / `sessions.resume_command` 适配任意编程 Agent CLI。没有配置启动命令时，dispatch 至少生成独立 session packet；接入层可以读取 packet 后启动会话。

当 `sessions.agent_provider` 是 `codex`、`claude-code` 或 `opencode` 时，Attestflow 会使用内置 provider preset 生成 adapter command。项目可以通过 `sessions.provider_options.command`、`launch_args`、`resume_args` 和 `timeout_seconds` 覆盖底层 CLI；`doctor_args`、`doctor_timeout_seconds` 和 `doctor_failure_patterns` 覆盖 provider preflight。默认 preflight 不执行项目任务，只检查 provider 是否具备可运行的登录、授权或凭证状态。

批量 dispatch 不依赖人工挑任务。Attestflow 先按 `priority, id` 排序，再选择满足以下条件的 ready 任务：

- 依赖已 `done` 或 `archived`
- task schema 和 ready 门禁有效
- 没有 active blocker 或外部输入缺口
- `files.write` 未被现有 lock 占用
- 和本批次已选任务的 `files.write` 没有重叠

每个被选任务仍然单独创建 run、locks、session packet 和 evidence；如果某个 session 启动失败，CLI 返回非零并保留已写入 evidence。session adapter 返回 `blocked` 时，任务会进入 `blocked` 并释放锁，顶层 autopilot 也记录为 blocked run，而不是把可恢复的认证或外部前置条件误报为执行失败。

## AI Planning 和任务落盘

任务产生分两层：

```text
approved spec -> programming agent provider -> planner JSON -> attestflow task import -> task JSON
```

编程 Agent 负责判断和拆解，不直接写 `harness/tasks/**/*.json`。正式 requirement source 入口只有一条：

```bash
python -m attestflow go <requirement-source>
```

Raw 文本、PRD、Issue、评审意见或 CI failure 不能绕过 draft spec 和 approval 直接进入 planner 或 `task import`。低层 `plan` / `task import --from-json` 能力仍然存在，但只能用于 approved spec 派生出的上下文，或已经由外部流程批准边界的 planner JSON；它们不是 raw goal 的等价入口。

`go --from-spec SPEC-####/spec.md --approve` 会把 approved spec 内容作为 planner capability input，调用 `--command`、`capabilities.planner.command` 或内置 provider adapter，将 stdout 作为 planner JSON，再复用 `task import`。Attestflow 接收 planner JSON 后执行确定性处理：

- 分配递增的 `TASK-*` ID
- 解析 planner 内部 `key` 依赖
- 补齐默认字段
- 校验 task schema 和 ready 门禁
- 拒绝缺少 BDD、unit tests、acceptance 或写文件范围的任务
- 只在全部任务可通过校验后写入 runtime task JSON
- 保存 capability evidence：`input.json`、`stdout.log`、`stderr.log`、`output.json`

任务 JSON 是 runtime 的事实来源，不是人工主编辑界面。人工只负责不可自动判断的目标取舍、凭证授权和外部业务决策。

## 任务顺序和 dry-run 计划

任务拆解后可以是每个任务一个独立 JSON 文件；执行顺序不依赖人工阅读文件列表，也不依赖文件系统顺序。Attestflow 聚合 `harness/tasks/*/*.json` 后按以下规则计算顺序：

```text
dependencies -> state/DoR -> locks/files.write -> priority -> id
```

- `dependencies` 决定拓扑约束；依赖未 `done` 或 `archived` 的任务不会进入当前批次。
- `state` 和 Definition of Ready 决定任务是否可执行；`blocked`、`needs_clarification`、schema 不合法或仍有 external inputs 的任务会被跳过并报告原因。
- 有效 file locks 会阻止任务进入 dry-run 批次；stale lock 会在 autopilot run/resume 开始时自动释放并写入 recovery 事件。
- 同一批次内 `files.write` 不能重叠；冲突任务会被推迟到后续批次。
- `autopilot.resources.model_concurrency`、`max_test_cost`、`max_model_tokens` 和 `ci_queue` 会限制同一批 active action 或 ready batch 的资源预算。
- 在满足以上条件后，任务按 `priority, id` 排序。

`python -m attestflow autopilot --dry-run --limit N` 是顶层自治执行前的只读计划视图。如果已有 `in_progress`、`review`、`verified` 或 `accepted` 任务，它会先展示这些 active task 的下一步动作，避免继续扩大 WIP；否则才模拟每一批 ready 任务完成后的后续可执行批次，输出批次和跳过原因。它不调用 Agent、不创建 run、不移动任务状态。这个命令承担 “plan-order” 能力，是后续 `autopilot run` 的确定性决策核心。

`python -m attestflow autopilot --run --goal "..." --limit N --max-steps M` 是当前最小可执行 orchestrator。它创建 `harness/autopilot-runs/<run>/metadata.json` 和 `ledger.jsonl`，记录 `autopilot_started`、`planner_started`、`planner_finished`、`autopilot_resumed`、`active_actions_planned`、`task_action_planned`、capability 事件、`repair_requested`、`repair_finished`、`batch_planned`、`task_started`、`task_dispatched`、失败和结束事件。`metadata.json` 是快速状态索引，包含参数、goal、planner run、planned task ids、状态、暂停原因、结束时间、动作、分发、失败、阻塞、跳过原因、release evidence、`release_status` 和 `release_repair_planner`；`ledger.jsonl` 是 append-only 审计日志。执行顺序是有 `--goal` 时先调用 planner capability 导入 runtime task JSON，再按 `limit` 批量推进 active task，最后复用 `start_task` 创建新任务自己的 run、锁、prompt packet 和 session。active task batch 会跳过同批次 `files.write` 冲突。默认 batch size 和 step budget 来自 `harness.yml` 的 `autopilot.default_limit` / `autopilot.max_steps`，`--limit` 和 `--max-steps` 只做本次运行覆盖。`max_steps` 约束本次最多执行多少个动作批次或分发批次，避免未验证阶段无限推进；如果到点后仍存在 active task、ready batch 或 release gate，run 会记录 `status: paused` 和 `pause_reason: max_steps_reached`，表示可恢复而不是完成。

`python -m attestflow autopilot --resume --max-steps M` 会读取最新 autopilot run 的 `metadata.json`，复用同一个 run 目录继续执行，追加 `ledger.jsonl`，并把 actions、dispatched、steps 和 `resume_count` 累加回 metadata。旧 metadata 会先迁移补齐 `actions`、`planned`、`dispatched`、`releaser_tasks`、`resume_count`、`loop_cycles` 和 `state_machine`，避免字段漂移。`--run` 用于开启一轮新自治运行；`--resume` 用于继续上一轮运行。`--loop` 可以和 `--run` 或 `--resume` 搭配，受限地反复 resume paused run，直到 `finished`、`blocked`、`failed` 或 cycle 用完；`--until terminal` 是全自动安全入口，会在同一个 run 上自动 resume，直到 `finished`、`blocked` 或 `failed`。默认 cycle 上限和等待时间来自 `harness.yml` 的 `autopilot.max_loop_cycles` / `autopilot.loop_interval_seconds`，`--max-cycles N` 和 `--interval-seconds S` 可临时覆盖。Loop 会写入 `metadata.json.loop_cycles` 和 `metadata.json.loop_stop_reason`；`max_cycles_reached` 表示安全阈值触发且仍处于 paused，`terminal_status` 表示已进入终态。

当前 `autopilot --run` 自动完成：

```text
goal -> plan -> active action batch or dispatch -> ledger
```

active action 的确定性状态机：

```text
in_progress -> bdd -> tdd -> implementer -> review
review -> reviewer -> optional verifier -> verify -> verified
verified -> accepted
accepted -> publish_changes -> pr_ensure -> ci_status -> pr_status -> close
```

启用 worktree 时，`accepted` 会先执行：

```text
accepted -> apply_worktree -> publish_changes -> pr_ensure -> ci_status -> pr_status -> close
```

失败修复规则：

- capability evidence 只有 `status: passed` 才算该 capability 已完成；`failed` evidence 会让 dry-run 和 run 继续指向该 capability，而不是误判通过。
- 失败会按来源选择最小 repair target：`bdd` 失败回 `bdd`，`unit` 验证失败回 `tdd`，实现/lint/typecheck/CI 失败回 `implementer`，PR review 状态失败回 `reviewer`，release provider 失败回 planner 生成最小修复任务。
- repair 会写入 `evidence.autopilot.repair.target_capability`，清掉该 target 之后的 stale capability evidence，并受 `autopilot.max_repair_attempts` 限制。
- repair 成功后清掉 pending repair，重新进入 `review -> reviewer -> optional verifier -> verify`。
- repair 次数超过上限时，当前 autopilot run 以 `failed` 结束，保留失败 evidence 和 ledger。
- 如果配置了 worktree，accepted 任务会先运行 `apply_worktree`，把 task commit ff-only merge 回控制仓库；之后才 publish、创建/更新 PR 和采集 CI/PR evidence。
- 如果配置了 `integrations.git_provider`，accepted 任务会运行 `publish_changes`，把 `harness/git-runs/*/output.json` 写入 `task.evidence.git`；`published` 或 `skipped` 表示可以继续后续 gate，`blocked` 会停止本轮 autopilot。
- 如果配置了 `integrations.pr_provider`，accepted 任务会先运行 `pr_ensure`，把 `harness/pr-runs/*/output.json` 写入 `task.evidence.pr_request`；`open`、`draft`、`merged` 或 `skipped` 表示 change request 已可继续后续 gate，`blocked` 会停止本轮 autopilot。
- 如果配置了 `integrations.ci_provider`，accepted 任务会运行 `ci_status`，把 `harness/ci-runs/*/output.json` 写入 `task.evidence.ci`；CI `passed` 或 `skipped` 才继续，`running`、`queued` 或 `unknown` 会暂停等待 resume 重新采集。
- 如果配置了 `integrations.pr_provider.auto_merge: true`，且 PR request 为 `open`，accepted 任务会在 CI 通过后运行 `pr_merge`，把 `harness/pr-runs/*/output.json` 写入 `task.evidence.pr_merge`；`merged` 或 `skipped` 才继续最终状态采集，`unknown` 会暂停等待 resume，`open`、`draft` 或 `blocked` 会停止本轮。
- 如果配置了 `integrations.pr_provider`，accepted 任务会运行 `pr_status`，把 `harness/pr-runs/*/output.json` 写入 `task.evidence.pr`；PR `merged` 或 `skipped` 才继续 close，`unknown` 会暂停等待 resume 重新采集。
- 如果配置了 `capabilities.releaser`，所有任务都有效地处于 `done` 或 `archived` 后，autopilot 会先运行 top-level releaser capability，把 release handoff 写入 `metadata.json.releaser` 和 `metadata.json.releaser_tasks`。有效完成要求 task registry 没有重复 id，文件名和 id 一致，目录状态和文件内 `state` 一致，并通过 task schema 校验。
- 如果配置了 `integrations.release_provider`，release gate 会把已完成任务摘要、task run evidence、CI evidence、PR evidence 和可选 `release_handoff` 一起传给 provider；如果 completed task 或其 JSON/YAML evidence 损坏，Attestflow 会在创建 release run 前 fail closed。输出 `harness/release-runs/*/output.json` 写入 autopilot `metadata.json.release`，provider 状态写入 `metadata.json.release_status`。只有 `released` 或 `skipped` 算 release gate 完成；`running`、`queued` 或 `unknown` 会暂停等待 resume 重新采集，`blocked` 会停止本轮；`failed` 且 planner capability 已配置时，会把 release failure summary、release evidence 和可选 release handoff summary 交给 planner 生成修复任务，导入后回到普通 task loop。
- 没有可执行 batch 但仍有未完成 skipped task 时，autopilot 会以 `blocked` 结束并把对应 task ids 写入 metadata，避免把等待输入、无效依赖或非法任务误判为完成。
- `autopilot --run` 到达 `--max-steps` 且仍有确定性下一步时会以 `paused` 结束，保留 `pause_reason: max_steps_reached`，供 `--resume` 接续。
- `autopilot --run` 遇到 blocked 结果会以非零退出码结束，避免上层自动化把等待外部状态误判为完成。

在 capability 命令配置完整且验证门禁通过时，单个任务可以从 `ready` 自动推进到 `done`；如果配置了 `capabilities.verifier`，autopilot 会在本地 `verify --task` 前先运行 verifier capability。全部任务完成后可以基于任务摘要和交付 evidence 采集 release evidence。release failed 可以经 planner 生成修复任务并重新进入 task loop。自治循环的确定性边界是：凭证、外部服务状态和业务取舍会进入 blocked 或 paused，而不是伪装成已完成。

```text
plan -> dispatch -> bdd -> tdd -> implement -> review -> verify -> repair failure -> close -> next task
```

run-level state machine 只允许：

- `in_progress -> paused`：外部状态 pending 或 step/cycle 安全上限触发。
- `paused -> in_progress`：`autopilot --resume`、`--loop` 或 `--until terminal` 继续。
- `in_progress -> blocked`：外部输入、凭证、审批或 provider 明确 blocked。
- `in_progress -> failed`：contract、验证、provider 或 repair 无法自动收敛。
- `in_progress -> finished`：任务和可选 release gate 全部 terminal。

## Task-scoped Capability 执行

`bdd`、`tdd`、`implementer`、`reviewer` 和 `verifier` 共享一个任务级执行入口；`releaser` 由 top-level release gate 调用：

```bash
python -m attestflow capability run reviewer TASK-0001
```

执行规则：

- 加载 runtime task JSON
- 构造 capability input，包含 task、project、commands、repository_context、incremental_context、capability contract 和固定 instructions
- 先经过 token budget gate；命中 provider cache 时直接写新 run evidence，不调用外部模型 provider
- 调用 `--command`、`capabilities.<name>.command` 或内置 provider adapter
- 保存 `input.json`、`stdout.log`、`stderr.log` 和 `output.json`
- provider 非零退出、stdout 不是 JSON object 或 capability output schema 不合法时失败
- provider 超时会终止 process group，写入 stderr log，并保留 capability run evidence
- `bdd`、`tdd`、`implementer`、`reviewer` 和 `verifier` 都有 typed artifact schema；`updated_files`、`test_files`、`written_files` 会和 `files.write` 校验，git workspace 中的实际新增/修改文件也必须在 `files.write` 内
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

## 阻塞协议

阻塞必须是结构化状态，不是 `notes` 里的自然语言。任务进入 `blocked` 时必须写入 `blockers[]`，每条 blocker 包含 `id`、`type`、`reason`、`unblock_condition`、`owner`、`source`、`status`、`created_at` 和 `resolved_at`。

确定性规则：

- `ready` 和其他可执行状态不能有 active blocker。
- `blocked` 必须至少有一个 active blocker。
- `external_inputs` 非空表示还有凭证、服务或业务决策未满足；这类任务不能被 `next` 调度。
- session adapter 或 task capability 返回 `blocked` 时，Attestflow 自动移动任务到 `blocked`，写 active blocker，并保留输入/输出 evidence。
- `unblock` 只解决指定 blocker；所有 active blocker 解决后，任务才回到 `ready`。

## CLI

稳定 CLI 表面：

```bash
python -m attestflow init --adapter python --agent-provider codex
python -m attestflow doctor
python -m attestflow validate-config
python -m attestflow validate-task TASK
python -m attestflow go REQUIREMENT_SOURCE
python -m attestflow go --from-spec harness/specs/SPEC-0001/spec.md --approve --non-interactive
python -m attestflow task import --from-json PLAN.json
python -m attestflow source import --kind github-issue --from-json ISSUE.json
python -m attestflow schema migrate --kind harness-config --from-json harness.yml --write
python -m attestflow schema export --type task --json
python -m attestflow schema openapi --json
python -m attestflow plugin list --json
python -m attestflow governance policy --json
python -m attestflow tasks
python -m attestflow next
python -m attestflow autopilot --dry-run --limit 3
python -m attestflow autopilot --run --limit 1 --max-steps 1
python -m attestflow autopilot --resume --max-steps 8
python -m attestflow autopilot --run --loop --max-cycles 20 --max-steps 1
python -m attestflow autopilot --status --json
python -m attestflow inspect --run RUN
python -m attestflow inspect --diff OLD_RUN NEW_RUN
python -m attestflow recover
python -m attestflow recover --apply
python -m attestflow recover --apply --resume-interrupted
python -m attestflow dispatch TASK
python -m attestflow dispatch --limit 3
python -m attestflow start TASK
python -m attestflow block TASK --reason REASON
python -m attestflow unblock TASK --blocker BLK --resolution RESOLUTION
python -m attestflow evidence TASK
python -m attestflow evidence export TASK --out DIR
python -m attestflow evidence bundle --run RUN --out DIR
python -m attestflow evidence bundle --release RELEASE --out DIR
python -m attestflow evidence verify DIR --check-source
python -m attestflow contract validate capability-output output.json
python -m attestflow verify
python -m attestflow verify --task TASK
python -m attestflow ci providers
python -m attestflow ci status --task TASK
python -m attestflow pr ensure TASK
python -m attestflow pr merge TASK
python -m attestflow pr status TASK
python -m attestflow release status
python -m attestflow close TASK
python -m attestflow resume
python -m attestflow secret-scan
```

命令职责：

- `init`：在目标项目生成模板文件；`--adapter generic|python|node|go|rust|monorepo|docker|bazel|java|kotlin|dotnet|swift|dart|ruby|php` 会复制对应项目 adapter 文档到 `harness/adapters/<adapter>/`、写入 `project.adapter`，并按项目文件生成基础验证命令；`--agent-provider codex|claude-code|opencode` 会写入内置 provider preset。
- `doctor`：检查配置、项目命令 executable、runtime 目录、任务 schema、provider CLI 和 provider preflight；不执行项目任务，但会尽早暴露缺少测试命令、登录、授权或凭证不可用。
- `validate-config`：验证 `harness.yml`。
- `validate-task`：验证 schema、状态、目录、依赖和门禁。
- `go REQUIREMENT_SOURCE`：把 raw 文本或文档保存为 source evidence，生成 draft spec，并在 approval 前停止。
- `go --from-spec SPEC --approve`：只在 spec 已批准且没有 open questions 时，把 approved spec 交给 planner/autopilot。
- `task import --from-json`：导入已经批准边界的 planner JSON，校验后写入 runtime task JSON；它不是 raw PRD 或 raw goal 的入口。
- `source import --kind github-issue|linear-ticket|jira-ticket|pr-review-comment|ci-failure --from-json FILE`：保存外部来源快照到 `harness/sources`，并创建带 `source` 元数据的 `proposed` task；source evidence 后续必须先收敛成 approved spec，再由 planner 生成可执行 ready task。
- `schema migrate/export/openapi`：迁移旧 harness 配置、导出 JSON Schema，并输出 OpenAPI 3.1 component schema，供 provider 作者和 CI 使用。
- `plugin list`：从 `plugins.directories` 扫描 `plugin.json`，只做注册发现和 manifest 校验，不执行插件代码。
- `governance policy`：输出支持的 schema version、provider contract version、稳定发布流程和 `1.0` 前破坏性变更规则。
- `tasks`：按状态和优先级列出任务。
- `next`：返回最高优先级、依赖已完成、文件未锁定的 `ready` 任务。
- `autopilot --dry-run`：只读生成自动执行计划，优先展示 active task 下一步动作和 repair mode，否则展示可执行 ready 批次和不可执行任务原因，不启动 Agent、不改状态。
- `autopilot --run`：创建顶层运行台账，先按 `limit` 和 `autopilot.resources` 批量推进 active task capability/状态动作，失败时按来源选择 repair target，accepted 任务会先 apply worktree，再执行 `pr ensure`，随后采集 CI evidence；若 `integrations.pr_provider.auto_merge: true` 且 PR 可合并，会执行 `pr merge`，最后采集 PR status evidence；全部任务完成后会采集 release evidence，并记录动作、恢复、修复、CI、PR、release、分发或失败事件；`max_steps` 到点但仍有下一步时记录为 `paused`。
- `autopilot --run/--resume --loop` / `--until terminal`：在同一个 top-level run 上自动续跑 paused 状态，受 `harness.yml` batch/step/loop policy 和 CLI override 限制，并记录 `loop_stop_reason`，不隐藏终态失败或阻塞。
- `autopilot --resume`：复用最新 autopilot run 的 `metadata.json` 和 `ledger.jsonl`，继续执行并追加事件。
- `autopilot --status`：读取最新 autopilot run 的 `metadata.json`，输出状态、暂停原因、步数、actions、planned、dispatched、releaser、release、failed 和 blocked 摘要；`--json` 输出完整 metadata。
- `inspect --run RUN`：读取指定或最新 autopilot run，并合并 `metadata.json`、`ledger.jsonl`、blocked task 文件和 provider `failure.json`，输出 timeline、blocker dashboard、provider failure drilldown 和 next-action；`--json` 输出同结构数据。
- `inspect --diff OLD NEW`：对比两个 autopilot run 的状态、暂停原因、release status、actions、planned/dispatched、failed、blocked、cancelled 和 releaser task 变化。
- `recover`：默认只读诊断 orphan autopilot run、task JSON 状态目录错位、已 finalized 的 stale worktree 和被取消的 provider session；`--apply` 只执行确定性修复，写入缺失 run metadata、移动错位 task、清理 finalized worktree，并生成 `harness/snapshots/ledger-snapshot-*.json`；`--resume-interrupted` 会显式调用 session resume adapter 恢复中断 provider session。
- `dispatch`：AI-first 执行入口，创建 run、locks、独立 agent session、prompt packet，并按配置启动外部 AI 会话。
- `dispatch --limit N`：批量选择依赖满足、写范围不冲突且未被锁定的 ready 任务，并逐个创建独立 session。
- `start`：低层生命周期入口，仍会创建 session packet，保留给脚本和兼容场景。
- `block`：写入结构化 active blocker，记录 reason / unblock condition / owner / source，并移动到 `blocked`。
- `unblock`：解决指定 blocker；没有 active blocker 后把任务转回 `ready`。
- `evidence`：读取 evidence packet；`evidence export` 会导出 task、run、ledger、capability output 和 manifest；`evidence bundle --run/--release` 会导出顶层 autopilot 或 release bundle，附带可复现 manifest、audit report 和 PR comment artifact；`evidence verify` 校验 bundle hash/size，`--check-source` 额外检测源 evidence 是否过期。
- `contract validate`：本地校验 planner、capability、session、CI、PR、release 或 task contract。
- `provider contract`：用固定夹具验证 Codex/Claude Code/OpenCode provider 能产出 planner、task、reviewer、verifier、release 五类合同 JSON。
- `verify`：执行配置的质量门禁，用于临时或 CI 验证。
- `verify --task`：执行配置的质量门禁，并把命令结果写入当前 task run；`transition TASK verified/accepted` 必须看到当前 run 的通过验证证据。
- `ci providers`：列出内置 CI provider preset。
- `ci status`：执行 CI provider contract，保存外部 CI 状态 evidence；带 `--task TASK-*` 时写入 `task.evidence.ci`。
- `pr ensure`：执行 PR provider contract，创建或更新外部 PR/change request evidence；带 task id 时写入 `task.evidence.pr_request`。
- `pr merge`：执行 PR provider contract，请求合并外部 PR/change request；带 task id 时写入 `task.evidence.pr_merge`。
- `pr status`：执行 PR provider contract，保存外部 PR/change 状态 evidence；带 task id 时写入 `task.evidence.pr`。
- `release status`：执行 Release provider contract，传入已完成任务摘要和可解析交付 evidence，保存外部发布 evidence。
- `close`：校验当前 run 的 DoD evidence，释放锁，写最终证据并移动到 `done`。
- `resume`：读取未完成 run，输出下一步动作。
- `secret-scan`：扫描已跟踪或项目文件中的明显密钥。

## CI Provider

CI provider 和编程 Agent provider 一样走 contract，不把 GitHub Actions、Buildkite 或自建 CI 写进 core：

```text
CI system -> provider command -> CI output JSON -> harness/ci-runs evidence
```

`integrations.ci_provider.provider: command` 调用项目配置的任意命令；`provider: github-actions` 使用内置 adapter 调用 `gh run list` 并映射到统一 `status`。CI/PR/Release command provider 都支持 `timeout_seconds`，避免外部系统卡住时顶层 loop 无限等待。`ci status` 只采集外部 CI 状态快照，不替代本地 `verify --task` 的 DoD evidence；`ci status --task TASK-*` 会额外把输出路径写入对应任务的 `task.evidence.ci`。release gate 可以同时引用本地 run evidence、CI evidence 和 PR evidence。CI `running`、`queued` 或 `unknown` 会让 autopilot 暂停为 `pause_reason: external_status_pending`，等待 `--resume` 重新采集，而不是误判为实现失败。

## PR Provider

PR provider 创建/更新并采集外部 PR/change 状态：

```text
Git provider -> provider command -> PR output JSON -> harness/pr-runs evidence
```

`integrations.pr_provider.provider: command` 调用项目配置的任意命令。`pr ensure [TASK-*]` 创建或更新 change request，并把输出写为 `task.evidence.pr_request`；`pr merge [TASK-*]` 请求合并，并把输出写为 `task.evidence.pr_merge`；`pr status [TASK-*]` 保存 PR 状态快照，并把输出写为 `task.evidence.pr`。autopilot 会在 `accepted` 阶段先 `ensure`，再跑 CI gate；只有显式配置 `integrations.pr_provider.auto_merge: true` 时才会在 CI 通过后执行 `merge`。`status` 的 `merged` 和 `skipped` 允许继续 close；`open`、`draft` 或 `blocked` 会让 autopilot 停在 blocked，等待外部系统状态变化。

## Release Provider

Release provider 采集外部发布状态：

```text
Release system -> provider command -> release output JSON -> harness/release-runs evidence
```

`integrations.release_provider.provider: command` 调用项目配置的任意命令。`release status` 的输入包含 `done_tasks`、每个已完成任务的标题/范围/验收摘要，以及可解析的 `ci`、`pr_request`、`pr`、`verify` evidence 内容。它保存发布状态快照；如果所有任务都已 `done` 或 `archived` 且没有新的可执行任务，autopilot 会写顶层 `metadata.json.release`。`released` 和 `skipped` 表示发布收敛；`running`、`queued` 或 `unknown` 表示外部发布仍在推进，会让 autopilot paused 并可 resume；`blocked` 会让 autopilot 停在 blocked；`failed` 且 planner capability 已配置时会触发 release repair planning，否则本轮 failed。

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
harness/ci-runs/
  ci-2026-05-29T20-00-00Z/
    input.json
    stdout.log
    stderr.log
    output.json
harness/pr-runs/
  pr-2026-05-29T20-00-00Z/
    input.json
    stdout.log
    stderr.log
    output.json
harness/release-runs/
  release-2026-05-29T20-00-00Z/
    input.json
    stdout.log
    stderr.log
    output.json
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
- `node`：检测 `pnpm-lock.yaml`、`yarn.lock` 或默认 `npm`，并把 `package.json` 的 `test`、`lint`、`typecheck`、`build` scripts 映射到 harness commands。

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

1. 运行 `python -m attestflow init --adapter python --agent-provider codex`，或按项目选择 `generic` / `node` / `monorepo` / `docker` / `bazel` / `java` / `kotlin` / `dotnet` / `swift` / `dart` / `ruby` / `php` 和 `claude-code` / `opencode`。
2. 运行 `python -m attestflow doctor`，确认配置、目录、provider CLI 和 provider preflight 可用。
3. 让 Agent 审核生成的 `harness.yml` 和项目命令，只有凭证或业务取舍需要人工确认。
4. 运行 `python -m attestflow go <requirement-source>`，把 raw 文本或文档保存为 source evidence 并生成 draft spec。
5. 审阅 draft spec，完成 clarification，确保 `Open Questions` 为 `None`、`无` 或空。
6. 批准 spec 后运行 `python -m attestflow go --from-spec harness/specs/SPEC-0001/spec.md --approve --non-interactive`，由 approved spec 生成 planner JSON 并导入任务；高级流程也可以把 approved spec 派生出的 planner JSON 交给 `python -m attestflow task import --from-json plan.json`。
7. 用 `python -m attestflow next` 选择下一个 ready 任务。
8. 用 `python -m attestflow autopilot --dry-run --limit N` 审计执行批次和跳过原因。
9. 用 `python -m attestflow autopilot --run --limit N --max-steps M` 创建顶层运行台账，并自动推进 planner、capability、review、verify、worktree apply、PR/CI gate、close 和 release provider；也可以运行 `python -m attestflow dispatch TASK-*` 或 `python -m attestflow dispatch --limit N` 手动分发。
10. Agent 按 BDD -> unit -> implementation 执行。
11. 运行 `python -m attestflow transition TASK-* review`。
12. 运行 `python -m attestflow verify --task TASK-*`，把验证结果绑定到当前 run。
13. 运行 `python -m attestflow transition TASK-* verified` 和 `python -m attestflow transition TASK-* accepted`。
14. 如配置了外部交付 provider，运行 `python -m attestflow pr ensure TASK-*`、`python -m attestflow ci status --task TASK-*`、可选 `python -m attestflow pr merge TASK-*`、`python -m attestflow pr status TASK-*` 保存 evidence。
15. 运行 `python -m attestflow close TASK-*`。
16. 重复 `autopilot --dry-run -> autopilot --run`；自动路径会把 PR/CI/release evidence 收敛进同一个可恢复 autopilot loop。

## 验收标准

- 核心协议和项目适配层分离。
- 保留来源会话中 harness 的本质：需求收敛、BDD/TDD、状态推进、证据和恢复。
- 明确定义状态流转、门禁、证据、恢复和多 Agent 所有权。
- 不硬依赖历史项目、外部 Agent 工具链、Docker、GitHub 或 Python-only 工作流。
- 可从本设计直接推导出实现计划，不需要再猜主要行为。
