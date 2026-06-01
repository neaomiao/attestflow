# Bazel Adapter

[English](README.md)

用于 Bazel workspace。`attestflow init --adapter bazel` 会检测 `MODULE.bazel`、`WORKSPACE.bazel` 或 `WORKSPACE`，并设置：

- `unit` -> `bazel test //...`
- `project_verify` -> `bazel build //...`

如果项目需要更窄的 target pattern，可以在 `harness.yml` 覆盖这些命令。
