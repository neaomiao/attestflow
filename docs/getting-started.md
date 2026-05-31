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
python -m attestflow init --path /path/to/project --adapter python --agent-provider command
cd /path/to/project
python -m attestflow doctor
```

常用 adapter：

- `generic`：只写通用模板，适合先手动配置命令。
- `python`：读取 pytest、ruff、mypy 显式配置并填入 `harness.yml`。
- `node`：读取 package manager 和 package scripts。
- `go`：检测 `go.mod`，填入 `go test ./...`。
- `rust`：检测 `Cargo.toml`，填入 `cargo test` / `cargo check` / `cargo build`。

## 3. 最小闭环

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
- `python -m attestflow contract validate capability-output output.json`：本地校验 provider 输出。
- `python -m attestflow provider contract --provider codex`：用固定夹具检查 provider 是否能产出核心合同 JSON。
- `python -m attestflow resume`：低层 task run 恢复提示。
- `harness/runs/*/ledger.jsonl`：append-only 审计日志。
- `harness/capability-runs/*/stderr.log`：provider 错误。

如果 run 是 `paused`，通常是 step/cycle 到达上限或外部 CI/PR/release 仍在进行，继续 `autopilot --resume`。如果 run 是 `blocked`，先解决 blocker，再 `unblock` 对应任务。
