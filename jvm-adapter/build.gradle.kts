plugins {
    kotlin("jvm") version "2.4.0"
    kotlin("plugin.serialization") version "2.4.0"
}

dependencies {
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
