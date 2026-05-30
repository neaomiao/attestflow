# Attestflow

Attestflow 的目标是把需求收敛、AI 任务拆解、BDD、单元测试、实现、验证、证据和任务状态推进固化为可执行流程。

字段名、状态名和命令名保持英文，便于代码和 CI 解析；说明文档使用中文。

核心原则：AI 能完成的工作不进入人工主路径。编程 Agent 负责拆解目标、生成任务草案、补充 BDD 和验收标准；Attestflow 负责确定性校验、分配任务 ID、落盘、锁、验证和证据。

## 一条命令接入新项目

发布到 GitHub 后，新项目可以用一条命令安装并初始化：

```bash
python3 -m pip install --user git+https://github.com/neaomiao/attestflow.git && python3 -m attestflow init --path . --agent-provider codex
```

如果已经在本地 clone 了本仓库：

```bash
python3 -m attestflow init --path /path/to/project --agent-provider codex
```

该命令会生成 `harness.yml`、任务状态目录、DoR/DoD、Agent 角色、GitHub Actions 模板和示例 `TASK-0001`。

`--agent-provider` 支持 `command`、`codex`、`claude-code`、`opencode`。如果本机 CLI 不在 `PATH`，可以用 `--agent-command /absolute/path/to/agent` 写入 provider preset。初始化后运行：

```bash
python3 -m attestflow doctor
```

`doctor` 会检查配置、runtime 目录、任务 schema、内置 provider CLI，以及 provider preflight。它不会执行项目任务；Codex 默认运行 `codex doctor --json`，Claude Code 默认运行 `claude auth status`，OpenCode 默认运行 `opencode providers list` 并拒绝 `0 credentials`。

## AI-first 任务生成

任务不应该靠人手写 YAML。最直接的主路径是让编程 Agent 输出 planner JSON，然后由 Attestflow 校验并落盘：

```bash
python3 -m attestflow task import --from-json plan.json
```

也可以从 stdin 接收编程 Agent 或自动化系统输出：

```bash
ai-planner "实现登录功能" | python3 -m attestflow task import --from-json -
```

`task import` 会分配 `TASK-*` ID、解析 planner 内部依赖、补齐默认字段、校验 ready 门禁，并写入 `harness/tasks/ready/*.json`。如果编程 Agent 输出缺少 scope、BDD、unit_tests、acceptance 或 files.write，导入会失败，不会写入半成品任务。

也可以使用内置 capability 入口，让 Attestflow 负责组装标准输入、调用配置的编程 Agent 命令、保存 capability evidence，并自动导入任务：

```bash
python3 -m attestflow plan "实现登录功能"
```

编程 Agent provider 可以是 Codex、Claude Code、OpenCode 或其他 agent CLI。`init --agent-provider codex|claude-code|opencode` 会把 capability provider 自动接到内置 adapter；显式 `--command` 或 `capabilities.<name>.command` 仍可覆盖。Provider 最终需要输出符合 `docs/contracts/planner-output-schema.md` 的 planner JSON。

Attestflow 的内置 capabilities 借鉴 Superpowers 的强制技能流程和 gstack 的专业角色分工，但不依赖它们。外部 skill、编程 Agent CLI 或 API wrapper 只是可选 agent provider；稳定接口是 Attestflow 自己的 capability contract。

Provider input 会自动带上受控仓库上下文：文件树、核心文档片段，以及任务 `files.read` / `files.write` 指向的文本片段。`harness/runs`、`harness/capability-runs`、`.git`、`node_modules` 等目录默认排除，避免把运行证据和噪音回灌给 Agent。

## 本地验证

```bash
python3 -m unittest discover -s tests
python3 -m attestflow verify
```

## 核心命令

```bash
python3 -m attestflow validate-config
python3 -m attestflow doctor
python3 -m attestflow validate-task harness/tasks/ready/TASK-0001-example.json
python3 -m attestflow capability list
python3 -m attestflow capability show planner
python3 -m attestflow plan "实现登录功能"
python3 -m attestflow capability run reviewer TASK-0001
python3 -m attestflow task import --from-json plan.json
python3 -m attestflow tasks
python3 -m attestflow next
python3 -m attestflow dispatch TASK-0001
python3 -m attestflow transition TASK-0001 review
python3 -m attestflow verify --task TASK-0001
python3 -m attestflow transition TASK-0001 verified
python3 -m attestflow transition TASK-0001 accepted
python3 -m attestflow close TASK-0001
python3 -m attestflow block TASK-0001 --reason "missing external input"
python3 -m attestflow unblock TASK-0001 --blocker BLK-0001 --resolution "input provided"
python3 -m attestflow evidence TASK-0001
python3 -m attestflow resume
python3 -m attestflow session resume TASK-0001
python3 -m attestflow provider list
python3 -m attestflow secret-scan
```

