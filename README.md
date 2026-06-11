# Attestflow

[Chinese README](README.zh-CN.md)

Reusable development harness for controlled agent workflows.

Attestflow is a reusable development harness for task gates, local evidence, resumable agent workflows, and controlled repository automation.

It keeps generative work in the programming agent and keeps deterministic control in the harness: task IDs, state transitions, locks, BDD/TDD gates, verification evidence, provider contracts, CI/PR/release handoffs, and audit-friendly runtime files.

## One-Command Onboarding

Run this from the repository you want to onboard:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/neaomiao/attestflow/main/scripts/bootstrap.sh)"
```

The same script is shell-portable and can also be run with `zsh`:

```bash
zsh -c "$(curl -fsSL https://raw.githubusercontent.com/neaomiao/attestflow/main/scripts/bootstrap.sh)"
```

When the command runs in an interactive terminal, it lists the configuration choices and asks you to select them:

- harness language: `en` or `zh-CN`
- project adapter: `auto`, `generic`, `python`, `node`, `go`, `rust`, `monorepo`, `docker`, `bazel`, `java`, `kotlin`, `dotnet`, `swift`, `dart`, `ruby`, or `php`
- agent provider: `command`, `codex`, `claude-code`, or `opencode`

Non-interactive usage is explicit:

```bash
bash scripts/bootstrap.sh --yes --language en --adapter auto --agent-provider command
```

The bootstrap script installs Attestflow if needed, initializes `harness.yml`, writes the selected language to `project.language`, creates the runtime directories, and runs `attestflow doctor`.

## Local Source Install

```bash
python3 -m pip install --user .
python3 -m attestflow install-smoke --offline
python3 -m attestflow init --path /path/to/project --adapter python --language en --agent-provider command
cd /path/to/project
python3 -m attestflow doctor
```

`--language` accepts `en` and `zh-CN`. It is stored in `harness.yml` so downstream adapters, prompts, docs, and SaaS control planes can consistently render the selected language.

## Quickstart Without Model Credentials

The Python example uses the deterministic local provider and does not require a model account:

```bash
cd examples/python-basic
PYTHONPATH=../.. python3 -m attestflow doctor
PYTHONPATH=../.. python3 -m attestflow go "Add greeting support"
# Review harness/specs/SPEC-0001/spec.md, resolve Open Questions, then approve/run:
PYTHONPATH=../.. python3 -m attestflow go --from-spec harness/specs/SPEC-0001/spec.md --approve --loop --max-cycles 12 --max-steps 1
PYTHONPATH=../.. python3 -m attestflow tasks
```

After it finishes, one task reaches `done` and the project contains generated BDD/unit tests, implementation files, run ledger entries, capability evidence, and close evidence.

## Requirement-To-Spec Entry

Use `attestflow go` when you want to start from raw requirement text or a requirement document:

```bash
attestflow go "Implement login"
attestflow go "Implement login" --clarify
attestflow go PRD.md
attestflow go --from-spec harness/specs/SPEC-0001/spec.md --approve --non-interactive
```

Inline text and documents create a draft spec under `harness/specs/SPEC-*/spec.md`, generate structured Open Questions, and stop with `spec approval required`; they do not execute planner or autopilot directly. Use `--clarify` in an interactive terminal to answer the questions in-place, or edit the spec manually until Open Questions are resolved. Only an approved spec crosses the execution boundary and enters the planner/autopilot loop.

Markdown, TXT, DOCX, and copyable text-layer PDF inputs are supported. DOCX and PDF extraction require installing `attestflow[documents]`. Scanned PDFs and OCR are not supported in v1.

Open Questions default to a deterministic Q1-Q5 fallback. Production projects can set `requirements.clarifier_command` in `harness.yml` to call a real requirements clarifier provider before the draft spec is written.

## Core Commands

```bash
python3 -m attestflow validate-config
python3 -m attestflow doctor
python3 -m attestflow install-smoke --offline
python3 -m attestflow go "Implement login"
python3 -m attestflow go PRD.md
python3 -m attestflow go --from-spec harness/specs/SPEC-0001/spec.md --approve --non-interactive
python3 -m attestflow task import --from-json plan.json --from-spec harness/specs/SPEC-0001/spec.md --approve
python3 -m attestflow blackboard post --from-role reviewer --to-role implementer --type finding --body "Missing retry boundary." --requires-response
python3 -m attestflow blackboard list --status open --json
python3 -m attestflow graphify-sync --all
python3 -m attestflow schema export --type capability-output --strict --json
python3 -m attestflow autopilot --resume --max-steps 8
python3 -m attestflow autopilot --status --json
python3 -m attestflow inspect --run RUN
python3 -m attestflow verify --task TASK-0001
python3 -m attestflow close TASK-0001
python3 -m attestflow evidence export TASK-0001 --out attestflow-artifacts/TASK-0001
python3 -m attestflow evidence bundle --run RUN --out attestflow-artifacts/RUN
python3 -m attestflow evidence verify attestflow-artifacts/RUN --check-source
python3 -m attestflow provider list
python3 -m attestflow ci status --task TASK-0001
python3 -m attestflow pr ensure TASK-0001
python3 -m attestflow pr merge TASK-0001
python3 -m attestflow release trust --out attestflow-release-trust --json
python3 -m attestflow usage report --json
python3 -m attestflow secret-scan
```

## What It Provides

- `harness.yml` validation and recoverable runtime layout
- task schema, state machine, DoR/DoD gates, BDD/TDD ordering, write-scope locks, and local evidence
- built-in project adapters for common language stacks
- provider contracts for planner, capability, session, blackboard, Git, CI, PR, and release adapters
- Codex, Claude Code, OpenCode, and command-provider presets
- AI-first planner import from JSON or stdin
- source intake for GitHub issues, Linear/Jira tickets, PR comments, and CI failures
- context budgeting, context cache, dynamic context requests, incremental context, evidence summaries, and optional provider result cache
- GitHub Actions status/log/artifact/rerun/dispatch support through the CI provider
- PR ensure/status/merge automation and optional auto-merge after CI passes
- release evidence and release trust bundle generation
- strict JSON Schema/OpenAPI export for provider contract hardening
- dashboard export, evidence retention/compaction/redaction, usage reporting, policy packs, plugin discovery, and plugin command execution

## Documentation

- [Getting Started](docs/getting-started.en.md)
- [Provider Cookbook](docs/providers.en.md)
- [GitHub Actions](docs/github-actions.md)
- [Universal Harness Design](docs/design/universal-harness.en.md)
- [Agent Blackboard Contract](docs/contracts/blackboard-schema.en.md)
- [Governance](docs/governance.en.md)

The English README is the default GitHub entrypoint. The Chinese README is maintained at [README.zh-CN.md](README.zh-CN.md).
