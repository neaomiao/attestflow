# Go Adapter

[中文](README.zh-CN.md)

Use this adapter for Go modules. During `attestflow init --adapter go`, Attestflow checks for `go.mod` and maps the core verification command into `harness.yml`:

- `commands.unit`: `go test ./...`
- `commands.project_verify`: `go test ./...`

The adapter stays intentionally thin. Attestflow owns task state, locks, evidence, and contract validation; your Go toolchain owns compilation and tests.
