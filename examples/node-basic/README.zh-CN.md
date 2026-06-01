# Node 基础示例

这个示例对应 `examples/python-basic`，用于 Node.js 项目。
它使用 `../providers/local_agent.py` 作为 deterministic local provider，因此不需要模型凭证。
下面命令假设 `python` 指向 Python 3.11+。

在本目录运行，并确保本机已经安装 Node.js：

```bash
PYTHONPATH=../.. python -m attestflow doctor
PYTHONPATH=../.. python -m attestflow autopilot --run --goal "Add greeting support" --loop --max-cycles 12 --max-steps 1
PYTHONPATH=../.. python -m attestflow tasks
```

预期结果：一个任务进入 `done`，生成 `greeter.js`，并在 `harness/runs/` 下保存 `node --test` evidence。
