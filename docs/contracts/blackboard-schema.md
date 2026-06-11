# Agent Blackboard Contract

日期：2026-06-11
状态：runtime store 和 CLI 已实现

Agent blackboard 是多 Agent 协作的稳定协议，也是具体的 `agent_messages` contract：Agent 不通过改彼此文件或私有状态来通信，而是把有作用域的消息追加到 blackboard，由 Attestflow 从事件日志推导当前消息状态。

字段名、状态名、事件名和命令名保持英文，便于代码、CI 和 provider adapter 解析。

## 位置

Blackboard 根目录由 `paths.blackboard` 配置，默认是：

```text
harness/blackboard/
```

运行时文件：

```text
harness/blackboard/messages.jsonl
harness/blackboard/blackboard.lock
```

`messages.jsonl` 只追加。写入方必须在持有 `blackboard.lock` 时读取当前事件日志、分配 ID 并追加新事件。

## 事件日志

`messages.jsonl` 的每一行都是一个 `schema_version: 1` 的 JSON object。

所有事件的必需字段：

- `event_id`：`EVT-0001`，从当前日志单调分配
- `event_type`：`post`、`resolve` 或 `supersede`
- `message_id`：`MSG-0001`
- `thread_id`：`THREAD-0001`
- `task_id`：可选 `TASK-*`
- `run_id`：可选 task run id
- `from_role`：非空协议角色
- `to_role`：可选协议角色
- `message_type`：`question`、`answer`、`finding`、`decision`、`handoff`、`blocker`、`status` 或 `note`
- `body`：非空可读内容
- `requires_response`：boolean
- `status`：`open`、`resolved` 或 `superseded`
- `reply_to`：可选 `MSG-*`
- `evidence_refs`：指向已有项目文件的相对路径
- `created_at`：UTC 时间戳

## 事件语义

`post` 创建新消息，必须使用 `status: open`。

`resolve` 给已有 open 消息追加终态事件，必须使用 `status: resolved`。

`supersede` 给已有 open 消息追加终态事件，必须使用 `status: superseded`。它已进入 contract；第一版 runtime CLI 暴露 `post`、`list`、`show` 和 `resolve`。

读取 blackboard 时按文件顺序回放事件。终态消息不能再次接收终态事件。JSONL 损坏、重复 message id、引用不存在的消息、非法 event type 或非法 status 都 fail closed。

## 作用域规则

`task_id` 对项目级消息是可选的；一旦提供，必须引用已有 runtime task。

`reply_to` 必须引用已有消息。如果 post 未显式提供 `thread_id` 且设置了 `reply_to`，它继承被回复消息的 `thread_id`；否则分配新的 thread id。

`evidence_refs` 必须是相对路径、不能逃出项目根目录，并且引用文件必须已经存在。这样 blackboard claim 会绑定到可审计 artifact，而不是未跟踪的旁路信息。

## Library API

运行时 API：

- `post_blackboard_message(...)`
- `list_blackboard_messages(...)`
- `show_blackboard_message(...)`
- `resolve_blackboard_message(...)`

API 返回推导后的 `BlackboardMessage` record。`show_blackboard_message(..., include_events=True)` 会包含底层事件历史。

## CLI

```bash
python -m attestflow blackboard post --from-role reviewer --to-role implementer --type finding --body "Missing retry boundary." --requires-response
python -m attestflow blackboard list --status open --json
python -m attestflow blackboard show MSG-0001 --events --json
python -m attestflow blackboard resolve MSG-0001 --from-role implementer --body "Added retry boundary."
```

输入非法或 blackboard 状态损坏时，CLI 返回非零并输出 `ERROR:`。

## 控制面边界

Blackboard 不是 task state、locks、run ledger 或 evidence 的替代品。它只是 Agent 之间的协作层。Orchestrator 仍然拥有任务状态流转、锁校验、最终集成和验证。
