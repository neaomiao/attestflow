# Rust Adapter

[中文](README.zh-CN.md)

Use this adapter for Rust crates or workspaces. During `attestflow init --adapter rust`, Attestflow checks for `Cargo.toml` and maps the core verification commands into `harness.yml`:

- `commands.unit`: `cargo test`
- `commands.typecheck`: `cargo check --all-targets --all-features`
- `commands.project_verify`: `cargo build`

The adapter stays intentionally thin. Attestflow owns task state, locks, evidence, and contract validation; Cargo owns compilation and tests.
