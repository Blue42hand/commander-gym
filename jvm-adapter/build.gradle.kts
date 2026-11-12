plugins {
    kotlin("jvm") version "2.4.21"
    kotlin("plugin.serialization") version "2.4.21"
}

dependencies {
    // This jar is loaded by Argentum's game-server, so Argentum itself owns these runtime
    // modules. Keeping them compile-only avoids dragging the host's full server dependency graph
    // (database drivers, Flyway, etc.) into the standalone adapter's runtime resolution.
    compileOnly("com.wingedsheep:argentum-ai")
    compileOnly("com.wingedsheep:argentum-game-server")
    compileOnly("com.wingedsheep:argentum-rules-engine")
    compileOnly("com.wingedsheep:argentum-sdk")

    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.12.0")
    implementation("org.springframework.boot:spring-boot-autoconfigure:4.1.2")

    testImplementation(kotlin("test"))
    // Composite project substitution does not carry Argentum's Spring dependency-management
    // plugin into this standalone build. Import the same Boot BOM so versionless runtime
    // dependencies declared by game-server (Flyway/Postgres, etc.) resolve exactly as upstream.
    testImplementation(platform("org.springframework.boot:spring-boot-dependencies:4.1.2"))
    testImplementation("com.wingedsheep:argentum-ai")
    // Acceptance tests boot the actual Argentum game-server from the composite build and exercise
    // AiPlayerController directly. Keep those host modules test-only so the adapter artifact itself
    // still does not own Argentum runtime dependencies.
    testImplementation("com.wingedsheep:argentum-ai")
    testImplementation("com.wingedsheep:argentum-game-server")
    testImplementation("com.wingedsheep:argentum-rules-engine")
    testImplementation("com.wingedsheep:argentum-sdk")
    testImplementation("org.springframework.boot:spring-boot-starter-test:4.1.2")
    testImplementation("org.springframework:spring-websocket")
}

kotlin { jvmToolchain(25) }
tasks.test { useJUnitPlatform() }

val nativeCheckout = file(providers.environmentVariable("ARGENTUM_ENGINE_DIR").orElse("../argentum-engine").get())
val nativeRevision = providers.exec {
    workingDir(nativeCheckout)
    commandLine("git", "rev-parse", "HEAD")
}.standardOutput.asText.map { it.trim() }
val gymRevision = providers.exec {
    workingDir(projectDir)
    commandLine("git", "rev-parse", "HEAD")
}.standardOutput.asText.map { it.trim() }
tasks.jar {
    inputs.property("nativeRevision", nativeRevision)
    inputs.property("gymRevision", gymRevision)
    manifest.attributes("Argentum-Revision" to nativeRevision.get(), "Commander-Gym-Revision" to gymRevision.get())
}

tasks.register<JavaExec>("runLocalGuiServer") {
    group = "application"
    description = "Run the normal Argentum game server with the Commander Gym provider loaded"
    dependsOn(tasks.named("testClasses"))
    classpath = sourceSets["test"].runtimeClasspath
    mainClass.set("org.commandergym.argentum.LocalGuiGameServerKt")
    standardInput = System.`in`
}

// Explicit opt-in fixture: runtime config and credentials are never bundled.
tasks.register<JavaExec>("runRecordedFullGame") {
    group = "verification"
    dependsOn(tasks.named("testClasses"))
    classpath = sourceSets["test"].runtimeClasspath
    mainClass.set("org.commandergym.argentum.RecordedFullGameKt")
}

// Explicit local-only opt-in. CI never contacts a model; no credentials are bundled.
tasks.register<JavaExec>("runLocalInferenceAcceptance") {
    group = "verification"
    dependsOn(tasks.named("testClasses"))
    classpath = sourceSets["test"].runtimeClasspath
    mainClass.set("org.commandergym.argentum.LocalInferenceAcceptanceKt")
}

// Public definitions from the exact composite-build engine, with no GameState or provider.
tasks.register<JavaExec>("exportPublicCardFacts") {
    group = "verification"
    dependsOn(tasks.named("testClasses"))
    classpath = sourceSets["test"].runtimeClasspath
    mainClass.set("org.commandergym.argentum.ExportPublicCardFactsKt")
    systemProperty("commander.gym.facts.engineRevision", nativeRevision.get())
    if (providers.gradleProperty("factsInput").isPresent && providers.gradleProperty("factsOutput").isPresent) {
        args(providers.gradleProperty("factsInput").get(), providers.gradleProperty("factsOutput").get())
    }
}

// Explicit private four-seat game; never invoked by CI or an ordinary test task.
tasks.register<JavaExec>("runLathrilMirrorGame") {
    group = "verification"
    dependsOn(tasks.named("testClasses"))
    classpath = sourceSets["test"].runtimeClasspath
    mainClass.set("org.commandergym.argentum.LathrilMirrorGame")
}

// Explicit offline replay of a private native rejection, never an ordinary test task.
tasks.register<JavaExec>("runConvokeSavedStateProbe") {
    group = "verification"
    dependsOn(tasks.named("testClasses"))
    classpath = sourceSets["test"].runtimeClasspath
    mainClass.set("org.commandergym.argentum.ConvokeSavedStateProbe")
}
