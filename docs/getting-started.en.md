# Getting Started

This guide validates the open-source core: install, initialize, planner import, capability execution, verification, evidence, and task close.

## 1. Local Example

The Python example uses the deterministic local provider and does not require model credentials:

```bash
cd examples/python-basic
PYTHONPATH=../.. python3 -m attestflow doctor
PYTHONPATH=../.. python3 -m attestflow go "Add greeting support"
# Review harness/specs/SPEC-0001/spec.md, resolve Open Questions, then approve/run:
PYTHONPATH=../.. python3 -m attestflow go --from-spec harness/specs/SPEC-0001/spec.md --approve --loop --max-cycles 12 --max-steps 1
PYTHONPATH=../.. python3 -m attestflow tasks
PYTHONPATH=../.. python3 -m attestflow evidence TASK-0001
```

After it finishes, one task reaches `done` and the project contains implementation files, BDD/unit tests, run ledger entries, capability evidence, and close evidence.

## 2. Onboard Your Repository

Use the one-command bootstrap from the target repository:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/neaomiao/attestflow/main/scripts/bootstrap.sh)"
```

Interactive mode lists the choices for language, project adapter, and agent provider. Non-interactive mode is explicit:

```bash
bash scripts/bootstrap.sh --yes --language en --adapter auto --agent-provider command
```

If you are working from a local checkout:

```bash
python3 -m pip install --user .
python3 -m attestflow install-smoke --offline
python3 -m attestflow init --path /path/to/project --adapter python --language en --agent-provider command
cd /path/to/project
python3 -m attestflow doctor
```

The selected language is stored in `harness.yml` as `project.language`. Supported values are `en` and `zh-CN`.

## 3. Minimal Loop

`attestflow go` accepts inline text, Markdown, TXT, DOCX, and copyable text-layer PDF input. DOCX/PDF parsing requires installing `attestflow[documents]`; scanned PDFs/OCR are not supported in v1, so convert them to Markdown, TXT, DOCX, or a PDF with copyable text first.

If work enters from an external system, preserve the source snapshot before planning:

```bash
python3 -m attestflow source import --kind github-issue --from-json issue.json
python3 -m attestflow source import --kind pr-review-comment --from-json review-comment.json
python3 -m attestflow source import --kind ci-failure --from-json ci-failure.json
```

After configuring `capabilities.planner.command`, `capabilities.bdd.command`, `capabilities.tdd.command`, `capabilities.implementer.command`, and `capabilities.reviewer.command`, run:

```bash
python3 -m attestflow go "Implement the next small feature"
# Review harness/specs/SPEC-0001/spec.md, resolve Open Questions, then approve/run:
python3 -m attestflow go --from-spec harness/specs/SPEC-0001/spec.md --approve --loop --max-cycles 20 --max-steps 1
```

Raw text or documents stop at draft spec creation. Only an approved spec enters the planner/autopilot loop, where Attestflow imports planner JSON, dispatches ready tasks, creates run/session/lock evidence, advances BDD/TDD/implementation/review/verify gates, and closes only with fresh evidence.

## 4. Troubleshooting

- `python3 -m attestflow doctor`: config, runtime directories, commands, and provider preflight.
- `python3 -m attestflow autopilot --status --json`: latest top-level run status.
- `python3 -m attestflow inspect --run RUN`: timeline, blockers, provider failures, and next action.
- `python3 -m attestflow recover --apply`: deterministic repair for missing runtime layout or interrupted runs.
- `python3 -m attestflow evidence verify DIR --check-source`: bundle integrity and source-evidence drift.
