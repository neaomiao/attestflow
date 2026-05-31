# Provider Cookbook

Provider 是 Attestflow 和编程 Agent 之间的边界。Attestflow 负责输入、输出、状态、锁、验证和证据；provider 负责生成 planner JSON 或完成 task-scoped capability。

## 通用命令合同

Provider command 必须：

- 从 stdin 读取 JSON object。
- 向 stdout 输出 JSON object。
- 把调试信息写到 stderr。
- 不直接编辑 `harness/tasks/**/*.json`。
- 不绕过 `files.write` 边界。

Planner provider 输出见 `docs/contracts/planner-output-schema.md`。Reviewer capability 输出最小形态：

```json
{
  "schema_version": 1,
  "status": "passed",
  "summary": "Implemented and verified the scoped work.",
  "findings": [],
  "evidence": ["tests passed"]
}
```

`bdd`、`tdd`、`implementer` 和 `verifier` 还必须返回各自的 `artifacts` 结构，详见 `docs/contracts/capability-schema.md`。`status` 只能是 `passed`、`failed` 或 `blocked`。外部凭证、服务状态、业务决策缺失时返回 `blocked`，不要伪造成功。

所有 provider output 都可以带可选 `usage`，用于记录真实模型消耗。Attestflow 不估算 token；只有 provider 知道模型账单时才填写：

```json
{
  "usage": {
    "provider": "codex",
    "model": "gpt-5",
    "input_tokens": 1200,
    "output_tokens": 300,
    "total_tokens": 1500,
    "cached_input_tokens": 0,
    "reasoning_tokens": 0,
    "cost_usd": 0.0123
  }
}
```

`input_tokens`、`output_tokens`、`total_tokens`、`cached_input_tokens` 和 `reasoning_tokens` 必须是非负整数；`cost_usd` 必须是非负数字。成功 provider run 会把该对象另存为 `usage.json`；session launch/resume 会分别写 `session-launch-usage.json` / `session-resume-usage.json`。

Attestflow 使用 argv 模式执行 provider command，不通过 shell 展开管道、重定向或 `;`。stdout/stderr 会写入证据日志，并对常见 token、secret、password、API key 和 bearer token 做 redaction。失败会写入 `failure.json`，`type` 取值为 `auth_missing`、`rate_limited`、`context_too_large`、`invalid_output`、`tool_denied`、`approval_required`、`output_too_large`、`timeout`、`network` 或 `failed`，并附带 `automatic_action` 和 `recovery_strategy`。

安全边界配置：

```yaml
security:
  provider_commands:
    allowlist: ["python3", "gh", "glab"]
    max_output_bytes: 1048576
    require_approval_for_irreversible: true
  network:
    mode: provider-owned
  filesystem:
    mode: write-scope-validated
```

- `allowlist` 非空时，只允许列表里的可执行文件名或绝对路径。
- `max_output_bytes` 限制 provider stdout/stderr 总量；超限时失败并写截断日志。
- `irreversible: true` 的 provider action 必须带 `approval_id` 或 `approval_path`，并有 `approved: true` 的审批文件。
- Attestflow 不做网络 sandbox；网络策略由 provider CLI、系统代理、防火墙或 CI 运行环境负责。
- Run ledger 每行包含 `previous_hash` 和 `hash`，用于检查 evidence 链是否被事后改写。

本地调试 provider 输出时，不需要读 Attestflow 源码：

```bash
python -m attestflow contract validate planner-output planner-output.json
python -m attestflow contract validate capability-output output.json
python -m attestflow contract validate session-launch-output session-output.json
python -m attestflow contract validate git-output git-output.json
python -m attestflow contract validate ci-output ci-output.json
python -m attestflow contract validate pr-output pr-output.json
python -m attestflow contract validate release-output release-output.json
python -m attestflow provider smoke --provider codex
python -m attestflow provider smoke --provider claude-code
python -m attestflow provider smoke --provider opencode
python -m attestflow provider contract --provider codex
```

运行中的 provider 输出不符合 contract 时，错误会带下一条 `contract validate` 命令。

Planner provider 的非法 JSON 或不符合 `planner-output` contract 的输出会自动重试一次。每次尝试都会保留自己的 capability run 目录；最终成功的 run 会写 `retry.json`，其中包含失败尝试、是否可重试和 `failure_attribution.source=provider`。需要覆盖默认次数时：

```yaml
capabilities:
  planner:
    provider_options:
      retry_attempts: 3
```

`provider smoke` 是真实 provider readiness 检查，不等同于静态 preset：

- 先运行 provider 版本探测，例如 `codex --version`。
- 再通过内置 session adapter 运行一次 live smoke，并按 `auth_missing`、`rate_limited`、`context_too_large`、`tool_denied`、`timeout`、`network`、`invalid_output`、`failed` 分类。
- 默认再跑 capability contract suite；调试登录或网络问题时可临时加 `--skip-contract`。
- 每个失败都会给出自动动作和恢复策略，例如缺登录会阻塞等待凭证，超时会建议重试或缩小上下文。

示例：

```bash
python -m attestflow provider smoke --provider codex --json
python -m attestflow provider smoke --provider claude-code --timeout-seconds 60 --json
python -m attestflow provider smoke --provider opencode --skip-contract --json
```

