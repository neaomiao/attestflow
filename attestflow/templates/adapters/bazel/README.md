# Bazel Adapter

Use this adapter for Bazel workspaces. `attestflow init --adapter bazel` detects `MODULE.bazel`, `WORKSPACE.bazel`, or `WORKSPACE`, then sets:

- `unit` -> `bazel test //...`
- `project_verify` -> `bazel build //...`

Projects with narrower target patterns can override these commands in `harness.yml`.
