# Docker Adapter

[中文](README.zh-CN.md)

Use this adapter when the project is primarily verified through a container image. `attestflow init --adapter docker` detects `Dockerfile` and common Compose files, enables the Docker execution policy, and sets:

- `project_verify` -> `docker build .`

Add service-specific test commands in `harness.yml` if tests must run inside Compose.
