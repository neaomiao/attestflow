# 贡献指南

[English](CONTRIBUTING.md)

Attestflow 是 AI-first 开发 harness。贡献应保持核心边界：

- 生成性工作属于编程 agent provider。
- Attestflow core 负责确定性校验、ID、状态、锁、验证、证据和恢复。
- Runtime task JSON 由 Attestflow 写入，不由 provider 直接写入。

## 开发环境

```bash
python3 -m unittest discover -s tests
python3 -m attestflow verify
```

项目运行时刻意不依赖第三方 Python 包。

## 变更规则

- 行为变更前先添加或更新测试。
- Provider 集成必须留在 command contract 或内置 adapter 后面。
- 不把 SaaS 控制面行为加入开源核心。
- 不在 core verification 路径加入网络调用。
- 保持源码模板和打包模板同步。

## Pull Request 清单

- `python3 -m unittest discover -s tests` 通过。
- `python3 -m attestflow verify` 通过。
- 新命令、contract、provider 行为或示例同步更新文档。
- 新 provider 行为留下 `input.json`、`stdout.log`、`stderr.log` 和 `output.json` 证据。
