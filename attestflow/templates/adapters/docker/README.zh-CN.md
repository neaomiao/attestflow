# Docker Adapter

[English](README.md)

用于主要通过容器镜像验证的项目。`attestflow init --adapter docker` 会检测 `Dockerfile` 和常见 Compose 文件，启用 Docker 执行策略，并设置：

- `project_verify` -> `docker build .`

如果测试必须在 Compose 服务内运行，请在 `harness.yml` 添加服务级测试命令。
