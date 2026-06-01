# Java Adapter

[English](README.md)

用于 Java 项目。`attestflow init --adapter java` 会检测 Maven 或 Gradle 文件，并设置：

- Maven: `unit` -> `mvn test`，`project_verify` -> `mvn verify`
- Gradle: `unit` -> `./gradlew test` 或 `gradle test`，`project_verify` -> `./gradlew build` 或 `gradle build`

项目可以在 `harness.yml` 覆盖命令。
