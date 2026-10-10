package org.commandergym.argentum

import com.wingedsheep.ai.ActionResponse
import com.wingedsheep.ai.AiPlayerController
import com.wingedsheep.ai.llm.BottomCardsInfo
import com.wingedsheep.ai.llm.CardSummary
import com.wingedsheep.ai.llm.MulliganInfo
import com.wingedsheep.engine.core.DecisionResponse
import com.wingedsheep.engine.core.GameAction
import com.wingedsheep.engine.core.PendingDecision
import com.wingedsheep.engine.core.SelectManaSourcesDecision
import com.wingedsheep.engine.core.ActionParameterizer
import com.wingedsheep.engine.core.ActionParams
import com.wingedsheep.engine.core.responseSpec
import com.wingedsheep.engine.core.engineSerializersModule
import com.wingedsheep.engine.provenance.SemanticFingerprint
import com.wingedsheep.engine.view.ClientGameState
import com.wingedsheep.engine.view.LegalActionInfo
import com.wingedsheep.gameserver.ai.AiControllerContext
import com.wingedsheep.gameserver.ai.AiControllerProfile
import com.wingedsheep.gameserver.ai.AiControllerProvider
import com.wingedsheep.gameserver.lobby.AiDeckSpec
import com.wingedsheep.sdk.model.EntityId
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.*
import java.net.URI
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.time.Duration
import java.util.UUID

private const val POLICY_SCHEMA_SCOPE = "commander-gym-game-server-policy-v1"

private data class BindingProfileConfig(
    val id: String,
    val displayName: String,
    val description: String?,
    val deckList: Map<String, Int>,
    val deckLabel: String,
    val commander: String?,
)

/** Commander Gym-owned implementation of vanilla Argentum's provider seam. */
class CommanderGymControllerProvider(
    private val endpoint: URI,
    private val token: String,
    private val timeout: Duration = Duration.ofSeconds(30),
    private val http: HttpClient = HttpClient.newBuilder().connectTimeout(timeout).build(),
    private val requireHumanParticipant: Boolean = false,
    private val recorder: RecorderCallbackClient? = null,
) : AiControllerProvider {
    override val mode: String = "commander-gym"
    override val supportsDefaultController: Boolean get() = !requireHumanParticipant
    private val base: String
    private val profileConfigs: Map<String, BindingProfileConfig>
    override val profiles: List<AiControllerProfile>

    init {
        require(endpoint.scheme == "http" && endpoint.host in setOf("127.0.0.1", "localhost", "::1")) {
            "Commander Gym policy endpoint must be loopback HTTP"
        }
        require(endpoint.rawQuery == null && endpoint.rawFragment == null && endpoint.userInfo == null)
        require(token.isNotBlank()) { "Commander Gym policy token must not be blank" }
        base = endpoint.toString().trimEnd('/')

        val loaded = fetchProfiles()
        require(loaded.map { it.id }.toSet().size == loaded.size) {
            "Commander Gym sidecar advertised duplicate Binding profile ids"
        }
        profileConfigs = loaded.associateBy { it.id }
        profiles = loaded.map { profile ->
            AiControllerProfile(
                id = profile.id,
                displayName = if (requireHumanParticipant) "Luna — ${profile.displayName}" else profile.displayName,
                description = profile.description,
                deckSpec = AiDeckSpec.Fixed(
                    deckList = profile.deckList,
                    label = profile.deckLabel,
                    commander = profile.commander,
                ),
            )
        }
    }

    override fun create(context: AiControllerContext): AiPlayerController {
        require(context.profileId != null || supportsDefaultController) {
            "Commander Gym manual service requires an explicit Binding profile"
        }
        val selected = context.profileId?.let { profileId ->
            requireNotNull(profileConfigs[profileId]) {
                "Unknown Commander Gym Binding profile '$profileId'"
            }
        }
        return CommanderGymPlayerController(
            playerId = context.playerId,
            endpoint = endpoint,
            token = token,
            timeout = timeout,
            profileId = selected?.id,
            gameSessionId = context.gameSessionId,
            expectedDeck = selected?.deckList,
            expectedCommander = selected?.commander,
            parameterize = { offered, params ->
                if (params.isEmpty) offered.action else {
                    val snapshot = context.snapshot()
                        ?: error("Commander Gym action params require a live Argentum runtime snapshot")
                    ActionParameterizer.apply(offered, params, snapshot.state)
                }
            },
            http = http,
            beforeCallback = {
                require(!requireHumanParticipant ||
                    (context.gameSessionId != null && context.isManualHumanGame())) {
                    "Commander Gym manual service requires a native game with a human participant"
                }
            },
            manualHumanGame = requireHumanParticipant,
            recorder = recorder,
        )
        // Only the native edge can obtain the trusted snapshot. It never crosses HTTP.
    }

    private fun fetchProfiles(): List<BindingProfileConfig> {
        val request = HttpRequest.newBuilder(URI.create("$base/v1/controller-profiles"))
            .timeout(timeout)
            .header("Authorization", "Bearer $token")
            .GET()
            .build()
        val response = http.send(request, HttpResponse.BodyHandlers.ofString())
        require(response.statusCode() == 200) {
            "Commander Gym profile catalog failed with HTTP ${response.statusCode()}"
        }
        val root = Json.parseToJsonElement(response.body()).jsonObject
        val entries = root["profiles"]?.jsonArray
            ?: error("Commander Gym profile catalog is missing profiles")
        return entries.map { element ->
            val value = element.jsonObject
            val id = value.requiredString("id")
            val displayName = value.requiredString("displayName")
            val description = value["description"]?.jsonPrimitive?.contentOrNull
            val deck = value.requiredObject("deck")
            val cards = deck.requiredObject("cards").mapValues { (name, countElement) ->
                countElement.jsonPrimitive.intOrNull?.also { count ->
                    require(count > 0) { "Binding profile '$id' card '$name' must have a positive count" }
                } ?: error("Binding profile '$id' card '$name' count must be an integer")
            }
            require(cards.isNotEmpty()) { "Binding profile '$id' deck must not be empty" }
            BindingProfileConfig(
                id = id,
                displayName = displayName,
                description = description,
                deckList = cards,
                deckLabel = deck.requiredString("label"),
                commander = deck["commander"]?.jsonPrimitive?.contentOrNull,
            )
        }
    }
}

