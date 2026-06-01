# .NET Adapter

Use this adapter for .NET projects. `attestflow init --adapter dotnet` detects `.sln` or `.csproj` files, then sets:

- `unit` -> `dotnet test`
- `project_verify` -> `dotnet build`

Projects with multiple solutions can narrow these commands in `harness.yml`.
