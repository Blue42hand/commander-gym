package org.commandergym.argentum

import com.wingedsheep.gameserver.ai.AiControllerProvider
import org.springframework.boot.autoconfigure.AutoConfiguration
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.context.annotation.Bean
import org.springframework.core.env.Environment
import java.net.URI
import java.nio.file.Path
import java.time.Duration

/** Separate opt-in local service. Never redirects or replaces the Luna provider. */
@AutoConfiguration
@ConditionalOnProperty(name = ["commander-gym.local.enabled"], havingValue = "true")
class CommanderGymLocalAutoConfiguration {
    @Bean
    fun commanderGymLocalAiControllerProvider(environment: Environment): AiControllerProvider {
        val prefix = "commander-gym.local"
        val timeout = environment.getProperty("$prefix.timeout-ms", Long::class.java, 120_000L)
        require(timeout in 1..120_000L) { "Local callback timeout must be in 1..120000 ms" }
        return CommanderGymControllerProvider(
            URI.create(environment.getRequiredProperty("$prefix.url")),
            environment.getRequiredProperty("$prefix.token"), Duration.ofMillis(timeout),
            requireHumanParticipant = environment.getProperty("$prefix.manual-only", Boolean::class.java, true),
            recorder = environment.getProperty("commander-gym.recorder.socket")?.let { RecorderCallbackClient(Path.of(it)) },
            mode = "commander-gym-local", profileLabel = "Local", requireExplicitProfile = true,
        )
    }
}
