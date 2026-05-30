# Attestflow

Attestflow 的目标是把需求收敛、AI 任务拆解、BDD、单元测试、实现、验证、证据和任务状态推进固化为可执行流程。

字段名、状态名和命令名保持英文，便于代码和 CI 解析；说明文档使用中文。

核心原则：AI 能完成的工作不进入人工主路径。大模型负责拆解目标、生成任务草案、补充 BDD 和验收标准；Attestflow 负责确定性校验、分配任务 ID、落盘、锁、验证和证据。

## 一条命令接入新项目

发布到 GitHub 后，新项目可以用一条命令安装并初始化：

```bash
python3 -m pip install --user git+https://github.com/neaomiao/attestflow.git && python3 -m attestflow init --path . --adapter generic
```

如果已经在本地 clone 了本仓库：

```bash
python3 -m attestflow init --path /path/to/project --adapter generic
```

该命令会生成 `harness.yml`、任务状态目录、DoR/DoD、Agent 角色、GitHub Actions 模板和示例 `TASK-0001`。

## AI-first 任务生成

任务不应该靠人手写 YAML。推荐主路径是让大模型输出 planner JSON，然后由 Attestflow 校验并落盘：

```bash
python3 -m attestflow task import --from-json plan.json
```

也可以从 stdin 接收模型或自动化系统输出：

```bash
ai-planner "实现登录功能" | python3 -m attestflow task import --from-json -
```

`task import` 会分配 `TASK-*` ID、解析 planner 内部依赖、补齐默认字段、校验 ready 门禁，并写入 `harness/tasks/ready/*.json`。如果模型输出缺少 scope、BDD、unit_tests、acceptance 或 files.write，导入会失败，不会写入半成品任务。

## 本地验证

```bash
python3 -m unittest discover -s tests
python3 -m attestflow verify
```

## 核心命令

```bash
python3 -m attestflow validate-config
python3 -m attestflow validate-task harness/tasks/ready/TASK-0001-example.json
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
python3 -m attestflow evidence TASK-0001
python3 -m attestflow resume
python3 -m attestflow secret-scan
```

接入后先让 Agent 审核 `harness.yml` 和项目命令，再由大模型生成 planner JSON 并导入任务。只有凭证、业务取舍和不可自动判断的外部决策需要人工确认。任务进入开发前必须满足 `ready` 门禁；完成前必须有当前 run 的 evidence。

`dispatch` 是 AI-first 执行入口。它会把 `ready` 任务移到 `in_progress`，创建 run、locks、独立 agent session、`prompt.md` 和 `session.yml`。如果 `harness.yml` 配置了 `sessions.launch_command`，Attestflow 会自动执行该命令来启动真实外部 AI 会话；否则会生成可恢复的 session packet，等待接入层消费。

`sessions.launch_command` 是 provider 适配点，支持 `{session_id}`、`{run_id}`、`{run_path}`、`{prompt_packet}`、`{session_log}`、`{root}` 占位符。

## 当前能力

当前版本不依赖第三方 Python 包：

- 受限 YAML 子集读写
- `harness.yml` 校验
- AI planner JSON 导入为 runtime task JSON
- task schema 校验
- `next` 调度
- `dispatch` 自动创建每任务独立 agent session、prompt packet、锁和 run evidence
- `start` 低层状态推进入口，也会创建 session packet
- `block` 阻塞任务
- `transition` 按状态机推进任务
- `verify --task` 执行配置命令，并把结果写入当前 run 的 metadata 和 ledger
- `close` 校验当前 run 的 DoD evidence 后关闭 accepted 任务、释放锁、写关闭 ledger
- `resume` 未完成 run 摘要
- `verify` 按 `harness.yml` 执行临时验证，不绑定任务
- 保守 secret scan
- 可安装包内置 base 模板和 planner 输出示例

后续重点是模型 provider 适配、CI provider 抽象和更完整的多 Agent 调度。
