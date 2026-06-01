# PHP Adapter

[English](README.md)

用于 Composer PHP 项目。`attestflow init --adapter php` 会检测 `composer.json`，并设置：

- Composer `test` script -> `composer test`
- PHPUnit config fallback -> `vendor/bin/phpunit`
- `project_verify` -> `composer validate`

项目可以在 `harness.yml` 覆盖命令。
