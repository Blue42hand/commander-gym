package org.commandergym.argentum

import com.sun.net.httpserver.HttpServer
import com.wingedsheep.ai.ActionResponse
import com.wingedsheep.engine.core.DeclareAttackers
import com.wingedsheep.engine.core.DeclareBlockers
import com.wingedsheep.engine.core.YesNoDecision
import com.wingedsheep.engine.core.SelectManaSourcesDecision
import com.wingedsheep.engine.core.DecisionContext
import com.wingedsheep.engine.view.ClientGameState
import com.wingedsheep.engine.view.LegalActionInfo
import com.wingedsheep.engine.core.ActionParams
import com.wingedsheep.engine.core.ActionParameterFieldKind
import com.wingedsheep.engine.core.ActionParameterSpec
import com.wingedsheep.engine.provenance.SemanticFingerprint
import com.wingedsheep.sdk.core.Phase
import com.wingedsheep.sdk.core.Step
import com.wingedsheep.sdk.model.EntityId
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Assertions.assertThrows
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
    fun exactProfileAcceptsLibraryOrSubmittedCommanderListOnly() {
        val controller = CommanderGymPlayerController(
            playerId = EntityId.of("ai"),
            endpoint = URI.create("http://127.0.0.1:8083"),
            token = "test-token",
            timeout = Duration.ofSeconds(2),
            profileId = "krenko-foundation-openai",
            expectedDeck = mapOf("Mountain" to 99),
            expectedCommander = "Krenko, Mob Boss",
        )
        controller.setDeckList(mapOf("Mountain" to 99), null)
        controller.setDeckList(mapOf("Mountain" to 99, "Krenko, Mob Boss" to 1), null)
        assertThrows(IllegalArgumentException::class.java) {
            controller.setDeckList(mapOf("Mountain" to 98, "Krenko, Mob Boss" to 1), null)
        }
    }

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
            parameterSpec = ActionParameterSpec(mapOf("attackers" to ActionParameterFieldKind.ENTITY_ID_MAP)),
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
                parameterize = { offered, params ->
                    seenParams = params
                    val attack = offered.action as DeclareAttackers
                    attack.copy(attackers = params.attackers)
                },
            )

            val evidence = com.wingedsheep.gameserver.recording.aiSeatEvidence(minimalState(playerId), listOf(legal), playerId.value)
            val response = controller.chooseRecordedAction(minimalState(playerId), listOf(legal), emptyList(), evidence)

            assertTrue(response is ActionResponse.SubmitAction)
            val submitted = (response as ActionResponse.SubmitAction).action as DeclareAttackers
            assertEquals(mapOf(attacker to defender), seenParams!!.attackers)
            assertEquals(mapOf(attacker to defender), submitted.attackers)

            val policyRequest = Json.parseToJsonElement(checkNotNull(requestBody)).jsonObject
            assertEquals(evidence.correlationId, policyRequest["decisionEvidence"]!!.jsonObject["correlationId"]!!.jsonPrimitive.content)
            assertEquals(evidence.observationBody, policyRequest["decisionEvidence"]!!.jsonObject["observationBody"]!!.jsonPrimitive.content)
            assertTrue(!policyRequest["state"].toString().contains(evidence.correlationId))
            val policyAction = policyRequest["legalActions"]!!.jsonArray[0].jsonObject
            val semanticId = policyAction["semanticId"]!!.jsonPrimitive.content
            assertEquals(
                SemanticFingerprint.forGameAction(
                    "DeclareAttackers",
                    template,
                    "commander-gym-game-server-policy-v1",
                ),
                semanticId,
            )
            assertEquals(
                "ENTITY_ID_MAP",
                policyAction["parameterSpec"]!!.jsonObject["allowedFields"]!!
                    .jsonObject["attackers"]!!.jsonPrimitive.content,
            )
        } finally {
            server.stop(0)
        }
    }

    @Test
    fun nativeBlockPairsReachTheGymPolicyWithoutFlatteningGlobalBlockRules() {
        val playerId = EntityId.of("ai")
        val drake = EntityId.of("drake")
        val bear = EntityId.of("bear")
        val piledriver = EntityId.of("piledriver")
        val legal = LegalActionInfo(
            actionType = "DeclareBlockers",
            description = "Declare blockers",
            action = DeclareBlockers(playerId, emptyMap()),
            validBlockers = listOf(drake),
            validBlockTargets = mapOf(drake to listOf(bear)),
            blockerMaxBlockCounts = mapOf(drake to 1),
            maxTotalBlockers = 1,
            parameterSpec = ActionParameterSpec(
                mapOf("blockers" to ActionParameterFieldKind.ENTITY_ID_ARRAY_MAP),
            ),
        )
        var requestBody: String? = null
        val server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        server.createContext("/v1/choose-action") { exchange ->
            requestBody = exchange.requestBody.bufferedReader().use { it.readText() }
            val response = """{"kind":"action","actionId":0,"action":
                {"type":"DeclareBlockers","playerId":"ai","blockers":{}},
                "params":{"blockers":{"drake":["bear"]}},"metadata":{}}"""
                .trimIndent().toByteArray(StandardCharsets.UTF_8)
            exchange.sendResponseHeaders(200, response.size.toLong())
            exchange.responseBody.use { it.write(response) }
        }
        server.start()
        try {
            val controller = CommanderGymPlayerController(
                playerId = playerId,
                endpoint = URI.create("http://127.0.0.1:${server.address.port}"),
                token = "test-token",
                timeout = Duration.ofSeconds(2),
                parameterize = { offered, params ->
                    (offered.action as DeclareBlockers).copy(blockers = params.blockers)
                },
            )
            val chosen = controller.chooseAction(
                state = minimalState(playerId), legalActions = listOf(legal),
                pendingDecision = null, recentGameLog = emptyList(),
            ) as ActionResponse.SubmitAction
            assertEquals(mapOf(drake to listOf(bear)), (chosen.action as DeclareBlockers).blockers)
            val offer = Json.parseToJsonElement(checkNotNull(requestBody)).jsonObject
                .getValue("legalActions").jsonArray.single().jsonObject
            val pairs = offer.getValue("validBlockTargets").jsonObject
            assertEquals(listOf("bear"), pairs.getValue("drake").jsonArray.map { it.jsonPrimitive.content })
            assertTrue(piledriver.value !in pairs.getValue("drake").toString())
            assertEquals("1", offer.getValue("blockerMaxBlockCounts").jsonObject
                .getValue("drake").jsonPrimitive.content)
            assertEquals(1, offer.getValue("maxTotalBlockers").jsonPrimitive.content.toInt())
        } finally {
            server.stop(0)
        }
    }

    @Test
    fun pendingDecisionCarriesArgentumOwnedNativeResponseContract() {
        val playerId = EntityId.of("ai")
        val pending = YesNoDecision(
            id = "r1",
            playerId = playerId,
            prompt = "You may draw a card",
            context = DecisionContext(),
        )

        var requestBody: String? = null
        val server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        server.createContext("/v1/choose-action") { exchange ->
            requestBody = exchange.requestBody.bufferedReader().use { it.readText() }
            val response = """
                {
                  "kind":"decision",
                  "playerId":"ai",
                  "response":{"type":"YesNoResponse","decisionId":"r1","choice":true},
                  "metadata":{"provider":"test"}
                }
            """.trimIndent().toByteArray(StandardCharsets.UTF_8)
            exchange.sendResponseHeaders(200, response.size.toLong())
            exchange.responseBody.use { it.write(response) }
        }
        server.start()

        try {
            val controller = CommanderGymPlayerController(
                playerId = playerId,
                endpoint = URI.create("http://127.0.0.1:${server.address.port}"),
                token = "test-token",
                timeout = Duration.ofSeconds(2),
                parameterize = { offered, _ -> offered.action },
            )

            val response = controller.chooseAction(
                state = minimalState(playerId),
                legalActions = emptyList(),
                pendingDecision = pending,
                recentGameLog = emptyList(),
            )
            assertTrue(response is ActionResponse.SubmitDecision)

            val policyRequest = Json.parseToJsonElement(checkNotNull(requestBody)).jsonObject
            val responseSpec = policyRequest["pendingDecision"]!!
                .jsonObject["responseSpec"]!!.jsonObject
            assertEquals("YesNoResponse", responseSpec["responseType"]!!.jsonPrimitive.content)
            assertEquals(
                "BOOLEAN",
                responseSpec["requiredFields"]!!.jsonObject["choice"]!!.jsonPrimitive.content,
            )
            assertEquals(
                false,
                responseSpec["cancelAllowed"]!!.jsonPrimitive.content.toBooleanStrict(),
            )
        } finally {
            server.stop(0)
        }
    }

    @Test
    fun nativeAutoPayFeasibilityReachesGymPolicyCallback() {
        val playerId = EntityId.of("ai")
        val pending = SelectManaSourcesDecision(
            id = "payment-1", playerId = playerId, prompt = "Produce mana for Krenko, Mob Boss",
            context = DecisionContext(), availableSources = emptyList(),
            requiredCost = "{2}{R}{R}", autoPaySuggestion = emptyList(),
            canAutoPayNow = false, canDecline = true,
        )
        var requestBody: String? = null
        val server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        server.createContext("/v1/choose-action") { exchange ->
            requestBody = exchange.requestBody.bufferedReader().use { it.readText() }
            val response = """
                {"kind":"decision","playerId":"ai",
                 "response":{"type":"ManaSourcesSelectedResponse","decisionId":"payment-1",
                             "selectedSources":[],"autoPay":false,"declined":true,
                             "waterbendPermanents":[]},"metadata":{"provider":"test"}}
            """.trimIndent().toByteArray(StandardCharsets.UTF_8)
            exchange.sendResponseHeaders(200, response.size.toLong())
            exchange.responseBody.use { it.write(response) }
        }
        server.start()
        try {
            val controller = CommanderGymPlayerController(
                playerId = playerId,
                endpoint = URI.create("http://127.0.0.1:${server.address.port}"),
                token = "test-token", timeout = Duration.ofSeconds(2),
                parameterize = { offered, _ -> offered.action },
            )
            assertTrue(controller.chooseAction(
                state = minimalState(playerId), legalActions = emptyList(),
                pendingDecision = pending, recentGameLog = emptyList(),
            ) is ActionResponse.SubmitDecision)
            val policyPending = Json.parseToJsonElement(checkNotNull(requestBody))
                .jsonObject["pendingDecision"]!!.jsonObject
            assertEquals(false, policyPending["canAutoPayNow"]!!.jsonPrimitive.content.toBooleanStrict())
            assertEquals("{2}{R}{R}", policyPending["requiredCost"]!!.jsonPrimitive.content)
            assertTrue("nativePaymentError" !in Json.parseToJsonElement(checkNotNull(requestBody)).jsonObject)

            assertTrue(controller.chooseActionAfterRejectedPayment(
                state = minimalState(playerId), legalActions = emptyList(),
                pendingDecision = pending, recentGameLog = listOf("Current native payment window"),
                nativePaymentError = "Selected mana sources cannot pay this spell's cost",
            ) is ActionResponse.SubmitDecision)
            val correction = Json.parseToJsonElement(checkNotNull(requestBody)).jsonObject
            assertEquals("Selected mana sources cannot pay this spell's cost",
                correction["nativePaymentError"]!!.jsonPrimitive.content)
            assertEquals("payment-1", correction["pendingDecision"]!!.jsonObject["id"]!!.jsonPrimitive.content)
            assertEquals("ai", correction["state"]!!.jsonObject["viewingPlayerId"]!!.jsonPrimitive.content)
            assertTrue("snapshot" !in correction)
            assertThrows(IllegalArgumentException::class.java) {
                controller.chooseActionAfterRejectedPayment(
                    state = minimalState(playerId), legalActions = emptyList(),
                    pendingDecision = pending, recentGameLog = emptyList(), nativePaymentError = " ",
                )
            }
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
