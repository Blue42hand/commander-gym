package org.commandergym.argentum

import com.wingedsheep.gameserver.GameServerApplication
import org.springframework.boot.builder.SpringApplicationBuilder

/**
 * Local manual-play launcher.
 *
 * Runs the normal Argentum game server with the Commander Gym provider available on the same
 * runtime classpath as the acceptance tests. Policy remains in the separate loopback sidecar.
 */
internal fun localGuiServerArgs(
    serverPort: Int,
    sidecarUrl: String,
    token: String,
    sidecarTimeoutMs: Long,
): Array<String> = arrayOf(
    // The local dev tournament endpoint is unauthenticated; keep this launcher on loopback.
    "--server.address=127.0.0.1",
    "--server.port=$serverPort",
    "--spring.profiles.active=local",
    // The local profile is useful for development but defaults to omniscient game views.
    // A strategic sidecar must receive the same hidden-information view as a human seat.
    "--game.debug-mode=false",
    "--game.ai.enabled=true",
    "--game.ai.mode=commander-gym",
    "--game.ai.thinking-delay-ms=0",
    "--game.dev-endpoints.enabled=true",
    "--commander-gym.sidecar.url=$sidecarUrl",
    "--commander-gym.sidecar.token=$token",
    "--commander-gym.sidecar.timeout-ms=$sidecarTimeoutMs",
)

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
        .run(*localGuiServerArgs(serverPort, sidecarUrl, token, sidecarTimeoutMs))

    Runtime.getRuntime().addShutdownHook(Thread { context.close() })
}
