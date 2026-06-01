# Swift Adapter

[中文](README.zh-CN.md)

Use this adapter for Swift Package Manager projects. `attestflow init --adapter swift` detects `Package.swift`, then sets:

- `unit` -> `swift test`
- `project_verify` -> `swift build`

Xcode-only projects should override commands in `harness.yml`.
