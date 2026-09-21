package org.commandergym.argentum

import com.wingedsheep.gameserver.ai.AiControllerProvider
import org.springframework.boot.autoconfigure.AutoConfiguration
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.context.annotation.Bean
import org.springframework.core.env.Environment
import java.net.URI
import java.time.Duration

/** Makes the adapter a normal provider bean when its vanilla Argentum mode is selected. */
@AutoConfiguration
@ConditionalOnProperty(name = ["game.ai.mode"], havingValue = "commander-gym")
class CommanderGymAutoConfiguration {
    @Bean
    fun commanderGymAiControllerProvider(environment: Environment): AiControllerProvider {
        val endpoint = environment.getRequiredProperty("commander-gym.sidecar.url")
        val token = environment.getRequiredProperty("commander-gym.sidecar.token")
        val timeout = environment.getProperty("commander-gym.sidecar.timeout-ms", Long::class.java, 30_000L)
        require(timeout > 0) { "commander-gym.sidecar.timeout-ms must be positive" }
        return CommanderGymControllerProvider(URI.create(endpoint), token, Duration.ofMillis(timeout))
    }
}
