package org.commandergym.argentum

import com.wingedsheep.ai.ActionResponse
import com.wingedsheep.ai.AiPlayerController
import com.wingedsheep.ai.llm.BottomCardsInfo
import com.wingedsheep.ai.llm.CardSummary
import com.wingedsheep.ai.llm.MulliganInfo
import com.wingedsheep.engine.core.DecisionResponse
import com.wingedsheep.engine.core.GameAction
import com.wingedsheep.engine.core.PendingDecision
import com.wingedsheep.engine.core.engineSerializersModule
import com.wingedsheep.engine.provenance.SemanticFingerprint
import com.wingedsheep.engine.view.ClientGameState
import com.wingedsheep.engine.view.LegalActionInfo
import com.wingedsheep.gameserver.ai.AiControllerContext
import com.wingedsheep.gameserver.ai.AiControllerProvider
import com.wingedsheep.engine.core.ActionParameterizer
import com.wingedsheep.engine.core.ActionParams
import com.wingedsheep.sdk.model.EntityId
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.*
import java.net.URI
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.time.Duration

private const val POLICY_SCHEMA_SCOPE = "commander-gym-game-server-policy-v1"

/** Commander Gym-owned implementation of vanilla Argentum's provider seam. */
class CommanderGymControllerProvider(
    private val endpoint: URI,
    private val token: String,
    private val timeout: Duration = Duration.ofSeconds(30),
) : AiControllerProvider {
    override val mode: String = "commander-gym"

    init {
        require(endpoint.scheme == "http" && endpoint.host in setOf("127.0.0.1", "localhost", "::1")) {
            "Commander Gym policy endpoint must be loopback HTTP"
        }
        require(endpoint.rawQuery == null && endpoint.rawFragment == null && endpoint.userInfo == null)
        require(token.isNotBlank()) { "Commander Gym policy token must not be blank" }
    }

    override fun create(context: AiControllerContext): AiPlayerController {
        // The policy process never receives or observes this trusted snapshot. The closure is kept
        // only at the native edge so Argentum's own ActionParameterizer can turn model-selected
        // ActionParams into the original GameAction template immediately before submission.
        val snapshotProvider = context.snapshot
        return CommanderGymPlayerController(
            playerId = context.playerId,
            endpoint = endpoint,
            token = token,
            timeout = timeout,
            parameterize = { action, params ->
                if (params.isEmpty) {
                    action
                } else {
                    val snapshot = snapshotProvider()
                        ?: error("Commander Gym action params require a live Argentum runtime snapshot")
                    ActionParameterizer.apply(action, params, snapshot.state)
                }
            },
        )
    }
}

class CommanderGymPlayerController(
    private val playerId: EntityId,
    endpoint: URI,
    token: String,
    timeout: Duration,
    private val parameterize: (GameAction, ActionParams) -> GameAction,
    private val http: HttpClient = HttpClient.newBuilder().connectTimeout(timeout).build(),
) : AiPlayerController {
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
    ): ActionResponse {
        val policyActions = JsonArray(legalActions.map { legal ->
            val encoded = json.encodeToJsonElement(legal).jsonObject
            buildJsonObject {
                encoded.forEach { (key, value) -> put(key, value) }
                put(
                    "semanticId",
                    SemanticFingerprint.forGameAction(
                        legal.actionType,
                        legal.action,
                        POLICY_SCHEMA_SCOPE,
                    ),
                )
            }
        })
        val policyDecision = pendingDecision?.let { pending ->
            val encoded = json.encodeToJsonElement(pending).jsonObject
            buildJsonObject {
                encoded.forEach { (key, value) -> put(key, value) }
                put(
                    "semanticId",
                    SemanticFingerprint.forPendingDecision(pending, POLICY_SCHEMA_SCOPE),
                )
            }
        } ?: JsonNull
        val body = buildJsonObject {
            put("playerId", playerId.value)
            put("state", json.encodeToJsonElement(state))
            put("legalActions", policyActions)
            put("pendingDecision", policyDecision)
            put("recentGameLog", json.encodeToJsonElement(recentGameLog))
        }
        val response = post("choose-action", body)
        return when (response.requiredString("kind")) {
            "action" -> {
                val index = response.requiredInt("actionId")
                val native = legalActions.getOrNull(index)
                    ?: error("Commander Gym returned a stale legal action index")
                val params = json.decodeFromJsonElement<ActionParams>(response.requiredObject("params"))
                ActionResponse.SubmitAction(parameterize(native.action, params))
            }
            "decision" -> {
                require(response.requiredString("playerId") == playerId.value) {
                    "Commander Gym returned a decision for another seat"
                }
                ActionResponse.SubmitDecision(
                    playerId,
                    json.decodeFromJsonElement<DecisionResponse>(response.requiredObject("response")),
                )
            }
            else -> error("Commander Gym returned an unknown response kind")
        }
    }

    override fun decideMulligan(mulliganMessage: MulliganInfo): Boolean {
        val response = post("decide-mulligan", buildJsonObject {
            put("playerId", playerId.value)
            put("mulligan", mulliganMessage.toJson())
        })
        return response["keep"]?.jsonPrimitive?.booleanOrNull
            ?: error("Commander Gym mulligan response is malformed")
    }

    override fun chooseBottomCards(message: BottomCardsInfo): List<EntityId> {
        val response = post("choose-bottom-cards", buildJsonObject {
            put("playerId", playerId.value)
            put("bottomCards", message.toJson())
        })
        val ids = response["cardIds"]?.jsonArray
            ?: error("Commander Gym bottom-card response is malformed")
        return ids.map { EntityId.of(it.jsonPrimitive.content) }
    }

    override fun setDeckList(deckList: Map<String, Int>, archetype: String?) {
        post("set-deck-list", buildJsonObject {
            put("playerId", playerId.value)
            put("deckList", buildJsonObject {
                deckList.forEach { (name, count) -> put(name, count) }
            })
            archetype?.let { put("archetype", it) }
        })
    }
    override fun chooseDraftPick(pack: List<CardSummary>, pickedSoFar: List<CardSummary>, packNumber: Int, pickNumber: Int, picksRequired: Int, passDirection: String): List<String> =
        error("Commander Gym game-server adapter does not support draft callbacks")
    override fun chooseWinstonAction(pileCards: List<CardSummary>, pileIndex: Int, pileSizes: List<Int>, pickedSoFar: List<CardSummary>): Boolean =
        error("Commander Gym game-server adapter does not support draft callbacks")
    override fun chooseGridDraftPick(grid: List<CardSummary?>, availableSelections: List<String>, pickedSoFar: List<CardSummary>): String =
        error("Commander Gym game-server adapter does not support draft callbacks")

    private fun post(path: String, body: JsonObject): JsonObject {
        val request = HttpRequest.newBuilder(URI.create("$base/v1/$path"))
            .timeout(requestTimeout)
            .header("Authorization", "Bearer $bearer")
            .header("Content-Type", "application/json")
            .POST(HttpRequest.BodyPublishers.ofString(json.encodeToString(body)))
            .build()
        val response = http.send(request, HttpResponse.BodyHandlers.ofString())
        require(response.statusCode() == 200) {
            val detail = runCatching {
                json.parseToJsonElement(response.body()).jsonObject["detail"]
                    ?.jsonPrimitive
                    ?.contentOrNull
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