class CommanderGymPlayerController(
    private val playerId: EntityId,
    endpoint: URI,
    token: String,
    timeout: Duration,
    private val profileId: String? = null,
    private val expectedDeck: Map<String, Int>? = null,
    private val expectedCommander: String? = null,
    private val parameterize: (LegalActionInfo, ActionParams) -> GameAction = { offered, params ->
        require(params.isEmpty) { "Native action parameters require Argentum runtime context" }
        offered.action
    },
    private val http: HttpClient = HttpClient.newBuilder().connectTimeout(timeout).build(),
    private val gameSessionId: String? = null,
    private val beforeCallback: () -> Unit = {},
    private val manualHumanGame: Boolean = false,
    private val recorder: RecorderCallbackClient? = null,
) : com.wingedsheep.ai.RecordedAiPlayerController, com.wingedsheep.ai.RecordedAiCallbackController {
    override fun chooseRecordedAction(state: ClientGameState, legalActions: List<LegalActionInfo>,
                                      recentGameLog: List<String>, evidence: com.wingedsheep.ai.AiDecisionEvidence): ActionResponse =
        chooseActionWithPaymentError(state, legalActions, null, recentGameLog, null, evidence).response
    private val base = endpoint.toString().trimEnd('/')
    private val bearer = token
    private val requestTimeout = timeout
    private val json = Json {
        encodeDefaults = true
        ignoreUnknownKeys = false
        classDiscriminator = "type"
        serializersModule = engineSerializersModule
    }

    override fun chooseAction(
        state: ClientGameState,
        legalActions: List<LegalActionInfo>,
        pendingDecision: PendingDecision?,
        recentGameLog: List<String>,
    ): ActionResponse = chooseActionWithPaymentError(
        state, legalActions, pendingDecision, recentGameLog, null,
    ).response

    override fun chooseActionAfterRejectedPayment(
        state: ClientGameState,
        legalActions: List<LegalActionInfo>,
        pendingDecision: PendingDecision,
        recentGameLog: List<String>,
        nativePaymentError: String,
    ): ActionResponse = chooseActionWithPaymentError(
        state, legalActions, pendingDecision, recentGameLog, nativePaymentError,
    ).response

    override fun chooseRecordedCallback(state: ClientGameState, legalActions: List<LegalActionInfo>,
        pendingDecision: PendingDecision?, recentGameLog: List<String>,
        evidence: com.wingedsheep.ai.AiDecisionEvidence, nativePaymentError: String?): com.wingedsheep.ai.RecordedAiChoice =
        chooseActionWithPaymentError(state, legalActions, pendingDecision, recentGameLog, nativePaymentError, evidence)

    private fun chooseActionWithPaymentError(
        state: ClientGameState,
        legalActions: List<LegalActionInfo>,
        pendingDecision: PendingDecision?,
        recentGameLog: List<String>,
        nativePaymentError: String?,
        evidence: com.wingedsheep.ai.AiDecisionEvidence? = null,
    ): com.wingedsheep.ai.RecordedAiChoice {
        if (nativePaymentError != null) {
            require(nativePaymentError.isNotBlank()) { "Native payment error must not be blank" }
            require(pendingDecision is SelectManaSourcesDecision) {
                "Payment correction requires a fresh native mana-source decision"
            }
        }
        val policyActions = JsonArray(legalActions.map { legal ->
            val encoded = json.encodeToJsonElement(legal).jsonObject
            buildJsonObject {
                encoded.forEach { (key, value) -> put(key, value) }
                put("semanticId", SemanticFingerprint.forGameAction(
                    legal.actionType, legal.action, POLICY_SCHEMA_SCOPE,
                ))
            }
        })
        val policyDecision = pendingDecision?.let { pending ->
            val encoded = json.encodeToJsonElement(pending).jsonObject
            buildJsonObject {
                encoded.forEach { (key, value) -> put(key, value) }
                put("semanticId", SemanticFingerprint.forPendingDecision(pending, POLICY_SCHEMA_SCOPE))
                put("responseSpec", json.encodeToJsonElement(pending.responseSpec()))
            }
        } ?: JsonNull
        val body = buildJsonObject {
            putIdentity()
            put("state", json.encodeToJsonElement(state))
            put("legalActions", policyActions)
            put("pendingDecision", policyDecision)
            put("recentGameLog", json.encodeToJsonElement(recentGameLog))
            if (evidence != null) put("decisionEvidence", json.encodeToJsonElement(com.wingedsheep.ai.AiDecisionEvidence.serializer(), evidence))
            if (nativePaymentError != null) put("nativePaymentError", nativePaymentError)
        }
        val response = post("choose-action", body)
        return when (response.requiredString("kind")) {
            "action" -> {
                val index = response.requiredInt("actionId")
                val native = legalActions.getOrNull(index)
                    ?: error("Commander Gym returned a stale legal action index")
                val params = json.decodeFromJsonElement<ActionParams>(response.requiredObject("params"))
                com.wingedsheep.ai.RecordedAiChoice(ActionResponse.SubmitAction(parameterize(native, params)),
                    index, response.requiredObject("params"))
            }
            "decision" -> {
                require(response.requiredString("playerId") == playerId.value) {
                    "Commander Gym returned a decision for another seat"
                }
                com.wingedsheep.ai.RecordedAiChoice(ActionResponse.SubmitDecision(
                    playerId,
                    json.decodeFromJsonElement<DecisionResponse>(response.requiredObject("response")),
                ))
            }
            else -> error("Commander Gym returned an unknown response kind")
        }
    }

    override fun decideMulligan(mulliganMessage: MulliganInfo): Boolean = mulligan(mulliganMessage, null)
    override fun decideRecordedMulligan(info: MulliganInfo, evidence: com.wingedsheep.ai.AiDecisionEvidence): Boolean = mulligan(info, evidence)
    private fun mulligan(mulliganMessage: MulliganInfo, evidence: com.wingedsheep.ai.AiDecisionEvidence?): Boolean {
        val response = post("decide-mulligan", buildJsonObject {
            putIdentity()
            put("mulligan", mulliganMessage.toJson())
            if (evidence != null) put("decisionEvidence", json.encodeToJsonElement(com.wingedsheep.ai.AiDecisionEvidence.serializer(), evidence))
        })
        return response["keep"]?.jsonPrimitive?.booleanOrNull
            ?: error("Commander Gym mulligan response is malformed")
    }

    override fun chooseBottomCards(message: BottomCardsInfo): List<EntityId> = bottom(message, null)
    override fun chooseRecordedBottomCards(info: BottomCardsInfo, evidence: com.wingedsheep.ai.AiDecisionEvidence): List<EntityId> = bottom(info, evidence)
    private fun bottom(message: BottomCardsInfo, evidence: com.wingedsheep.ai.AiDecisionEvidence?): List<EntityId> {
        val response = post("choose-bottom-cards", buildJsonObject {
            putIdentity()
            put("bottomCards", message.toJson())
            if (evidence != null) put("decisionEvidence", json.encodeToJsonElement(com.wingedsheep.ai.AiDecisionEvidence.serializer(), evidence))
        })
        val ids = response["cardIds"]?.jsonArray
            ?: error("Commander Gym bottom-card response is malformed")
        return ids.map { EntityId.of(it.jsonPrimitive.content) }
    }

    override fun setDeckList(deckList: Map<String, Int>, archetype: String?) {
        val expected = expectedDeck ?: return
        val submitted = expectedCommander?.let { commander ->
            expected + (commander to (expected[commander] ?: 0) + 1)
        }
        require(deckList == expected || deckList == submitted) {
            "Argentum delivered a deck that does not match Commander Gym Binding profile '$profileId'"
        }
    }

    override fun chooseDraftPick(pack: List<CardSummary>, pickedSoFar: List<CardSummary>, packNumber: Int, pickNumber: Int, picksRequired: Int, passDirection: String): List<String> =
        error("Commander Gym game-server adapter does not support draft callbacks")
    override fun chooseWinstonAction(pileCards: List<CardSummary>, pileIndex: Int, pileSizes: List<Int>, pickedSoFar: List<CardSummary>): Boolean =
        error("Commander Gym game-server adapter does not support draft callbacks")
    override fun chooseGridDraftPick(grid: List<CardSummary?>, availableSelections: List<String>, pickedSoFar: List<CardSummary>): String =
        error("Commander Gym game-server adapter does not support draft callbacks")

    private fun JsonObjectBuilder.putIdentity() {
        put("playerId", playerId.value)
        profileId?.let { put("profileId", it) }
        gameSessionId?.let { put("gameSessionId", it) }
        if (manualHumanGame) put("manualHumanGame", true)
    }

    private fun post(path: String, body: JsonObject): JsonObject {
        beforeCallback()
        val callbackId = if (recorder == null) null else UUID.randomUUID().toString()
        val envelope = callbackId?.let { id ->
            require(manualHumanGame && gameSessionId != null && profileId != null) { "Recorder requires native manual Binding game" }
            buildJsonObject {
                put("protocol", 1)
                put("callbackId", id)
                put("gameId", gameSessionId)
                put("seatId", playerId.value)
                put("bindingId", profileId)
                put("callback", when (path) {
                    "choose-action" -> "chooseAction"
                    "decide-mulligan" -> "decideMulligan"
                    "choose-bottom-cards" -> "chooseBottomCards"
                    else -> error("Unsupported recorded callback")
                })
            }
        }
        if (envelope != null) recorder!!.exchange(buildJsonObject {
            envelope.forEach { (key, value) -> put(key, value) }
            put("op", "register")
            put("request", body)
        })
        val builder = HttpRequest.newBuilder(URI.create("$base/v1/$path"))
            .timeout(requestTimeout)
            .header("Authorization", "Bearer $bearer")
            .header("Content-Type", "application/json")
            .POST(HttpRequest.BodyPublishers.ofString(json.encodeToString(body)))

        if (callbackId != null) builder.header("X-Commander-Gym-Callback-Id", callbackId)
        val response = try {
            http.send(builder.build(), HttpResponse.BodyHandlers.ofString())
        } finally {
            if (envelope != null) recorder!!.exchange(buildJsonObject {
                envelope.forEach { (key, value) -> put(key, value) }
                put("op", "closed")
            })
        }
        require(response.statusCode() == 200) {
            val detail = runCatching {
                json.parseToJsonElement(response.body()).jsonObject["detail"]
                    ?.jsonPrimitive?.contentOrNull
            }.getOrNull()
            buildString {
                append("Commander Gym policy callback failed with HTTP ")
                append(response.statusCode())
                if (!detail.isNullOrBlank()) {
                    append(": ")
                    append(detail.take(1200))
                }
            }
        }
        return json.parseToJsonElement(response.body()).jsonObject
    }
}