Session launch/resume 还会在 adapter 执行前后做实际文件快照，校验新增、修改、删除、重命名和二进制改动是否都落在 task 的 `files.write` 内。报告写入 `session-launch-write-scope.json` 或 `session-resume-write-scope.json`；越权写入会把 session 标为 `launch_failed` 或 `resume_failed`。

## Delivery providers

CI、PR 和 release provider 使用同一条证据路径：Attestflow 写入 `input.json`、stdout/stderr、`output.json`，并用 `ci-output`、`pr-output` 或 `release-output` contract 校验结果。内置 adapter 只负责把常见工具的 JSON 输出映射成 Attestflow contract；凭证、网络和外部权限仍由对应 CLI 处理。

查看内置 provider：

```bash
python -m attestflow ci providers
python -m attestflow pr providers
python -m attestflow release providers
```

CI 内置 provider：

- `github-actions`
- `gitlab-ci`
- `buildkite`
- `circleci`

PR 内置 provider：

- `github`
- `gitlab`

Release / delivery 内置 provider：

- `github-release`
- `gitlab-release`
- `linear`
- `jira`
- `buildkite`
- `circleci`
- `self-hosted-release`

示例配置：

```yaml
integrations:
  git_provider:
    provider: git
    provider_options:
      remote: origin
      push: true
  ci_provider:
    provider: gitlab-ci
    provider_options:
      command: glab
      status_args: ["ci", "status", "--output", "json"]
  pr_provider:
    provider: github
    provider_options:
      command: gh
      status_args: ["pr", "view", "--json", "number,url,state,isDraft,headRefName,baseRefName"]
      ensure_args: ["pr", "create", "--json", "number,url,state,isDraft,headRefName,baseRefName"]
  release_provider:
    provider: self-hosted-release
    provider_options:
      command: ./tools/release-status
      release_args: ["status", "--json"]
```

端到端测试不依赖真实 SaaS 账号：测试用 fake CLI 模拟 GitHub、GitLab、Linear、Jira、Buildkite、CircleCI 和自建发布系统的 JSON 输出，再通过真实 provider command 执行、证据写入和 contract 校验路径验证映射。

## Local deterministic provider

`examples/providers/local_agent.py` 用于无账号 smoke test：

```yaml
capabilities:
  planner:
    agent_provider: command
    command: python ../providers/local_agent.py
  bdd:
    agent_provider: command
    command: python ../providers/local_agent.py
  tdd:
    agent_provider: command
    command: python ../providers/local_agent.py
  implementer:
    agent_provider: command
    command: python ../providers/local_agent.py
  reviewer:
    agent_provider: command
    command: python ../providers/local_agent.py
```

它会输出一个 greeting task，并在 capability 阶段写入示例测试和实现文件。它不是生产 provider，只用于验证开源核心闭环。

## Codex

初始化：

```bash
python -m attestflow init --path . --adapter python --agent-provider codex
python -m attestflow doctor
```

默认 preflight 是 `codex doctor --json`。如果 Codex 不在 `PATH`：

```bash
python -m attestflow init --path . --adapter python --agent-provider codex --agent-command /absolute/path/to/codex
```

如果需要覆盖启动参数：

```yaml
sessions:
  agent_provider: codex
  provider_options:
    command: /absolute/path/to/codex
    timeout_seconds: 600
capabilities:
  planner:
    agent_provider: codex
    command: null
    provider_options:
      timeout_seconds: 600
```

内置 adapter 会把 capability input 转成 prompt，并从 stdout 抽取 contract JSON。

## Claude Code

初始化：

```bash
python -m attestflow init --path . --adapter python --agent-provider claude-code
python -m attestflow doctor
```

默认 preflight 是 `claude auth status`。如果企业环境禁用 preflight：

```yaml
sessions:
  agent_provider: claude-code
  provider_options:
    doctor_enabled: false
```

## OpenCode

初始化：

```bash
python -m attestflow init --path . --adapter node --agent-provider opencode
python -m attestflow doctor
```

默认 preflight 是 `opencode providers list`，并拒绝输出中的 `0 credentials`。

## 自定义 provider

最小 Python provider：

```python
from __future__ import annotations

import json
import sys

payload = json.loads(sys.stdin.read())
capability = payload.get("capability", {}).get("name", "planner")

if capability == "planner":
    print(json.dumps({
        "schema_version": 1,
        "goal": payload["goal"],
        "tasks": [
            {
                "key": "docs_update",
                "title": "Update README",
                "priority": 10,
                "type": "docs",
                "purpose": "Document the requested behavior.",
                "scope": ["README update"],
                "out_of_scope": ["code changes"],
                "requirements": {"confirmed": ["Docs describe the behavior"], "unresolved": [], "assumptions": []},
                "bdd_scenarios": ["Reader can follow the documented command."],
                "unit_tests": ["python -m attestflow validate-config"],
                "acceptance": ["README contains the new command."],
                "dependencies": [],
                "files": {"read": ["README.md"], "write": ["README.md"]}
            }
        ]
    }))
else:
    print(json.dumps({
        "schema_version": 1,
        "status": "passed",
        "summary": f"{capability} completed.",
        "findings": [],
        "evidence": ["provider completed"]
    }))
```

配置：

```yaml
capabilities:
  planner:
    agent_provider: command
    command: python tools/my_provider.py
```
