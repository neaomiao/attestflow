# Ruby Adapter

[English](README.md)

用于 Ruby 项目。`attestflow init --adapter ruby` 会检测 `Gemfile` 和 `Rakefile`，并设置：

- `unit` -> `bundle exec rake test`
- `project_verify` -> `bundle exec rake`

使用 RSpec 或自定义 Rake task 的项目可以在 `harness.yml` 覆盖命令。
