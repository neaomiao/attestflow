# Python Basic Example

This example runs the full open-source Attestflow core without any external AI account.
It uses `../providers/local_agent.py` as a deterministic provider that writes the demo tests and implementation.
The commands assume `python` points to Python 3.11+.

Run from this directory:

```bash
PYTHONPATH=../.. python -m attestflow doctor
PYTHONPATH=../.. python -m attestflow go "Add greeting support"
# Answer the printed Open Questions, or edit harness/specs/SPEC-0001/spec.md until Open Questions is None.
PYTHONPATH=../.. python -m attestflow go --from-spec harness/specs/SPEC-0001/spec.md --approve --loop --max-cycles 12 --max-steps 1
PYTHONPATH=../.. python -m attestflow tasks
PYTHONPATH=../.. python -m attestflow evidence TASK-0001
```

Expected result: one task moves to `done`, `greeter.py` is created, and BDD/unit verification evidence is stored under `harness/runs/`.
