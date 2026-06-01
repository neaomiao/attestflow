# .NET Adapter

用于 .NET 项目。`attestflow init --adapter dotnet` 会检测 `.sln` 或 `.csproj` 文件，并设置：

- `unit` -> `dotnet test`
- `project_verify` -> `dotnet build`

多 solution 项目可以在 `harness.yml` 收窄命令范围。
