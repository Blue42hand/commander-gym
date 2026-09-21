plugins {
    kotlin("jvm") version "2.4.0"
    kotlin("plugin.serialization") version "2.4.0"
}

dependencies {
    // The included Argentum game-server build intentionally leaves a few persistence library
    // versions to Spring Boot dependency management. Import the same host platform here so those
    // transitive project dependencies remain resolvable when the adapter is compiled as a
    // composite build rather than from inside Argentum's root build.
    implementation(platform("org.springframework.boot:spring-boot-dependencies:4.1.0"))

    implementation("com.wingedsheep:argentum-ai")
    implementation("com.wingedsheep:argentum-game-server")
    implementation("com.wingedsheep:argentum-rules-engine")
    implementation("com.wingedsheep:argentum-sdk")
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.11.0")
    implementation("org.springframework.boot:spring-boot-autoconfigure:4.1.0")
    testImplementation(kotlin("test"))
}

kotlin { jvmToolchain(21) }
tasks.test { useJUnitPlatform() }
