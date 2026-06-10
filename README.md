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
PYTHONPATH=../.. python3 -m attestflow autopilot --run --goal "Add greeting support" --loop --max-cycles 12 --max-steps 1
PYTHONPATH=../.. python3 -m attestflow tasks
```

After it finishes, one task reaches `done` and the project contains generated BDD/unit tests, implementation files, run ledger entries, capability evidence, and close evidence.

## Core Commands

```bash
python3 -m attestflow validate-config
python3 -m attestflow doctor
python3 -m attestflow install-smoke --offline
python3 -m attestflow plan "Implement login"
python3 -m attestflow task import --from-json plan.json
python3 -m attestflow autopilot --run --goal "Implement login" --loop --max-cycles 20 --max-steps 1
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
- provider contracts for planner, capability, session, Git, CI, PR, and release adapters
- Codex, Claude Code, OpenCode, and command-provider presets
- AI-first planner import from JSON or stdin
- source intake for GitHub issues, Linear/Jira tickets, PR comments, and CI failures
- context budgeting, context cache, dynamic context requests, incremental context, evidence summaries, and optional provider result cache
- GitHub Actions status/log/artifact/rerun/dispatch support through the CI provider
- PR ensure/status/merge automation and optional auto-merge after CI passes
- release evidence and release trust bundle generation
- dashboard export, evidence retention/compaction/redaction, usage reporting, policy packs, plugin discovery, and plugin command execution

## Documentation

- [Getting Started](docs/getting-started.en.md)
- [Provider Cookbook](docs/providers.en.md)
- [GitHub Actions](docs/github-actions.md)
- [Universal Harness Design](docs/design/universal-harness.en.md)
- [Governance](docs/governance.en.md)

The English README is the default GitHub entrypoint. The Chinese README is maintained at [README.zh-CN.md](README.zh-CN.md).
