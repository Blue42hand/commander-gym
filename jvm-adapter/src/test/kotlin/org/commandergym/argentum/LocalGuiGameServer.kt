package org.commandergym.argentum

import com.wingedsheep.gameserver.GameServerApplication
import org.springframework.boot.builder.SpringApplicationBuilder

/**
 * Local manual-play launcher.
 *
 * Runs the normal Argentum game server with the Commander Gym provider available on the same
 * runtime classpath as the acceptance tests. Policy remains in the separate loopback sidecar.
 */
fun main() {
    val token = System.getenv("COMMANDER_GYM_SIDECAR_TOKEN")
        ?.takeIf { it.isNotBlank() }
        ?: error("COMMANDER_GYM_SIDECAR_TOKEN must be set")

    val serverPort = System.getenv("COMMANDER_GYM_GUI_SERVER_PORT")
        ?.toIntOrNull()
        ?.takeIf { it in 1..65535 }
        ?: 8080
    val sidecarUrl = System.getenv("COMMANDER_GYM_SIDECAR_URL")
        ?.takeIf { it.isNotBlank() }
        ?: "http://127.0.0.1:8083"
    val sidecarTimeoutMs = System.getenv("COMMANDER_GYM_JVM_SIDECAR_TIMEOUT_MS")
        ?.toLongOrNull()
        ?.coerceAtLeast(1_000)
        ?: 120_000

    val context = SpringApplicationBuilder(GameServerApplication::class.java)
        .run(
            "--server.port=$serverPort",
            "--spring.profiles.active=local",
            "--game.ai.enabled=true",
            "--game.ai.mode=commander-gym",
            "--game.ai.thinking-delay-ms=0",
            "--commander-gym.sidecar.url=$sidecarUrl",
            "--commander-gym.sidecar.token=$token",
            "--commander-gym.sidecar.timeout-ms=$sidecarTimeoutMs",
        )

    Runtime.getRuntime().addShutdownHook(Thread { context.close() })
}
