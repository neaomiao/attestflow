# Python Adapter

用于 Python 项目。`attestflow init --adapter python` 会读取显式项目工具配置，并填入对应命令：

- `[tool.pytest.ini_options]` 或 `pytest.ini` -> `python -m pytest`
- `[tool.ruff]` -> `python -m ruff check .`
- `[tool.mypy]` -> `python -m mypy .`

没有配置的工具会保留 base standard-library 默认值，项目之后可以通过 `harness.yml` 选择启用。
