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

Attestflow 使用 argv 模式执行 provider command，不通过 shell 展开管道、重定向或 `;`。stdout/stderr 会写入证据日志，并对常见 token、secret、password、API key 和 bearer token 做 redaction。失败会写入 `failure.json`，`type` 取值为 `auth_missing`、`rate_limited`、`context_too_large`、`invalid_output`、`tool_denied`、`timeout`、`network` 或 `failed`，并附带 `automatic_action`。

本地调试 provider 输出时，不需要读 Attestflow 源码：

```bash
python -m attestflow contract validate planner-output planner-output.json
python -m attestflow contract validate capability-output output.json
python -m attestflow contract validate session-launch-output session-output.json
python -m attestflow contract validate ci-output ci-output.json
python -m attestflow contract validate pr-output pr-output.json
python -m attestflow contract validate release-output release-output.json
python -m attestflow provider contract --provider codex
```

运行中的 provider 输出不符合 contract 时，错误会带下一条 `contract validate` 命令。

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
