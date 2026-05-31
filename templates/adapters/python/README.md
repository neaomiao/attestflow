# Python Adapter

Use this adapter for Python projects. During `attestflow init --adapter python`, Attestflow reads explicit project tool configuration and fills matching commands:

- `[tool.pytest.ini_options]` or `pytest.ini` -> `python -m pytest`
- `[tool.ruff]` -> `python -m ruff check .`
- `[tool.mypy]` -> `python -m mypy .`

If a tool is not configured, the base standard-library defaults stay in place and the project can opt in later through `harness.yml`.
