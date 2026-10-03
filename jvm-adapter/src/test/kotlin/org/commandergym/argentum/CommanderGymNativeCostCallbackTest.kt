package org.commandergym.argentum

import com.sun.net.httpserver.HttpServer
import com.wingedsheep.ai.ActionResponse
import com.wingedsheep.engine.core.ActionParameterizer
import com.wingedsheep.engine.core.ActionParams
import com.wingedsheep.engine.core.ActivateAbility
import com.wingedsheep.engine.state.GameState
import com.wingedsheep.engine.legalactions.AdditionalCostData
import com.wingedsheep.engine.legalactions.LegalAction
import com.wingedsheep.engine.mechanics.mana.ManaSolver
import com.wingedsheep.engine.registry.CardRegistry
import com.wingedsheep.engine.view.ClientGameState
import com.wingedsheep.engine.view.LegalActionEnricher
import com.wingedsheep.sdk.core.Phase
import com.wingedsheep.sdk.core.Step
import com.wingedsheep.sdk.model.EntityId
import com.wingedsheep.sdk.scripting.AbilityId
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Test
import java.net.InetSocketAddress
import java.net.URI
import java.nio.charset.StandardCharsets
import java.time.Duration

class CommanderGymNativeCostCallbackTest {
    @Test
    fun nativeCostChoicesCrossTheMaskedCallbackAndRejectUnofferedIds() {
        val player = EntityId.of("ai")
        val offeredId = EntityId.of("own-candidate")
        val hiddenId = EntityId.of("opponent-secret")
        val fields = listOf(
            "tappedPermanents", "sacrificedPermanents", "discardedCards", "exiledCards",
        )
        val registry = CardRegistry()
        val enricher = LegalActionEnricher(ManaSolver(registry), registry)
        var selectedField = fields.first()
        var requestBody = ""
        val server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        server.createContext("/v1/choose-action") { exchange ->
            requestBody = exchange.requestBody.bufferedReader().use { it.readText() }
            val response = """{"kind":"action","actionId":0,"params":{"$selectedField":["own-candidate"]}}"""
                .toByteArray(StandardCharsets.UTF_8)
            exchange.sendResponseHeaders(200, response.size.toLong())
            exchange.responseBody.use { it.write(response) }
        }
        server.start()
        try {
            val controller = CommanderGymPlayerController(
                playerId = player,
                endpoint = URI.create("http://127.0.0.1:${server.address.port}"),
                token = "test-token",
                timeout = Duration.ofSeconds(2),
                parameterize = { offered, params ->
                    ActionParameterizer.apply(offered, params, GameState())
                },
            )
            for (field in fields) {
                selectedField = field
                val cost = AdditionalCostData(
                    description = "Choose a native additional cost",
                    costType = field,
                    validTapTargets = if (field == "tappedPermanents") listOf(offeredId) else emptyList(),
                    validSacrificeTargets = if (field == "sacrificedPermanents") listOf(offeredId) else emptyList(),
                    validDiscardTargets = if (field == "discardedCards") listOf(offeredId) else emptyList(),
                    validExileTargets = if (field == "exiledCards") listOf(offeredId) else emptyList(),
                )
                val nativeOffer = LegalAction(
                    actionType = "ActivateAbility",
                    description = "Pay a native additional cost",
                    action = ActivateAbility(player, EntityId.of("source"), AbilityId("ability")),
                    additionalCostInfo = cost,
                )
                val offered = enricher.enrich(listOf(nativeOffer), GameState(), player).single()
                val response = controller.chooseAction(
                    state = minimalState(player), legalActions = listOf(offered),
                    pendingDecision = null, recentGameLog = emptyList(),
                ) as ActionResponse.SubmitAction
                val paid = (response.action as ActivateAbility).costPayment!!
                val selected = when (field) {
                    "tappedPermanents" -> paid.tappedPermanents
                    "sacrificedPermanents" -> paid.sacrificedPermanents
                    "discardedCards" -> paid.discardedCards
                    else -> paid.exiledCards
                }
                assertEquals(listOf(offeredId), selected, field)

                val policyAction = Json.parseToJsonElement(requestBody).jsonObject
                    .getValue("legalActions").jsonArray.single().jsonObject
                assertEquals(
                    "ENTITY_ID_ARRAY",
                    policyAction.getValue("parameterSpec").jsonObject
                        .getValue("allowedFields").jsonObject.getValue(field).jsonPrimitive.content,
                )
                val otherCostFields = fields - field
                val allowed = policyAction.getValue("parameterSpec").jsonObject
                    .getValue("allowedFields").jsonObject
                assertEquals(emptySet<String>(), otherCostFields.filter { it in allowed }.toSet())
                assertEquals(
                    listOf("own-candidate"),
                    policyAction.getValue("additionalCostInfo").jsonObject
                        .getValue(when (field) {
                            "tappedPermanents" -> "validTapTargets"
                            "sacrificedPermanents" -> "validSacrificeTargets"
                            "discardedCards" -> "validDiscardTargets"
                            else -> "validExileTargets"
                        }).jsonArray.map { it.jsonPrimitive.content },
                )
                assertEquals(false, requestBody.contains(hiddenId.value))
                val invalid = when (field) {
                    "tappedPermanents" -> ActionParams(tappedPermanents = listOf(hiddenId))
                    "sacrificedPermanents" -> ActionParams(sacrificedPermanents = listOf(hiddenId))
                    "discardedCards" -> ActionParams(discardedCards = listOf(hiddenId))
                    else -> ActionParams(exiledCards = listOf(hiddenId))
                }
                assertThrows(IllegalArgumentException::class.java) {
                    ActionParameterizer.apply(offered, invalid, GameState())
                }
                val unofferedField = if (field == "tappedPermanents") {
                    ActionParams(discardedCards = listOf(offeredId))
                } else {
                    ActionParams(tappedPermanents = listOf(offeredId))
                }
                assertThrows(IllegalArgumentException::class.java) {
                    ActionParameterizer.apply(offered, unofferedField, GameState())
                }
            }
        } finally {
            server.stop(0)
        }
    }

    private fun minimalState(player: EntityId) = ClientGameState(
        viewingPlayerId = player, cards = emptyMap(), zones = emptyList(), players = emptyList(),
        currentPhase = Phase.PRECOMBAT_MAIN, currentStep = Step.PRECOMBAT_MAIN,
        activePlayerId = player, priorityPlayerId = player, turnNumber = 1,
        isGameOver = false, winnerId = null, combat = null,
    )
}
