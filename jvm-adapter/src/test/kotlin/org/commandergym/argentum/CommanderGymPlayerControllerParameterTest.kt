package org.commandergym.argentum

import com.sun.net.httpserver.HttpServer
import com.wingedsheep.ai.ActionResponse
import com.wingedsheep.engine.core.DeclareAttackers
import com.wingedsheep.engine.view.ClientGameState
import com.wingedsheep.engine.view.LegalActionInfo
import com.wingedsheep.engine.core.ActionParams
import com.wingedsheep.sdk.core.Phase
import com.wingedsheep.sdk.core.Step
import com.wingedsheep.sdk.model.EntityId
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import java.net.InetSocketAddress
import java.net.URI
import java.nio.charset.StandardCharsets
import java.time.Duration

class CommanderGymPlayerControllerParameterTest {
    @Test
    fun sidecarActionParamsAreDecodedAndAppliedToTheOriginalNativeTemplate() {
        val playerId = EntityId.of("ai")
        val attacker = EntityId.of("attacker-1")
        val defender = EntityId.of("defender-1")
        val template = DeclareAttackers(playerId, emptyMap())
        val legal = LegalActionInfo(
            actionType = "DeclareAttackers",
            description = "Declare attackers",
            action = template,
            validAttackers = listOf(attacker),
            validAttackTargets = listOf(defender),
        )

        val server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        server.createContext("/v1/choose-action") { exchange ->
            val response = """
                {
                  "kind":"action",
                  "actionId":0,
                  "action":{"type":"DeclareAttackers","playerId":"ai","attackers":{}},
                  "params":{"attackers":{"attacker-1":"defender-1"}},
                  "metadata":{"provider":"test"}
                }
            """.trimIndent().toByteArray(StandardCharsets.UTF_8)
            exchange.sendResponseHeaders(200, response.size.toLong())
            exchange.responseBody.use { it.write(response) }
        }
        server.start()

        var seenParams: ActionParams? = null
        try {
            val controller = CommanderGymPlayerController(
                playerId = playerId,
                endpoint = URI.create("http://127.0.0.1:${server.address.port}"),
                token = "test-token",
                timeout = Duration.ofSeconds(2),
                parameterize = { action, params ->
                    seenParams = params
                    val attack = action as DeclareAttackers
                    attack.copy(attackers = params.attackers)
                },
            )

            val response = controller.chooseAction(
                state = minimalState(playerId),
                legalActions = listOf(legal),
                pendingDecision = null,
                recentGameLog = emptyList(),
            )

            assertTrue(response is ActionResponse.SubmitAction)
            val submitted = (response as ActionResponse.SubmitAction).action as DeclareAttackers
            assertEquals(mapOf(attacker to defender), seenParams!!.attackers)
            assertEquals(mapOf(attacker to defender), submitted.attackers)
        } finally {
            server.stop(0)
        }
    }

    private fun minimalState(playerId: EntityId) = ClientGameState(
        viewingPlayerId = playerId,
        cards = emptyMap(),
        zones = emptyList(),
        players = emptyList(),
        currentPhase = Phase.COMBAT,
        currentStep = Step.DECLARE_ATTACKERS,
        activePlayerId = playerId,
        priorityPlayerId = playerId,
        turnNumber = 1,
        isGameOver = false,
        winnerId = null,
        combat = null,
    )
}
