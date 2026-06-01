# Swift Adapter

用于 Swift Package Manager 项目。`attestflow init --adapter swift` 会检测 `Package.swift`，并设置：

- `unit` -> `swift test`
- `project_verify` -> `swift build`

仅使用 Xcode 的项目应在 `harness.yml` 覆盖命令。
