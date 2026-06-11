# Agent Blackboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement an append-only agent blackboard contract with shared library APIs, CLI commands, config defaults, tests, and product documentation.

**Architecture:** Add a focused `attestflow/blackboard.py` module that owns event validation, lock-serialized append, derived message views, and task/evidence validation. Wire CLI `blackboard post/list/show/resolve` through this module and keep all communication as auditable runtime state under `harness/blackboard/messages.jsonl`. Documentation describes this as indirect Agent coordination, not a replacement for task state, locks, approved specs, or verification gates.

**Tech Stack:** Python standard library only, existing `unittest` test suite, existing config/CLI/io/task patterns.

---

### Task 1: Config Default

**Files:**
- Modify: `attestflow/config.py`
- Modify: `templates/base/harness.yml`
- Modify: `attestflow/templates/base/harness.yml`
- Test: `tests/unit/test_config_and_io.py`

- [ ] **Step 1: Write failing config test**

Add an assertion to the existing config default path test:

```python
self.assertEqual(config["paths"]["blackboard"], "harness/blackboard")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.unit.test_config_and_io`

Expected: failure because `paths.blackboard` is missing.

- [ ] **Step 3: Add config defaults**

Add `blackboard: "harness/blackboard"` to `DEFAULT_CONFIG["paths"]` and both base harness templates.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.unit.test_config_and_io`

Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add attestflow/config.py templates/base/harness.yml attestflow/templates/base/harness.yml tests/unit/test_config_and_io.py
git commit -m "Add blackboard path config"
```

### Task 2: Blackboard Library

**Files:**
- Create: `attestflow/blackboard.py`
- Test: `tests/unit/test_blackboard.py`

- [ ] **Step 1: Write failing post/list tests**

Create `tests/unit/test_blackboard.py` with tests for:

```python
message = post_blackboard_message(root, config, from_role="reviewer", to_role="implementer", message_type="finding", body="Missing lockout criterion.")
self.assertEqual(message.message_id, "MSG-0001")
self.assertEqual(message.thread_id, "THREAD-0001")
self.assertEqual(message.status, "open")
self.assertEqual(len(list_blackboard_messages(root, config)), 1)
```

Also assert `harness/blackboard/messages.jsonl` contains one event with `event_id == "EVT-0001"` and `event_type == "post"`.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.unit.test_blackboard`

Expected: import failure for missing `attestflow.blackboard`.

- [ ] **Step 3: Implement minimal library**

Create:

```python
@dataclass(frozen=True)
class BlackboardMessage:
    message_id: str
    thread_id: str
    task_id: str | None
    run_id: str | None
    from_role: str
    to_role: str | None
    message_type: str
    body: str
    requires_response: bool
    status: str
    created_at: str
    updated_at: str
    evidence_refs: list[str]
    events: list[dict[str, Any]] | None = None
```

Implement `post_blackboard_message`, `list_blackboard_messages`, `show_blackboard_message`, and `resolve_blackboard_message`. Use `fcntl.flock` on POSIX and best-effort append locking; keep the implementation standard-library-only.

- [ ] **Step 4: Add validation tests**

Cover invalid `message_type`, empty `from_role`, empty `body`, unknown `reply_to`, unknown `task_id`, absolute evidence ref, escaping evidence ref, missing evidence ref, double resolve, and malformed JSONL fail-closed read.

- [ ] **Step 5: Run library tests**

Run: `python3 -m unittest tests.unit.test_blackboard`

Expected: OK.

- [ ] **Step 6: Commit**

```bash
git add attestflow/blackboard.py tests/unit/test_blackboard.py
git commit -m "Add blackboard runtime library"
```

### Task 3: CLI Commands

**Files:**
- Modify: `attestflow/cli.py`
- Test: `tests/unit/test_blackboard_cli.py`

- [ ] **Step 1: Write failing CLI tests**

Create CLI tests for:

```python
cli.main(["blackboard", "post", "--from-role", "reviewer", "--to-role", "implementer", "--type", "finding", "--body", "Missing criterion."])
cli.main(["blackboard", "list", "--json"])
cli.main(["blackboard", "show", "MSG-0001", "--json"])
cli.main(["blackboard", "resolve", "MSG-0001", "--from-role", "implementer", "--body", "Resolved."])
```

Also assert invalid CLI calls return non-zero and print `ERROR:` without traceback.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.unit.test_blackboard_cli`

Expected: parser rejects unknown `blackboard` command.

- [ ] **Step 3: Implement CLI**

Add `cmd_blackboard_post`, `cmd_blackboard_list`, `cmd_blackboard_show`, and `cmd_blackboard_resolve`. Add `blackboard` subcommands to `build_parser()`. Human output:

- post: `posted MSG-0001 in THREAD-0001`
- list: one line per message: `MSG-0001 open finding reviewer -> implementer`
- show: compact details unless `--json`
- resolve: `resolved MSG-0001`

- [ ] **Step 4: Run CLI tests**

Run: `python3 -m unittest tests.unit.test_blackboard_cli`

Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add attestflow/cli.py tests/unit/test_blackboard_cli.py
git commit -m "Add blackboard CLI"
```

### Task 4: Documentation and Verification

**Files:**
- Create: `docs/contracts/blackboard-schema.md`
- Create: `docs/contracts/blackboard-schema.en.md`
- Modify: `tests/unit/test_bilingual_docs.py`
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `docs/design/universal-harness.md`
- Modify: `docs/design/universal-harness.en.md`

- [ ] **Step 1: Add contract docs**

Document the append-only event contract, derived message rules, CLI commands, and non-bypass guarantee in English and Chinese.

- [ ] **Step 2: Update bilingual inventory**

Add `docs/contracts/blackboard-schema.en.md` / `docs/contracts/blackboard-schema.md` to `EXPECTED_DOC_PAIRS`.

- [ ] **Step 3: Update product docs**

README and universal harness docs should state that blackboard is indirect, auditable Agent coordination and cannot change task state by itself.

- [ ] **Step 4: Run final verification**

Run:

```bash
python3 -m unittest tests.unit.test_blackboard tests.unit.test_blackboard_cli tests.unit.test_config_and_io tests.unit.test_bilingual_docs
python3 -m unittest discover -s tests/unit
python3 -m attestflow verify
git diff --check
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add docs/contracts/blackboard-schema.md docs/contracts/blackboard-schema.en.md tests/unit/test_bilingual_docs.py README.md README.zh-CN.md docs/design/universal-harness.md docs/design/universal-harness.en.md
git commit -m "Document blackboard contract"
```

## Self-Review

- Spec coverage: storage, schema, derived state, locking, library API, CLI, validation, docs, and tests are covered.
- Placeholder scan: clean; no unresolved marker words.
- Scope: v1 remains local append-only blackboard only; no realtime bus or automatic provider writes.
