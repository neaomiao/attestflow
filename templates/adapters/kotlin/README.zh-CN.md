# Kotlin Adapter

[English](README.md)

用于 Kotlin JVM 项目。`attestflow init --adapter kotlin` 会优先检测 Gradle/Kotlin DSL 文件，并设置：

- Gradle: `unit` -> `./gradlew test` 或 `gradle test`
- Gradle: `project_verify` -> `./gradlew build` 或 `gradle build`

Maven Kotlin 项目会回退到 `mvn test` 和 `mvn verify`。
