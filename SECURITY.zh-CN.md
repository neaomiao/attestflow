# 安全策略

Attestflow 协调 AI 编码 agent，因此安全问题通常集中在执行边界、证据泄漏或 provider 行为。

## 支持版本

在 `1.0` 前，只支持当前 `main` 分支。

## 报告漏洞

如果问题可能暴露密钥、绕过文件所有权、伪造证据或意外执行命令，请打开私密报告或直接联系维护者。

请包含：

- Attestflow 版本或 commit。
- 最小复现步骤。
- 是否涉及 provider command、session adapter、CI provider、PR provider 或 release provider。
- 已移除密钥的相关 `harness/*/metadata.yml` 或 `ledger.jsonl` 片段。

## 安全边界

- Provider stdout/stderr 会作为证据保存，不应包含密钥。
- Provider command 使用调用者权限运行。
- Attestflow 校验 contract 并记录证据，但不为任意 provider command 提供 OS 级 sandbox。
- `security.provider_commands.allowlist` 可限制 provider command 能启动的可执行文件；只有本地 operator 信任明确时才应留空。
- `security.provider_commands.max_output_bytes` 会限制 stdout/stderr 总量，超限时 fail closed 并写入截断证据。
- `security.provider_commands.sandbox.mode: restricted-env` 使用最小环境和显式 `allowed_env` 运行 provider，并移除 `blocked_env` / `blocked_env_prefixes`。
- `security.provider_commands.sandbox.network: disabled` 会记录网络意图、设置 `ATTESTFLOW_NETWORK=disabled` 并移除代理环境变量；它是本地策略信号，不是防火墙。
- `irreversible: true` 的 provider option 需要 approval evidence。
- 网络访问由 provider 拥有：请在 Attestflow 外配置 CLI、凭证、代理和网络策略，并用 `provider smoke` 验证。
- 文件写入在 Attestflow 边界校验：task capability 使用 write-scope check，session launch/resume 会记录 `session-*-write-scope.json`。
- Runtime ledger 使用 `previous_hash` 和 `hash` 串联，便于发现事后篡改。
- `evidence maintain --redact --compact --retention-days N` 可做本地 redaction、日志压缩和旧 run 清理；审计影响时先不加 `--apply`。
- 关闭任务和发布 evidence 前运行 `secret-scan`。
