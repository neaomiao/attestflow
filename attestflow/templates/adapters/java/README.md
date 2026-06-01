# Java Adapter

[中文](README.zh-CN.md)

Use this adapter for Java projects. `attestflow init --adapter java` detects Maven or Gradle files, then sets:

- Maven: `unit` -> `mvn test`, `project_verify` -> `mvn verify`
- Gradle: `unit` -> `./gradlew test` or `gradle test`, `project_verify` -> `./gradlew build` or `gradle build`

Projects can override commands in `harness.yml`.
