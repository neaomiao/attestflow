# Attestflow

[English](README.md)

Attestflow 的目标是把需求收敛、AI 任务拆解、BDD、单元测试、实现、验证、证据和任务状态推进固化为可执行流程。

字段名、状态名和命令名保持英文，便于代码和 CI 解析；说明文档使用中文。

核心原则：AI 能完成的工作不进入人工主路径。编程 Agent 负责拆解目标、生成任务草案、补充 BDD 和验收标准；Attestflow 负责确定性校验、分配任务 ID、落盘、锁、验证和证据。

## 5 分钟 Quickstart

不需要模型账号，可以先用内置示例验证开源核心闭环：

下面命令假设当前 shell 里 `python` 指向 Python 3.11+；如果没有，请先激活 venv，或把 `python` 替换成你的 Python 3.11+ 解释器。

```bash
cd examples/python-basic
PYTHONPATH=../.. python -m attestflow doctor
PYTHONPATH=../.. python -m attestflow autopilot --run --goal "Add greeting support" --loop --max-cycles 12 --max-steps 1
PYTHONPATH=../.. python -m attestflow tasks
```

成功后会有一个任务进入 `done`，并生成 `greeter.py`、BDD/unit tests、capability evidence、run ledger 和 close evidence。

接入自己的仓库：

```bash
python -m pip install --user .
python -m attestflow install-smoke --offline
python -m attestflow init --path /path/to/project --adapter python --language zh-CN --agent-provider command
cd /path/to/project
python -m attestflow doctor
```

更多步骤见 [Getting Started](docs/getting-started.md)。Codex、Claude Code、OpenCode 和自定义 provider 配置见 [Provider Cookbook](docs/providers.md)。

## 一条命令接入新项目

