# Node Basic Example

[中文](README.zh-CN.md)

This example mirrors `examples/python-basic` for a Node.js project.
It uses the deterministic local provider at `../providers/local_agent.py`, so it does not need model credentials.
The commands assume `python` points to Python 3.11+.

Run from this directory on a machine with Node.js:

```bash
PYTHONPATH=../.. python -m attestflow doctor
PYTHONPATH=../.. python -m attestflow autopilot --run --goal "Add greeting support" --loop --max-cycles 12 --max-steps 1
PYTHONPATH=../.. python -m attestflow tasks
```

Expected result: one task moves to `done`, `greeter.js` is created, and `node --test` evidence is stored under `harness/runs/`.
