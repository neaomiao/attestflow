# Go Adapter

用于 Go module。`attestflow init --adapter go` 会检测 `go.mod`，并把核心验证命令写入 `harness.yml`：

- `commands.unit`: `go test ./...`
- `commands.project_verify`: `go test ./...`

Adapter 保持轻量。Attestflow 负责任务状态、锁、证据和 contract validation；Go 工具链负责编译和测试。
