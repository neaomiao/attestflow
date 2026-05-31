# Autonomy Contract

Attestflow 的自治边界是确定性的：系统自动推进可证明的工程步骤；缺少凭证、授权、外部状态或业务判断时写入结构化 blocker，不把等待伪装成完成。

## Run 状态

- `in_progress`：当前 run 正在推进确定性动作。
- `paused`：外部状态未收敛，或达到本轮 step/cycle 安全上限；允许 `autopilot --resume` 或 `autopilot --until terminal` 继续。
- `blocked`：缺少凭证、权限、生产审批、支付、业务取舍或外部系统给出 blocked；必须由人或外部系统满足 unblock condition。
- `failed`：contract、provider、验证或 repair 超过自动恢复能力；需要修复 harness/provider/代码后重新运行。
- `finished`：所有 task 达到 terminal，release gate 若配置也已 `released` 或 `skipped`。

## 自动继续

必须自动继续的情况：

- 存在可执行 active action：`bdd`、`tdd`、`implementer`、`reviewer`、`verifier`、本地 `verify`、PR/CI/release 状态采集。
- 上一轮 `paused` 且原因是 `max_steps_reached`，并且还有确定性下一步。
- CI、PR、release 返回 `running`、`queued` 或 `unknown` 后，下一次 resume 重新采集。
- stale lock、缺失 task run、缺失 worktree 能安全恢复时，先恢复再继续。

## 必须 blocked

以下情况必须进入 `blocked`，不能自动绕过：

- 缺少 provider 登录、API key、CLI 凭证或 workspace 权限。
- 生产发布审批、支付、人工合规确认、业务需求取舍未完成。
- PR/release provider 返回 `blocked`，或 PR 仍是 `open` / `draft` 且策略要求等待外部合并。
- task 自身包含 unresolved external inputs。

## 必须 failed

以下情况必须进入 `failed`：

- provider 输出不是合同 JSON，或合同校验失败。
- capability 产物和 `files.write` 不一致，或 git 检测到实际写入越界。
- 本地 verify/CI/PR/release 明确失败且 repair 次数超过 `autopilot.max_repair_attempts`。
- planner/releaser/release repair planner 不可用，且没有安全的自动下一步。

## 完整自治运行验收

一次完整自治运行必须能形成这条证据链：

```text
goal -> plan -> task -> BDD -> tests -> implementation -> review -> verify -> PR/CI -> release -> done
```

每个阶段都必须保存输入、输出、日志或 metadata 引用；`metadata.json` 是状态索引，`ledger.jsonl` 是 append-only 审计日志。`autopilot --run --goal ... --until terminal` 是安全入口：它会在同一个 run 上自动 resume，直到 `finished`、`blocked` 或 `failed`。

## Doctor

`python -m attestflow autonomy doctor` 检查配置、runtime 目录、task schema、provider command、git repository、git remote、workspace clean、CI/PR/release provider、测试命令和权限边界。doctor 只诊断，不执行项目任务。
