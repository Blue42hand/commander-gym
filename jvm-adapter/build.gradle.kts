plugins {
    kotlin("jvm") version "2.4.0"
    kotlin("plugin.serialization") version "2.4.0"
}

dependencies {
    // This jar is loaded by Argentum's game-server, so Argentum itself owns these runtime
    // modules. Keeping them compile-only avoids dragging the host's full server dependency graph
    // (database drivers, Flyway, etc.) into the standalone adapter's runtime resolution.
    compileOnly("com.wingedsheep:argentum-ai")
    compileOnly("com.wingedsheep:argentum-game-server")
    compileOnly("com.wingedsheep:argentum-gym")
    compileOnly("com.wingedsheep:argentum-rules-engine")
    compileOnly("com.wingedsheep:argentum-sdk")

    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.11.0")
    implementation("org.springframework.boot:spring-boot-autoconfigure:4.1.0")

    testImplementation(kotlin("test"))
    // Composite project substitution does not carry Argentum's Spring dependency-management
    // plugin into this standalone build. Import the same Boot BOM so versionless runtime
    // dependencies declared by game-server (Flyway/Postgres, etc.) resolve exactly as upstream.
    testImplementation(platform("org.springframework.boot:spring-boot-dependencies:4.1.0"))
    // Acceptance tests boot the actual vanilla Argentum game-server from the composite build.
    // These remain test-only so the adapter artifact itself does not own Argentum runtime deps.
    testImplementation("com.wingedsheep:argentum-game-server")
    testImplementation("com.wingedsheep:argentum-gym")
    testImplementation("com.wingedsheep:argentum-rules-engine")
    testImplementation("com.wingedsheep:argentum-sdk")
    testImplementation("org.springframework.boot:spring-boot-starter-test:4.1.0")
}

kotlin { jvmToolchain(21) }
tasks.test { useJUnitPlatform() }
