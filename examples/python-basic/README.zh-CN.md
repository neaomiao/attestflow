# Python 基础示例

[English](README.md)

这个示例不需要外部 AI 账号，可以跑完整的开源 Attestflow core。
它使用 `../providers/local_agent.py` 作为 deterministic provider，写入 demo tests 和 implementation。
下面命令假设 `python` 指向 Python 3.11+。

在本目录运行：

```bash
PYTHONPATH=../.. python -m attestflow doctor
PYTHONPATH=../.. python -m attestflow autopilot --run --goal "Add greeting support" --loop --max-cycles 12 --max-steps 1
PYTHONPATH=../.. python -m attestflow tasks
PYTHONPATH=../.. python -m attestflow evidence TASK-0001
```

预期结果：一个任务进入 `done`，生成 `greeter.py`，并在 `harness/runs/` 下保存 BDD/unit verification evidence。
