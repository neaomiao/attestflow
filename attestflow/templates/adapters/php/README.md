# PHP Adapter

[中文](README.zh-CN.md)

Use this adapter for Composer-based PHP projects. `attestflow init --adapter php` detects `composer.json`, then sets:

- Composer `test` script -> `composer test`
- PHPUnit config fallback -> `vendor/bin/phpunit`
- `project_verify` -> `composer validate`

Projects can override commands in `harness.yml`.
