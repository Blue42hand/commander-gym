package org.commandergym.argentum

import com.wingedsheep.gameserver.ai.AiControllerProvider
import org.springframework.boot.autoconfigure.AutoConfiguration
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.boot.autoconfigure.condition.AnyNestedCondition
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Conditional
import org.springframework.context.annotation.ConfigurationCondition.ConfigurationPhase
import org.springframework.core.env.Environment
import java.net.URI
import java.time.Duration

/** Enables per-seat Gym profiles alongside Engine AI, or the legacy server-wide mode. */
@AutoConfiguration
@Conditional(CommanderGymEnabledCondition::class)
class CommanderGymAutoConfiguration {
    @Bean
    fun commanderGymAiControllerProvider(environment: Environment): AiControllerProvider {
        val endpoint = environment.getRequiredProperty("commander-gym.sidecar.url")
        val token = environment.getRequiredProperty("commander-gym.sidecar.token")
        val timeout = environment.getProperty("commander-gym.sidecar.timeout-ms", Long::class.java, 120_000L)
        require(timeout > 0) { "commander-gym.sidecar.timeout-ms must be positive" }
        return CommanderGymControllerProvider(
            URI.create(endpoint), token, Duration.ofMillis(timeout),
            requireHumanParticipant = environment.getProperty("commander-gym.manual-only", Boolean::class.java, false),
        )
    }
}

class CommanderGymEnabledCondition : AnyNestedCondition(ConfigurationPhase.REGISTER_BEAN) {
    @ConditionalOnProperty(name = ["commander-gym.enabled"], havingValue = "true")
    class ExplicitlyEnabled

    @ConditionalOnProperty(name = ["game.ai.mode"], havingValue = "commander-gym")
    class LegacyDefaultMode
}
