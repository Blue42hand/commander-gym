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
    compileOnly("com.wingedsheep:argentum-rules-engine")
    compileOnly("com.wingedsheep:argentum-sdk")

    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.11.0")
    implementation("org.springframework.boot:spring-boot-autoconfigure:4.1.0")
    testImplementation(kotlin("test"))
}

kotlin { jvmToolchain(21) }
tasks.test { useJUnitPlatform() }
