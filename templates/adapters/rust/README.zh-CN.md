# Rust Adapter

用于 Rust crate 或 workspace。`attestflow init --adapter rust` 会检测 `Cargo.toml`，并把核心验证命令写入 `harness.yml`：

- `commands.unit`: `cargo test`
- `commands.typecheck`: `cargo check --all-targets --all-features`
- `commands.project_verify`: `cargo build`

Adapter 保持轻量。Attestflow 负责任务状态、锁、证据和 contract validation；Cargo 负责编译和测试。
