package org.commandergym.argentum

import com.sun.net.httpserver.HttpServer
import com.wingedsheep.ai.ActionResponse
import com.wingedsheep.engine.core.DeclareAttackers
import com.wingedsheep.engine.view.ClientGameState
import com.wingedsheep.engine.view.LegalActionInfo
import com.wingedsheep.engine.core.ActionParams
import com.wingedsheep.engine.provenance.SemanticFingerprint
import com.wingedsheep.sdk.core.Phase
import com.wingedsheep.sdk.core.Step
import com.wingedsheep.sdk.model.EntityId
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertTrue
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
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

        var requestBody: String? = null
        val server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        server.createContext("/v1/choose-action") { exchange ->
            requestBody = exchange.requestBody.bufferedReader().use { it.readText() }
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

            val policyRequest = Json.parseToJsonElement(checkNotNull(requestBody)).jsonObject
            val semanticId = policyRequest["legalActions"]!!.jsonArray[0]
                .jsonObject["semanticId"]!!.jsonPrimitive.content
            assertEquals(
                SemanticFingerprint.forGameAction(
                    "DeclareAttackers",
                    template,
                    "commander-gym-game-server-policy-v1",
                ),
                semanticId,
            )
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
