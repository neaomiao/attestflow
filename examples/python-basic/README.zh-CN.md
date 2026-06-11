# Python 基础示例

这个示例不需要外部 AI 账号，可以跑完整的开源 Attestflow core。
它使用 `../providers/local_agent.py` 作为 deterministic provider，写入 demo tests 和 implementation。
下面命令假设 `python` 指向 Python 3.11+。

在本目录运行：

```bash
PYTHONPATH=../.. python -m attestflow doctor
PYTHONPATH=../.. python -m attestflow go "Add greeting support"
# 回答输出的 Open Questions，或手动编辑 harness/specs/SPEC-0001/spec.md，直到 Open Questions 为 None。
PYTHONPATH=../.. python -m attestflow go --from-spec harness/specs/SPEC-0001/spec.md --approve --loop --max-cycles 12 --max-steps 1
PYTHONPATH=../.. python -m attestflow tasks
PYTHONPATH=../.. python -m attestflow evidence TASK-0001
```

预期结果：一个任务进入 `done`，生成 `greeter.py`，并在 `harness/runs/` 下保存 BDD/unit verification evidence。
