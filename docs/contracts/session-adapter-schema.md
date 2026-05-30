# Session Adapter Schema 契约

日期：2026-05-30
状态：已实现 command adapter

## 目标

Session adapter 是 Attestflow 和外部编程 Agent CLI 之间的边界。Codex、Claude Code、OpenCode 或其他 Agent 只通过这个边界接入；Attestflow core 不依赖任何外部 Agent SDK。

Attestflow 负责确定性工作：创建 run、锁、`prompt.md`、adapter input、adapter output、session record、metadata 和 ledger。编程 Agent adapter 只负责启动或恢复一个独立会话，并返回机器可校验的 JSON。

## 配置

```yaml
sessions:
  agent_provider: codex
  role: worker_agent
  launch_command: null
  resume_command: null
  provider_options: {}
```

`launch_command` 和 `resume_command` 从 stdin 读取 JSON object，向 stdout 输出 JSON object。stderr 会写入 evidence log。

如果 `agent_provider` 是内置 preset，`launch_command` / `resume_command` 可以保持 `null`：

| agent_provider | 默认 CLI | 默认 launch |
| --- | --- | --- |
| `codex` | `codex` | `codex exec --json --sandbox workspace-write` |
| `claude-code` | `claude` | `claude -p --output-format json` |
| `opencode` | `opencode` | `opencode run --format json` |

初始化新项目时可以直接写入 preset：

```bash
python -m attestflow init --agent-provider codex
python -m attestflow init --agent-provider claude-code
python -m attestflow init --agent-provider opencode
```

可用 `provider_options` 覆盖底层 CLI：

```yaml
sessions:
  agent_provider: codex
  provider_options:
    command: /opt/bin/codex
    launch_args:
      - exec
      - --json
      - --sandbox
      - workspace-write
    resume_args:
      - exec
      - resume
      - "{external_session_id}"
      - --json
    doctor_args:
      - doctor
      - --json
    doctor_timeout_seconds: 20
    doctor_failure_patterns: []
```

`doctor` 会在检查命令存在后执行 provider preflight。内置默认值是 `codex doctor --json`、`claude auth status` 和 `opencode providers list`；OpenCode 默认把 `0 credentials` 视为未就绪。项目可以用 `provider_options.doctor_args`、`doctor_timeout_seconds` 和 `doctor_failure_patterns` 覆盖；离线或受限环境可以设 `provider_options.doctor_enabled: false` 跳过 preflight。

## Adapter Input

```json
{
  "schema_version": 1,
  "action": "launch",
  "agent_provider": "codex",
  "root": "/absolute/project",
  "session": {
    "session_id": "session-2026-05-30T00-00-00Z-TASK-0001",
    "task_id": "TASK-0001",
    "run_id": "2026-05-30T00-00-00Z-TASK-0001",
    "role": "worker_agent",
    "status": "prepared",
    "external_session_id": null
  },
  "run": {
    "run_id": "2026-05-30T00-00-00Z-TASK-0001",
    "path": "/absolute/project/harness/runs/2026-05-30T00-00-00Z-TASK-0001"
  },
  "task": {"id": "TASK-0001"},
  "provider_options": {},
  "prompt_packet": {
    "path": "prompt.md",
    "absolute_path": "/absolute/project/harness/runs/2026-05-30T00-00-00Z-TASK-0001/prompt.md",
    "content": "# Attestflow Agent Session Packet\n..."
  },
  "commands": {},
  "instructions": [
    "Launch or resume one independent programming agent session for this task only.",
    "Return only JSON that follows docs/contracts/session-adapter-schema.md.",
    "Do not edit runtime task JSON directly; Attestflow records session evidence."
  ]
}
```

`action` 为 `launch` 或 `resume`。恢复时 `session.external_session_id` 会带上上一次 adapter 返回的外部会话 id。

## Adapter Output

Launch output：

```json
{
  "schema_version": 1,
  "status": "launched",
  "external_session_id": "codex-session-123",
  "resume_command": "codex resume codex-session-123",
  "summary": "Started a Codex task session."
}
```

Resume output：

```json
{
  "schema_version": 1,
  "status": "resumed",
  "external_session_id": "codex-session-123",
  "resume_command": "codex resume codex-session-123",
  "summary": "Resumed the Codex task session."
}
```

字段规则：

- `schema_version` 必须为 `1`。
- launch `status` 只能是 `launched` 或 `blocked`。
- resume `status` 只能是 `resumed` 或 `blocked`。
- `summary` 必须非空。
- `external_session_id` 是外部 Agent 会话 id；没有外部 id 时可省略。
- `resume_command` 可由 adapter 返回，用于后续 `attestflow session resume TASK-*`。

内置 preset 会尽量从外部 CLI 输出中提取 `thread_id`、`session_id`、`sessionID`、`sessionId` 或 `conversation_id` 作为 `external_session_id`。如果外部 CLI 不返回稳定 id，resume 会退回该 CLI 的“继续最近会话”能力；这仍然会留下 Attestflow 的本地 run/session evidence，但项目可以用 `provider_options.resume_args` 收紧行为。

## Evidence

Launch 会写入：

- `session-adapter-input.json`
- `session-adapter-output.json`
- `session-launch.stdout.log`
- `session-launch.stderr.log`
- `session.yml`
- `metadata.yml`
- `ledger.jsonl`

Resume 会写入：

- `session-resume-adapter-input.json`
- `session-resume-adapter-output.json`
- `session-resume.stdout.log`
- `session-resume.stderr.log`
- `session.yml`
- `metadata.yml`
- `ledger.jsonl`

非零退出码、stdout 不是 JSON object、schema 不合法都会把 session 标记为 `launch_failed` 或 `resume_failed`，并保留失败原因。Attestflow 不把失败静默吞掉，也不要求人工补写任务文档。