private fun MulliganInfo.toJson() = buildJsonObject {
    put("hand", JsonArray(hand.map { JsonPrimitive(it.value) }))
    put("mulliganCount", mulliganCount)
    put("cardsToPutOnBottom", cardsToPutOnBottom)
    put("isOnThePlay", isOnThePlay)
    put("cards", cards.toCardJson())
}

private fun BottomCardsInfo.toJson() = buildJsonObject {
    put("hand", JsonArray(hand.map { JsonPrimitive(it.value) }))
    put("cardsToPutOnBottom", cardsToPutOnBottom)
    put("cards", cards.toCardJson())
}

private fun Map<EntityId, CardSummary>.toCardJson() = buildJsonObject {
    forEach { (id, card) ->
        put(id.value, buildJsonObject {
            put("name", card.name)
            card.manaCost?.let { put("manaCost", it) }
            card.typeLine?.let { put("typeLine", it) }
            card.oracleText?.let { put("oracleText", it) }
            card.power?.let { put("power", it) }
            card.toughness?.let { put("toughness", it) }
        })
    }
}

private fun JsonObject.requiredString(name: String) = this[name]?.jsonPrimitive?.contentOrNull
    ?: error("Commander Gym response is missing $name")
private fun JsonObject.requiredInt(name: String) = this[name]?.jsonPrimitive?.intOrNull
    ?: error("Commander Gym response is missing $name")
private fun JsonObject.requiredObject(name: String) = this[name]?.jsonObject
    ?: error("Commander Gym response is missing $name")
