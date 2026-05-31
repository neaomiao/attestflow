# Kotlin Adapter

Use this adapter for Kotlin JVM projects. `attestflow init --adapter kotlin` prefers Gradle/Kotlin DSL files, then sets:

- Gradle: `unit` -> `./gradlew test` or `gradle test`
- Gradle: `project_verify` -> `./gradlew build` or `gradle build`

Maven Kotlin projects fall back to `mvn test` and `mvn verify`.