推荐主路径是一个 bash/zsh 命令完成安装、初始化、语言选择和 `doctor`：

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/neaomiao/attestflow/main/scripts/bootstrap.sh)"
```

也可以显式传参，适合 CI 或自动化脚本：

```bash
bash scripts/bootstrap.sh --yes --language zh-CN --adapter auto --agent-provider command
```

脚本会列出可选配置并让用户选择：`--language en|zh-CN`、`--adapter auto|generic|python|node|go|rust|monorepo|docker|bazel|java|kotlin|dotnet|swift|dart|ruby|php`、`--agent-provider command|codex|claude-code|opencode`。选择结果会写入 `harness.yml`，其中 `project.language` 是后续文档、prompt 和控制面展示语言的事实来源。

如果已经在本地 clone 了本仓库，也可以直接运行：

```bash
python3 -m attestflow init --path /path/to/project --adapter python --language zh-CN --agent-provider codex
```

初始化会生成 `harness.yml`、任务状态目录、DoR/DoD、Agent 角色和 planner 输出示例；不会预置可执行任务，避免新项目初始化后误跑示例任务。

`--agent-provider` 支持 `command`、`codex`、`claude-code`、`opencode`。如果本机 CLI 不在 `PATH`，可以用 `--agent-command /absolute/path/to/agent` 写入 provider preset。初始化后运行：

```bash
python3 -m attestflow doctor
```

`doctor` 会检查配置、runtime 目录、任务 schema、内置 provider CLI，以及 provider preflight。它不会执行项目任务；Codex 默认运行 `codex doctor --json`，Claude Code 默认运行 `claude auth status`，OpenCode 默认运行 `opencode providers list` 并拒绝 `0 credentials`。

安装包本身用 `install-smoke` 做跨平台冒烟：检查 Python 版本、`attestflow` console script 是否在 `PATH`、包内模板是否存在、`init` 后的 runtime 目录和 `doctor`。`--offline` 会用无外部 provider 的默认 harness 验证离线安装路径；源码仓库还可以加 `--check-template-mirror`，确保 `templates/` 与打包进 wheel 的 `attestflow/templates/` 没有漂移。

`--adapter` 支持 `generic`、`python`、`node`、`go`、`rust`、`monorepo`、`docker`、`bazel`、`java`、`kotlin`、`dotnet`、`swift`、`dart`、`ruby`、`php`。这些 adapter 只生成可审计的默认命令和说明文件；项目特殊命令仍以 `harness.yml` 为准。

## AI-first 任务生成

任务不应该靠人手写 YAML。最直接的主路径是让编程 Agent 输出 planner JSON，然后由 Attestflow 校验并落盘：

更高层的需求入口是 `attestflow go`。它可以接收一段直接粘贴的需求、PRD 文档，或已经批准的 spec：

```bash
attestflow go "实现登录功能"
attestflow go PRD.md
attestflow go --from-spec harness/specs/SPEC-0001/spec.md --approve --non-interactive
```

raw text/document 只会生成 `harness/specs/SPEC-*/spec.md` 草稿，并返回 `spec approval required`；它不会直接执行 planner 或 autopilot。只有 approved spec 才能越过执行边界，进入 planner/autopilot 闭环。

v1 支持 Markdown、TXT、DOCX 和可复制文本层 PDF。DOCX/PDF 解析需要安装 `attestflow[documents]`；扫描 PDF/OCR 暂不支持。

```bash
python3 -m attestflow task import --from-json plan.json
```

也可以从 stdin 接收编程 Agent 或自动化系统输出：

```bash
ai-planner "实现登录功能" | python3 -m attestflow task import --from-json -
```

`task import` 会分配 `TASK-*` ID、解析 planner 内部依赖、补齐默认字段、校验 ready 门禁，并写入 `harness/tasks/ready/*.json`。如果编程 Agent 输出缺少 scope、BDD、unit_tests、acceptance 或 files.write，导入会返回非零并拒绝写入半成品任务。

外部需求入口先保留来源证据，再交给 intake/planner 拆解：

```bash
python3 -m attestflow source import --kind github-issue --from-json issue.json
python3 -m attestflow source import --kind pr-review-comment --from-json review-comment.json
python3 -m attestflow source import --kind ci-failure --from-json ci-failure.json
```

`source import` 会把 GitHub issue、Linear/Jira ticket、PR review comment 或 CI failure JSON 快照保存到 `harness/sources/.../source.json`，并创建 `harness/tasks/proposed/TASK-*.json`。这些 proposed task 保留 `source.kind`、外部 ID、URL、优先级和证据路径；真正的 ready task 仍由 intake/planner 生成，避免把外部 ticket 直接当成可执行实现边界。

也可以使用内置 capability 入口，让 Attestflow 负责组装标准输入、调用配置的编程 Agent 命令、保存 capability evidence，并自动导入任务：

```bash
python3 -m attestflow plan "实现登录功能"
```

编程 Agent provider 可以是 Codex、Claude Code、OpenCode 或其他 agent CLI。`init --adapter <adapter> --agent-provider codex|claude-code|opencode` 会写入所选项目 adapter 文档，并把 capability provider 自动接到内置 agent adapter；显式 `--command` 或 `capabilities.<name>.command` 仍可覆盖。`capabilities.<name>.timeout_seconds` 或 `provider_options.timeout_seconds` 会强制终止卡住的 Agent provider 并保留日志。Provider 最终需要输出符合 `docs/contracts/planner-output-schema.md` 的 planner JSON；可用 `python3 -m attestflow contract validate planner-output output.json` 本地调试。

Attestflow 的内置 capabilities 借鉴 Superpowers 的强制技能流程和 gstack 的专业角色分工，但不依赖它们。外部 skill、编程 Agent CLI 或 API wrapper 只是可选 agent provider；稳定接口是 Attestflow 自己的 capability contract。

Provider input 会自动带上受控仓库上下文：文件树、核心文档片段，以及任务 `files.read` / `files.write` 指向的文本片段。`harness/runs`、`harness/capability-runs`、`harness/ci-runs`、`harness/git-runs`、`harness/pr-runs`、`.git`、`node_modules` 等目录默认排除，避免把运行证据和噪音回灌给 Agent。

## 本地验证

```bash
python3 -m unittest discover -s tests
python3 -m attestflow verify
```

## 核心命令

```bash
python3 -m attestflow validate-config
python3 -m attestflow doctor
python3 -m attestflow capability list
python3 -m attestflow capability show planner
python3 -m attestflow plan "实现登录功能"
python3 -m attestflow go "实现登录功能"
python3 -m attestflow go PRD.md
python3 -m attestflow go --from-spec harness/specs/SPEC-0001/spec.md --approve --non-interactive
python3 -m attestflow capability run reviewer TASK-0001
python3 -m attestflow task import --from-json plan.json
python3 -m attestflow source import --kind github-issue --from-json issue.json
python3 -m attestflow schema migrate --kind harness-config --from-json harness.yml --write
python3 -m attestflow schema export --type task --json
python3 -m attestflow schema openapi --json
python3 -m attestflow plugin list --json
python3 -m attestflow plugin run demo-plugin echo --from-json input.json --json
python3 -m attestflow policy list --json
python3 -m attestflow policy validate strict-local --json
python3 -m attestflow policy apply strict-local --out merged-harness.yml --json
python3 -m attestflow governance policy --json
python3 -m attestflow validate-task harness/tasks/ready/TASK-0001.json
python3 -m attestflow contract validate capability-output output.json
python3 -m attestflow tasks
python3 -m attestflow next
python3 -m attestflow autopilot --dry-run --limit 3
python3 -m attestflow autopilot --run --goal "实现登录功能" --limit 2 --max-steps 20
python3 -m attestflow autopilot --run --limit 1 --max-steps 1
python3 -m attestflow autopilot --resume --max-steps 8
python3 -m attestflow autopilot --run --loop --max-cycles 20 --max-steps 1
python3 -m attestflow autopilot --status --json
python3 -m attestflow inspect --run RUN
python3 -m attestflow inspect --diff OLD_RUN NEW_RUN
python3 -m attestflow recover
python3 -m attestflow recover --apply
python3 -m attestflow recover --apply --resume-interrupted
python3 -m attestflow dispatch TASK-0001
python3 -m attestflow dispatch --limit 3
python3 -m attestflow transition TASK-0001 review
python3 -m attestflow verify --task TASK-0001
python3 -m attestflow transition TASK-0001 verified
python3 -m attestflow transition TASK-0001 accepted
python3 -m attestflow close TASK-0001
python3 -m attestflow block TASK-0001 --reason "missing external input"
python3 -m attestflow unblock TASK-0001 --blocker BLK-0001 --resolution "input provided"
python3 -m attestflow evidence TASK-0001
python3 -m attestflow evidence export TASK-0001 --out attestflow-artifacts/TASK-0001
python3 -m attestflow evidence bundle --run RUN --out attestflow-artifacts/RUN
python3 -m attestflow evidence bundle --release RELEASE --out attestflow-artifacts/RELEASE
python3 -m attestflow evidence verify attestflow-artifacts/RUN --check-source
python3 -m attestflow evidence maintain --retention-days 90 --redact --compact --apply
python3 -m attestflow dashboard export --out attestflow-dashboard --json
python3 -m attestflow resume
python3 -m attestflow session resume TASK-0001
python3 -m attestflow provider list
python3 -m attestflow ci providers
python3 -m attestflow ci status --task TASK-0001
python3 -m attestflow ci await --head-sha abc123
python3 -m attestflow ci logs --run-id 123456789
python3 -m attestflow ci artifacts --run-id 123456789 --download-dir attestflow-artifacts
python3 -m attestflow ci rerun --run-id 123456789 --failed
python3 -m attestflow ci dispatch --workflow ci.yml --ref feature/my-change --input task=TASK-0001
python3 -m attestflow pr ensure TASK-0001
python3 -m attestflow pr merge TASK-0001
python3 -m attestflow pr status TASK-0001
python3 -m attestflow release status
python3 -m attestflow release trust --out attestflow-release-trust --json
python3 -m attestflow secret-scan
```

接入后先让编程 Agent 审核 `harness.yml` 和项目命令，再生成 planner JSON 并导入任务。只有凭证、业务取舍和不可自动判断的外部决策需要人工确认。任务进入开发前必须满足 `ready` 门禁；完成前必须有当前 run 的 evidence。

任务顺序不是靠单个 JSON 文件的文件名隐式决定，而是由调度器聚合所有 task JSON 后按 `dependencies -> state/DoR -> locks/files.write -> priority -> id` 计算。`autopilot --dry-run --limit N` 会只读输出可执行批次和跳过原因，用来审计下一轮自动执行顺序；它不会启动 Agent，也不会推进任务状态。

`autopilot --run --limit N --max-steps M` 是当前的最小顶层执行入口。它会创建 `harness/autopilot-runs/<run>/metadata.json` 和 `ledger.jsonl`，按 dry-run 同一套规则优先批量推进已开始任务的下一步动作，再分发新的 ready 批次。默认 batch size 和 step budget 来自 `harness.yml` 的 `autopilot.default_limit` / `autopilot.max_steps`，CLI 参数只作为临时覆盖。传入 `--goal "..."` 时，autopilot 会先调用 planner capability，把目标生成并导入 runtime task JSON，然后在同一轮继续调度。当前 run 能自动完成“goal -> plan -> active action batch or dispatch -> ledger”：`in_progress` 会按 `bdd -> tdd -> implementer -> review` 推进，`review` 会按 `reviewer -> optional verifier -> verify` 推进，`verified` 会进入 `accepted`，`accepted` 会先把 worktree 变更合回控制仓库，再执行已配置的 `publish`，随后执行已配置的 `pr ensure`、采集 CI evidence；如果 `integrations.pr_provider.auto_merge: true` 且 PR request 为 `open`，会在 CI 通过后执行 `pr merge`，再采集最终 PR status evidence，最后尝试 `close`。active task actions 会按 `limit` 和 `files.write` 冲突组成批次，同一 top-level step 内推进多个互不冲突的任务。只有全部任务都进入 `done` 或 `archived` 后，如果配置了 release provider，autopilot 才会把已完成任务摘要和交付 evidence 汇总给 release provider，采集 release evidence 并写入顶层 metadata 的 `release` / `release_status`。`release_status` 只有 `released` 或 `skipped` 才算完成；`running`、`queued` 或 `unknown` 会暂停等待 `--resume` 重试；`failed` 且 planner capability 已配置时，会把 release failure summary、release evidence 和可选 release handoff summary 交给 planner 生成修复任务并导入 runtime task JSON。blocked run 会以非零退出码结束，`metadata.json` 会保留 goal/planner/planned/blocked/skipped/release/release_repair_planner 索引。没有可执行 batch 但仍存在未完成的 skipped task 时，run 也会记录为 `blocked`，避免把等待人工输入、无效依赖或非法任务误判成完成。`--max-steps` 到点且仍有 active task、ready batch 或 release gate 时，run 会写成 `status: paused` 与 `pause_reason: max_steps_reached`，表示可用 `--resume` 继续，而不是已经完成。`--loop` 会在同一个 top-level run 上受限地反复 resume paused run，直到 `finished`、`blocked`、`failed` 或 cycle 用完；默认 cycle 上限和等待时间来自 `harness.yml` 的 `autopilot.max_loop_cycles` / `autopilot.loop_interval_seconds`，也可以用 `--max-cycles N` / `--interval-seconds S` 临时覆盖。metadata 会记录 `resume_count`、`loop_cycles` 和 `loop_stop_reason`，其中 `resume_count` 是同一 run 被恢复的次数，`max_cycles_reached` 表示安全阈值触发且仍需后续 resume，`terminal_status` 表示已进入终态。在 capability 命令配置完整且验证门禁通过时，单个任务可以自动推进到 `done`。`autopilot --resume --max-steps M` 会复用最新 autopilot run 目录，追加 ledger 并累加 metadata，适合 `--max-steps` 到点或进程中断后继续同一轮自治执行。

`dispatch` 是 AI-first 执行入口。它会把 `ready` 任务移到 `in_progress`，创建 run、locks、独立 agent session、`prompt.md` 和 `session.yml`。`dispatch --limit N` 会按依赖、现有锁和同批次 `files.write` 冲突自动挑选可并行任务。如果 `harness.yml` 配置了 `sessions.launch_command`，Attestflow 会按 `docs/contracts/session-adapter-schema.md` 执行 command adapter 来启动真实外部 AI 会话；否则会生成可恢复的 session packet，等待接入层消费。

`sessions.launch_command` / `sessions.resume_command` 是编程 Agent 适配点。命令从 stdin 读取 JSON，向 stdout 返回 JSON；Attestflow 会保存 `session-adapter-input.json`、`session-adapter-output.json`、stdout/stderr logs，并用 `attestflow session resume TASK-*` 恢复对应会话。`sessions.provider_options.timeout_seconds` 会强制终止卡住的 launch/resume adapter，并把 session 标记为 `launch_failed` 或 `resume_failed`。

如果 `sessions.agent_provider` 设为 `codex`、`claude-code` 或 `opencode`，且没有显式配置 `launch_command`，Attestflow 会自动使用内置 provider preset。`provider_options.command`、`provider_options.launch_args`、`provider_options.resume_args`、`provider_options.doctor_args` 和 `provider_options.doctor_failure_patterns` 可以覆盖底层 CLI 命令、运行参数和 preflight 规则；离线环境可设 `provider_options.doctor_enabled: false` 跳过 provider preflight。

如果 `sessions.worktree.enabled: true`，每个 task run 会先创建独立 git worktree，默认路径为 `../.attestflow-worktrees/{project}/{task_id}-{run_id}`。session adapter、task-scoped capability 和 `verify --task` 都会在该 worktree 里执行；控制面证据仍写回主项目的 `harness/runs` 和 `harness/capability-runs`。`close` 会把 worktree 变更提交成 task commit，并用 `git merge --ff-only` 合回控制仓库；如果控制仓库已经漂移，close 会失败并保留任务在 `accepted`。run metadata 会记录 `commit_before`、`commit_after` 和是否已应用到控制仓库。

`integrations.git_provider`、`integrations.ci_provider`、`integrations.pr_provider` 和 `integrations.release_provider` 是外部交付证据适配点。`provider: command` 会调用任意输出统一 JSON 的命令；`timeout_seconds` 会强制终止卡住的 provider 并保留日志。Git `provider: git` 会使用内置 adapter 执行提交和推送；`attestflow publish --task TASK-*` 会把输出写入 `task.evidence.git`。CI `github-actions` preset 支持 `ci status`、`ci await`、`ci logs`、`ci artifacts`、`ci rerun` 和 `ci dispatch`：默认用 `gh run list` 读取状态，可按 branch、head SHA、workflow、event 精确筛选，失败时采集 failed jobs、annotations 和 failed log evidence。PR provider 支持 `pr ensure [TASK-*]` 创建/更新 change request、`pr merge [TASK-*]` 请求合并，以及 `pr status [TASK-*]` 查询合入状态；带 `TASK-*` 参数时会把输出写入对应 `task.evidence.pr_request` / `task.evidence.pr_merge` / `task.evidence.pr`。`ci <action> --task TASK-*` 会把 CI 输出写入 `task.evidence.ci`。配置 `capabilities.releaser` 时，autopilot 会在 release provider 前生成 release handoff evidence，并把它作为 `release_handoff` 传给 provider。CI/Release 返回 `running`、`queued` 或 `unknown` 时，autopilot 会记录 evidence 并暂停为 `pause_reason: external_status_pending`，下一次 `--resume` 重新采集。结果分别保存到 `harness/git-runs/git-*/`、`harness/ci-runs/ci-*/`、`harness/pr-runs/pr-*/` 和 `harness/release-runs/release-*/`。

## 当前能力

当前版本不依赖第三方 Python 包：

- 受限 YAML 子集读写
- `harness.yml` 校验
- `init --adapter <adapter> --agent-provider codex|claude-code|opencode` 写入项目 adapter 和内置 provider preset；内置 adapter 覆盖 generic、Python、Node、Go、Rust、monorepo、Docker、Bazel、Java、Kotlin、.NET、Swift、Dart、Ruby、PHP，并按项目文件生成基础验证命令；`doctor` 和 `autonomy doctor` 使用同一组 runtime 目录真相源检查任务、run、Git/CI/PR/release/plugin evidence 目录、任务 schema、provider CLI 和 provider preflight；`recover --apply` 可重建缺失 runtime 目录
- `contract validate` 校验 planner、capability、session、Git、CI、PR、release 和 runtime task contract，provider 作者可以直接定位输出字段错误
- provider output 可选记录真实模型 `usage`，成功 run 会保留 `usage.json` 或 session usage evidence；`usage report` 会聚合 capability、session、CI、Git、PR、release 和 plugin provider 的 token/cost 用量
- token economy 控制层：输入预算门、context cache、动态 context resolve、incremental context、evidence 摘要和可选 provider result cache，用于减少重复上下文和重复模型调用；capability output 可返回 `artifacts.context_requests[]`，Attestflow 会本地解析允许的局部 context 并自动重试一次
- 内置 capability registry：intake、planner、bdd、tdd、implementer、reviewer、verifier、releaser
- 内置 capability provider adapter：Codex、Claude Code、OpenCode preset 可直接驱动 `plan` 和 `capability run`
- `plan` programming agent provider：调用编程 Agent provider，保存 capability 输入/输出证据并导入 runtime task JSON
- `capability run` task programming agent provider：对单个任务执行 `bdd`、`tdd`、`implementer`、`reviewer` 或 `verifier`，校验 capability output schema，保存 evidence 并写回任务证据索引；`releaser` 由 top-level release gate 调用
- 自动仓库上下文：收集文件树、核心文档和任务 focus files，写入 capability provider input；超过 token budget 时自动把全文替换为摘要和 cache key，并允许 provider 后续按需请求局部 context；自动解析结果保存在 `dynamic-context.json`
- AI planner JSON 导入为 runtime task JSON
- 外部来源导入：GitHub issue、Linear/Jira ticket、PR review comment 和 CI failure 会保存 source evidence，并进入 `proposed` task 队列等待 intake/planner
- 治理和版本演进：`schema migrate/export/openapi`、provider `contract_version` 校验、`plugin list` 注册发现、`plugin run` 执行 manifest command、`policy list/validate/apply` 本地 policy pack、`governance policy` 发布和破坏性变更规则
- task schema 校验
- `next` 调度单个最高优先级任务
- `autopilot --dry-run` 生成只读执行计划，优先展示 active-task 下一步动作和 repair mode，否则按依赖、优先级、锁、写范围冲突、`max_test_cost` 和 `max_model_tokens` 展示 ready 批次与跳过原因
- `autopilot --run --goal` 调用 planner capability 生成并导入 runtime task JSON，然后进入同一轮自治执行
- `autopilot --run` 创建顶层 run ledger，先按 `limit` 批量推进 active-task capability/状态动作，失败时按 repair_attempts 限制回到 implementer 修复，accepted 任务会先 apply worktree，再执行已配置的 `publish` 和 `pr ensure`，随后采集 CI evidence；若 `integrations.pr_provider.auto_merge: true` 且 PR 可合并，会执行 `pr merge`，最后采集 PR status evidence；全部任务完成后才会采集 release evidence；agent session、Git、PR、CI 或 release 返回 blocked 时记录为 blocked run 并返回非零退出码；`max_steps` 到点但仍有后续工作时记录为 `paused`
- `autopilot --run/--resume --loop` 在同一个 run 上自动续跑 paused 状态，直到终态或 cycle 上限；默认 batch、step 和 loop policy 来自 `harness.yml`，CLI 参数可覆盖
- `autopilot --resume` 复用最新 autopilot run 的 `metadata.json` 和 `ledger.jsonl`，继续执行并追加事件
- `autopilot --status` 读取最新 `harness/autopilot-runs/*/metadata.json`，输出顶层 run 状态、暂停原因、planned tasks、releaser、release 和 blocked/failed 摘要，支持 `--json`
- `inspect --run RUN` 把 autopilot run 的 `metadata.json`、`ledger.jsonl`、blocked task 文件和 provider `failure.json` 汇总成 timeline、blocker dashboard、provider failure drilldown 和 next-action；`inspect --diff OLD NEW` 对比两个 run 的状态、actions、planned/dispatched 和 release 变化，支持 `--json`
- `recover` 检查缺失 runtime 目录、孤儿 autopilot run、task 文件状态错位、已 finalized 但未清理的 worktree、被取消的 provider session；默认只报告，`recover --apply` 会修复可确定修复的 runtime 状态并写入 `harness/snapshots/ledger-snapshot-*.json`；`--resume-interrupted` 会显式调用 session resume adapter 恢复中断 provider session
- `dispatch --limit N` 批量调度依赖已满足、写范围不冲突且未被锁定的 ready 任务；每个任务自动创建独立 agent session、prompt packet、锁和 run evidence，并可调用编程 Agent session adapter
- 可选 per-task git worktree 隔离：session adapter、capability 和 verify command 在任务 worktree 中执行，close 时用 ff-only merge 把 task commit 合回控制项目
- `session resume` 通过同一 session adapter 合同恢复外部编程 Agent 会话
- 内置 session provider preset：Codex、Claude Code、OpenCode
- Git provider contract：`publish` 提交并推送当前分支；`publish --task TASK-*` 同时写回 `task.evidence.git`；内置 `git` provider 会拒绝直接推送默认分支，除非显式允许
- CI provider contract：`ci status` / `ci await` / `ci logs` / `ci artifacts` / `ci rerun` / `ci dispatch` 保存外部 CI 状态、日志、产物和动作 evidence；`--task TASK-*` 同时写回 `task.evidence.ci`；内置 GitHub Actions preset 支持 PR/SHA 精确筛选、failed log 和 artifact evidence
- PR provider contract：`pr ensure` 创建或更新外部 PR/change request，`pr merge` 请求合并，`pr status` 保存外部 PR/change 状态 evidence；带 task id 时写回 `task.evidence.pr_request` / `task.evidence.pr_merge` / `task.evidence.pr`；可由 command provider 接任意代码托管系统
- Release provider contract：`release status` 接收已完成任务摘要和 PR/CI evidence，保存外部发布 evidence；可由 command provider 接任意发布系统
- `evidence export TASK-* --out DIR` 导出 task、run、ledger、capability output 和 manifest；`evidence bundle --run/--release` 导出顶层交付 evidence、release bundle、PR comment artifact、可复现 manifest 和 audit report；`evidence verify DIR --check-source` 校验 bundle hash/size 并检测源 evidence 是否过期；`evidence maintain` 可按 retention 做本地 GC、secret redaction 和大日志 compaction
- `dashboard export --out DIR` 生成零依赖本地 HTML dashboard 和 `data.json`，用于查看 task state、最新 run 和交付 evidence 状态
- `release trust --out DIR` 生成本地 release trust 包：`sbom.json`、`provenance.json`、`checklist.md`、`report.json` 和带 SHA-256 的 `manifest.json`，检查 pyproject、Python matrix、build、install-smoke 和 artifact upload
- `start` 低层状态推进入口，也会创建 session packet
- 结构化 blocker 协议：`blockers[]` 记录 reason、unblock condition、owner、source；`block` / `unblock` 推进阻塞生命周期
- session adapter 或 capability output 返回 `blocked` 时，自动把任务移入 `blocked` 并写入 active blocker
- `transition` 按状态机推进任务
- `verify --task` 执行配置命令，并把结果写入当前 run 的 metadata 和 ledger；`transition TASK verified/accepted` 会拒绝缺失或失败的验证证据
- `close` 校验当前 run 的 DoD evidence 后关闭 accepted 任务、释放锁、写关闭 ledger
- `resume` 基于未完成 run 的最新 ledger event 输出下一步动作；如果 active task lock 丢失，会提示修复状态或重新获取锁；如果存在多个 unfinished runs 或多个 active task locks，会拒绝含糊恢复并返回非零退出码
- `verify` 按 `harness.yml` 执行临时验证，不绑定任务
- 保守 secret scan
- 可安装包内置 base 模板和 planner 输出示例

后续重点是扩大真实项目 dogfood 面和长期 provider 兼容性样本。
