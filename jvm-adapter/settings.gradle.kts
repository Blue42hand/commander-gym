pluginManagement { repositories { gradlePluginPortal(); mavenCentral() } }
dependencyResolutionManagement { repositories { mavenCentral() } }

val argentumDir = providers.environmentVariable("ARGENTUM_ENGINE_DIR")
    .orElse("../argentum-engine")
    .get()

includeBuild(argentumDir) {
    dependencySubstitution {
        substitute(module("com.wingedsheep:argentum-ai")).using(project(":ai"))
        substitute(module("com.wingedsheep:argentum-game-server")).using(project(":game-server"))
        substitute(module("com.wingedsheep:argentum-rules-engine")).using(project(":rules-engine"))
        substitute(module("com.wingedsheep:argentum-sdk")).using(project(":mtg-sdk"))
    }
}

rootProject.name = "commander-gym-argentum-adapter"
