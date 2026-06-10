# Getting Started

本指南验证开源核心：安装、初始化、planner 导入、capability 执行、验证、证据和关闭任务。

## 1. 本地示例

不需要模型账号，直接跑 deterministic local provider：

下面命令假设 `python` 指向 Python 3.11+。如果你的系统没有 `python` 命令，请先激活 venv，或把 `python` 替换成 `python3.11` / `python3.12` / `python3.13`。

```bash
cd examples/python-basic
PYTHONPATH=../.. python -m attestflow doctor
PYTHONPATH=../.. python -m attestflow autopilot --run --goal "Add greeting support" --loop --max-cycles 12 --max-steps 1
PYTHONPATH=../.. python -m attestflow tasks
PYTHONPATH=../.. python -m attestflow evidence TASK-0001
PYTHONPATH=../.. python -m attestflow evidence export TASK-0001 --out attestflow-artifacts/TASK-0001
PYTHONPATH=../.. python -m attestflow evidence bundle --run <autopilot-run-id> --out attestflow-artifacts/run
PYTHONPATH=../.. python -m attestflow evidence verify attestflow-artifacts/run
```

成功后会看到一个 `done` 任务，以及这些产物：

- `greeter.py`
- `tests/bdd/test_greeter_behavior.py`
- `tests/unit/test_greeter.py`
- `harness/runs/<run>/metadata.yml`
- `harness/runs/<run>/ledger.jsonl`
- `harness/capability-runs/*/output.json`

Node 示例在 `examples/node-basic`，需要本机安装 Node.js：

```bash
cd examples/node-basic
PYTHONPATH=../.. python -m attestflow doctor
PYTHONPATH=../.. python -m attestflow autopilot --run --goal "Add greeting support" --loop --max-cycles 12 --max-steps 1
```

## 2. 接入自己的仓库

从源码安装并初始化：

```bash
python -m pip install --user .
python -m attestflow install-smoke --offline
python -m attestflow init --path /path/to/project --adapter python --language zh-CN --agent-provider command
cd /path/to/project
python -m attestflow doctor
```

`install-smoke` 是安装层检查，不依赖模型账号。它会确认 Python 版本、CLI 是否在 `PATH`、包内模板、初始化流程和 `doctor` 都可用；源码仓库中可以运行 `python -m attestflow install-smoke --offline --check-template-mirror`，额外校验源码模板与打包模板一致。

如果想让接入流程一次性完成并在交互式终端里选择语言、adapter 和 agent provider，使用：

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/neaomiao/attestflow/main/scripts/bootstrap.sh)"
```

语言选择会写入 `harness.yml` 的 `project.language`，当前支持 `en` 和 `zh-CN`。

常用 adapter：

- `generic`：只写通用模板，适合先手动配置命令。
- `python`：读取 pytest、ruff、mypy 显式配置并填入 `harness.yml`。
- `node`：读取 package manager 和 package scripts。
- `go`：检测 `go.mod`，填入 `go test ./...`。
- `rust`：检测 `Cargo.toml`，填入 `cargo test` / `cargo check` / `cargo build`。
- `monorepo`：检测 `pnpm-workspace.yaml`、`turbo.json`、`nx.json`，把 package scripts 映射成 workspace 命令。
- `docker`：检测 `Dockerfile` 和 Compose 文件，启用 Docker 执行策略并填入 `docker build .`。
- `bazel`：检测 Bazel workspace，填入 `bazel test //...` / `bazel build //...`。
- `java` / `kotlin`：检测 Maven 或 Gradle，填入对应 test/build 命令。
- `dotnet` / `swift` / `dart` / `ruby` / `php`：检测各自标准项目文件并填入基础验证命令。

## 3. 最小闭环

`attestflow go` 可以接收内联文本、Markdown、TXT、DOCX 和可复制文本层 PDF。DOCX/PDF 解析需要安装 `attestflow[documents]`；扫描版 PDF/OCR 在 v1 不支持，请先转换成 Markdown、TXT、DOCX 或带可复制文本层的 PDF。

如果入口来自外部系统，先保存来源快照，再让 intake/planner 决定真正的任务边界：

```bash
python -m attestflow source import --kind github-issue --from-json issue.json
python -m attestflow source import --kind pr-review-comment --from-json review-comment.json
python -m attestflow source import --kind ci-failure --from-json ci-failure.json
```

导入后会生成 `harness/sources/.../source.json` 和 `harness/tasks/proposed/TASK-*.json`。`proposed` task 不是可执行开发任务；它保留外部来源、优先级和证据，后续仍走 `goal -> intake -> planner` 或配置好的 autopilot 流程。

配置 `capabilities.planner.command`、`capabilities.bdd.command`、`capabilities.tdd.command`、`capabilities.implementer.command` 和 `capabilities.reviewer.command` 后，运行：

```bash
python -m attestflow autopilot --run --goal "Implement the next small feature" --until terminal --max-steps 1
```

Attestflow 会：

1. 调用 planner provider，导入 runtime task JSON。
2. 分发 ready task，创建 run、prompt packet、session record 和 locks。
3. 按 `bdd -> tdd -> implementer -> reviewer -> verify -> close` 推进。
4. 保存 capability、verification、ledger 和 close evidence。

## 4. 出问题时看哪里

- `python -m attestflow doctor`：配置、目录、命令和 provider preflight。
- `python -m attestflow autopilot --status --json`：最新 top-level run 状态。
- `python -m attestflow inspect --run RUN`：把 run metadata、ledger、blocker 和 provider failure 汇总成 timeline、失败 drilldown 和下一步动作。
- `python -m attestflow inspect --diff OLD_RUN NEW_RUN`：对比两次 top-level run 的状态、actions、planned/dispatched 和 release 变化。
- `python -m attestflow recover`：诊断 orphan run、半写 task、stale worktree 和 provider 中断；加 `--apply` 才执行确定性修复并写 ledger snapshot，`--resume-interrupted` 会显式恢复被取消的 provider session。
- `python -m attestflow contract validate capability-output output.json`：本地校验 provider 输出。
- `python -m attestflow provider contract --provider codex`：用固定夹具检查 provider 是否能产出核心合同 JSON。
- `python -m attestflow source import --kind ci-failure --from-json failure.json`：把外部 issue、review comment 或 CI failure 转成带来源证据的 proposed task。
- `python -m attestflow evidence bundle --run RUN --out DIR`：导出顶层 autopilot bundle、release evidence、PR comment、manifest 和 audit report。
- `python -m attestflow evidence verify DIR --check-source`：校验 bundle hash/size，并检查源 evidence 是否已经变化。
- `python -m attestflow resume`：低层 task run 恢复提示。
- `harness/runs/*/ledger.jsonl`：append-only 审计日志。
- `harness/capability-runs/*/stderr.log`：provider 错误。

如果 run 是 `paused`，通常是 step/cycle 到达上限或外部 CI/PR/release 仍在进行，继续 `autopilot --resume`。如果 run 是 `blocked`，先解决 blocker，再 `unblock` 对应任务。
