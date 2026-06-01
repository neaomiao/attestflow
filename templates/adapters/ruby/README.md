# Ruby Adapter

Use this adapter for Ruby projects. `attestflow init --adapter ruby` detects `Gemfile` and `Rakefile`, then sets:

- `unit` -> `bundle exec rake test`
- `project_verify` -> `bundle exec rake`

Projects using RSpec or custom Rake tasks can override commands in `harness.yml`.
