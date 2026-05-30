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
  launch_command: python3 scripts/attestflow-codex-session.py launch
  resume_command: python3 scripts/attestflow-codex-session.py resume
```

`launch_command` 和 `resume_command` 从 stdin 读取 JSON object，向 stdout 输出 JSON object。stderr 会写入 evidence log。

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