接入后先让编程 Agent 审核 `harness.yml` 和项目命令，再生成 planner JSON 并导入任务。只有凭证、业务取舍和不可自动判断的外部决策需要人工确认。任务进入开发前必须满足 `ready` 门禁；完成前必须有当前 run 的 evidence。

`dispatch` 是 AI-first 执行入口。它会把 `ready` 任务移到 `in_progress`，创建 run、locks、独立 agent session、`prompt.md` 和 `session.yml`。如果 `harness.yml` 配置了 `sessions.launch_command`，Attestflow 会按 `docs/contracts/session-adapter-schema.md` 执行 command adapter 来启动真实外部 AI 会话；否则会生成可恢复的 session packet，等待接入层消费。

`sessions.launch_command` / `sessions.resume_command` 是编程 Agent 适配点。命令从 stdin 读取 JSON，向 stdout 返回 JSON；Attestflow 会保存 `session-adapter-input.json`、`session-adapter-output.json`、stdout/stderr logs，并用 `attestflow session resume TASK-*` 恢复对应会话。

如果 `sessions.agent_provider` 设为 `codex`、`claude-code` 或 `opencode`，且没有显式配置 `launch_command`，Attestflow 会自动使用内置 provider preset。`provider_options.command`、`provider_options.launch_args`、`provider_options.resume_args`、`provider_options.doctor_args` 和 `provider_options.doctor_failure_patterns` 可以覆盖底层 CLI 命令、运行参数和 preflight 规则；离线环境可设 `provider_options.doctor_enabled: false` 跳过 provider preflight。

## 当前能力

当前版本不依赖第三方 Python 包：

- 受限 YAML 子集读写
- `harness.yml` 校验
- `init --agent-provider codex|claude-code|opencode` 写入内置 provider preset；`doctor` 检查配置、runtime 目录、任务 schema、provider CLI 和 provider preflight
- 内置 capability registry：intake、planner、bdd、tdd、implementer、reviewer、verifier、releaser
- 内置 capability provider adapter：Codex、Claude Code、OpenCode preset 可直接驱动 `plan` 和 `capability run`
- `plan` programming agent provider：调用编程 Agent provider，保存 capability 输入/输出证据并导入 runtime task JSON
- `capability run` task programming agent provider：对单个任务执行 `bdd`、`tdd`、`implementer`、`reviewer`、`verifier` 或 `releaser`，校验 capability output schema，保存 evidence 并写回任务证据索引
- 自动仓库上下文：收集文件树、核心文档和任务 focus files，写入 capability provider input
- AI planner JSON 导入为 runtime task JSON
- task schema 校验
- `next` 调度
- `dispatch` 自动创建每任务独立 agent session、prompt packet、锁和 run evidence，并可调用编程 Agent session adapter
- `session resume` 通过同一 session adapter 合同恢复外部编程 Agent 会话
- 内置 session provider preset：Codex、Claude Code、OpenCode
- `start` 低层状态推进入口，也会创建 session packet
- 结构化 blocker 协议：`blockers[]` 记录 reason、unblock condition、owner、source；`block` / `unblock` 推进阻塞生命周期
- session adapter 或 capability output 返回 `blocked` 时，自动把任务移入 `blocked` 并写入 active blocker
- `transition` 按状态机推进任务
- `verify --task` 执行配置命令，并把结果写入当前 run 的 metadata 和 ledger
- `close` 校验当前 run 的 DoD evidence 后关闭 accepted 任务、释放锁、写关闭 ledger
- `resume` 未完成 run 摘要
- `verify` 按 `harness.yml` 执行临时验证，不绑定任务
- 保守 secret scan
- 可安装包内置 base 模板和 planner 输出示例

后续重点是 CI provider 抽象、更完整的多 Agent 调度，以及可安装包的端到端接入体验。
