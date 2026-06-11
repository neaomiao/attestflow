# GitHub Actions PR Gate

Attestflow core 不依赖 GitHub，但开源核心仓库可以发布可复用的 GitHub Actions 示例。

以 `examples/github-actions/attestflow-pr.yml` 为起点。该 workflow 做三个确定性检查：

1. 安装本地包。
2. 编译 `attestflow`、`examples` 和 `tests`。
3. 运行 `python -m attestflow verify`。
4. 对每个已完成任务运行 `python -m attestflow evidence export TASK-* --out ...` 导出证据。

如果没有已完成任务 evidence，workflow 会以 `1` 退出，因此 PR 不能在没有可审计 Attestflow evidence bundle 的情况下通过。最终 bundle 会通过 `actions/upload-artifact` 上传。

仓库 CI 还会在发布加固前跑安装矩阵：

- macOS 和 Linux 在普通源码安装后运行全量 unit、BDD、`compileall`，再运行 `python -m attestflow verify`。
- Windows 运行源码安装、`compileall`、`python -m attestflow verify` 和 install smoke；全量 unit discovery 保留在 Unix，因为部分 provider adapter 测试使用 Unix executable fixture。
- Linux 覆盖本地 venv、pipx、uv 和源码安装。
- macOS 和 Windows 覆盖 wheel 安装。
- tag 或手动运行覆盖从 PyPI 安装 `attestflow`。
- 主 verify job 运行 `attestflow install-smoke --offline --check-template-mirror`；安装矩阵里的每种打包方式也会运行 `install-smoke`。

## Runtime integration

需要 Attestflow 读取或操作 GitHub Actions evidence 时，配置内置 preset：

```yaml
integrations:
  ci_provider:
    provider: github-actions
    provider_options:
      repository: owner/repo
      branch: feature/my-change
      head_sha: abc123
      workflow: ci.yml
      event: pull_request
```

支持的动作：

```bash
python -m attestflow ci status --head-sha abc123 --branch feature/my-change --workflow ci.yml
python -m attestflow ci await --head-sha abc123 --max-wait-seconds 600
python -m attestflow ci logs --run-id 123456789
python -m attestflow ci artifacts --run-id 123456789 --download-dir attestflow-artifacts
python -m attestflow ci rerun --run-id 123456789 --failed
python -m attestflow ci dispatch --workflow ci.yml --ref feature/my-change --input task=TASK-0001
```

`status` 不再依赖“最新的 workflow run”。使用 branch、head SHA、workflow、event 或 run id 把 CI evidence 绑定到正在交付的准确 PR 或 commit。失败 run 会尽力附带 job 详情、annotation 和失败日志，让 intake/planner 能把 CI failure source 变成修复任务，而不需要人工收集日志。
