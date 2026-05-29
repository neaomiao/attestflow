# Universal Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reusable, standard-library-only harness MVP that validates task/config contracts, selects and starts ready tasks, writes run evidence, resumes interrupted runs, and exposes templates for adoption.

**Architecture:** Keep protocol logic in small `attestflow` modules. `io.py` handles the limited YAML subset used by task/config files. `tasks.py`, `locks.py`, `evidence.py`, and `resume.py` implement the state workflow, while `cli.py` exposes stable commands. Templates live under `templates/base` and tests use `unittest` so the repository runs without third-party dependencies.

**Tech Stack:** Python 3.11+ standard library, `unittest`, YAML-compatible subset reader/writer, Markdown docs.

---

## File Structure

- Create `pyproject.toml`: package metadata and console script.
- Create `README.md`: adoption and local verification commands.
- Create `attestflow/__init__.py`: version export.
- Create `attestflow/__main__.py`: `python -m attestflow` entrypoint.
- Create `attestflow/io.py`: limited YAML subset parser/writer.
- Create `attestflow/config.py`: config loading and validation.
- Create `attestflow/tasks.py`: task schema, state machine, validation, scheduling, start.
- Create `attestflow/locks.py`: task/file lock helpers.
- Create `attestflow/evidence.py`: run directory, metadata, ledger, evidence packet.
- Create `attestflow/runner.py`: command execution with logs.
- Create `attestflow/resume.py`: unfinished run discovery and next action summary.
- Create `attestflow/secrets.py`: conservative secret scan.
- Create `attestflow/cli.py`: command parser and exit codes.
- Create `templates/base/harness.yml`: generic adapter config.
- Create `templates/base/harness/tasks/ready/TASK-0001-example.yml`: sample ready task.
- Create `templates/base/harness/gates/definition_of_ready.yml`: default DoR.
- Create `templates/base/harness/gates/definition_of_done.yml`: default DoD.
- Create `templates/base/harness/agents/roles.yml`: default roles.
- Create `templates/base/.github/workflows/ci.yml`: generic CI template.
- Create `tests/unit/test_config_and_io.py`: YAML/config tests.
- Create `tests/unit/test_task_lifecycle.py`: task validation, next, start, resume tests.
- Create `tests/unit/test_secret_scan.py`: secret scanner tests.
- Create `tests/bdd/test_harness_lifecycle.py`: scenario-style lifecycle test.

## Task 1: Write Failing Tests

- [ ] **Step 1: Add unit and BDD tests before production code**

Create tests that import `attestflow` modules which do not exist yet. The expected first run must fail with import errors.

- [ ] **Step 2: Run RED verification**

Run: `python3 -m unittest discover -s tests`

Expected: non-zero exit because `attestflow` does not exist.

## Task 2: Implement Core Modules

- [ ] **Step 1: Add package entrypoints**

Create `attestflow/__init__.py` and `attestflow/__main__.py`.

- [ ] **Step 2: Add limited YAML IO**

Implement `load_data(path)` and `dump_data(data, path)` for dictionaries, lists, strings, booleans, integers, and nulls.

- [ ] **Step 3: Add config validation**

Implement `load_config(root)` and `validate_config(config)` with required top-level keys: `schema_version`, `project`, `paths`, `commands`, `policies`.

- [ ] **Step 4: Add task validation and scheduling**

Implement state constants, transition constants, `validate_task`, `iter_tasks`, `select_next_task`, and `start_task`.

- [ ] **Step 5: Add locks and evidence**

Implement task/file locks, run directory metadata, ledger append, and default evidence packet.

- [ ] **Step 6: Add resume and secret scan**

Implement unfinished run discovery and conservative secret finding without printing values.

- [ ] **Step 7: Add CLI**

Expose `init`, `doctor`, `validate-config`, `validate-task`, `tasks`, `next`, `start`, `resume`, `secret-scan`, and `verify`.

- [ ] **Step 8: Run GREEN verification**

Run: `python3 -m unittest discover -s tests`

Expected: all tests pass.

## Task 3: Add Templates And Docs

- [ ] **Step 1: Add base templates**

Create generic `harness.yml`, task sample, gates, roles, and CI template.

- [ ] **Step 2: Add README**

Document local verification, core commands, and adoption flow.

- [ ] **Step 3: Run full local verification**

Run: `python3 -m unittest discover -s tests`

Expected: all tests pass.

## Self-Review

- Spec coverage: implements config, task schema, state validation, next/start, locks, run evidence, resume, secret scan, and templates.
- Placeholder scan: no deferred `TODO`/`TBD` steps are used.
- Type consistency: modules use `pathlib.Path`, dictionaries from `io.load_data`, and task ids as strings.
