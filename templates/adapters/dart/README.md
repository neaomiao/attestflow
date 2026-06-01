# Dart Adapter

[中文](README.zh-CN.md)

Use this adapter for Dart or Flutter package roots. `attestflow init --adapter dart` detects `pubspec.yaml`, then sets:

- `unit` -> `dart test`
- `typecheck` -> `dart analyze`

Flutter apps can override these with `flutter test` and `flutter analyze`.
