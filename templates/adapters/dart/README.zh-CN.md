# Dart Adapter

用于 Dart 或 Flutter package root。`attestflow init --adapter dart` 会检测 `pubspec.yaml`，并设置：

- `unit` -> `dart test`
- `typecheck` -> `dart analyze`

Flutter app 可以在 `harness.yml` 改成 `flutter test` 和 `flutter analyze`。
