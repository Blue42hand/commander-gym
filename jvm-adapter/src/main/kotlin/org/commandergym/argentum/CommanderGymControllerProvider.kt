package org.commandergym.argentum

import com.wingedsheep.ai.ActionResponse
import com.wingedsheep.ai.AiPlayerController
import com.wingedsheep.ai.llm.BottomCardsInfo
import com.wingedsheep.ai.llm.CardSummary
import com.wingedsheep.ai.llm.MulliganInfo
import com.wingedsheep.engine.core.DecisionResponse
import com.wingedsheep.engine.core.PendingDecision
import com.wingedsheep.engine.core.engineSerializersModule
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
) : AiControllerProvider {
    override val mode: String = "commander-gym"
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
                displayName = profile.displayName,
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
            expectedDeck = selected?.deckList,
            http = http,
        )
        // Deliberately do not retain context or call context.snapshot().
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
        val body = buildJsonObject {
            putIdentity()
            put("state", json.encodeToJsonElement(state))
            put("legalActions", json.encodeToJsonElement(legalActions))
            put("pendingDecision", pendingDecision?.let(json::encodeToJsonElement) ?: JsonNull)
            put("recentGameLog", json.encodeToJsonElement(recentGameLog))
        }
        val response = post("choose-action", body)
        return when (response.requiredString("kind")) {
            "action" -> {
                val index = response.requiredInt("actionId")
                val native = legalActions.getOrNull(index)
                    ?: error("Commander Gym returned a stale legal action index")
                ActionResponse.SubmitAction(native.action)
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
            putIdentity()
            put("mulligan", mulliganMessage.toJson())
        })
        return response["keep"]?.jsonPrimitive?.booleanOrNull
            ?: error("Commander Gym mulligan response is malformed")
    }

    override fun chooseBottomCards(message: BottomCardsInfo): List<EntityId> {
        val response = post("choose-bottom-cards", buildJsonObject {
            putIdentity()
            put("bottomCards", message.toJson())
        })
        val ids = response["cardIds"]?.jsonArray
            ?: error("Commander Gym bottom-card response is malformed")
        return ids.map { EntityId.of(it.jsonPrimitive.content) }
    }

    override fun setDeckList(deckList: Map<String, Int>, archetype: String?) {
        val expected = expectedDeck ?: return
        require(deckList == expected) {
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
    }

    private fun post(path: String, body: JsonObject): JsonObject {
        val request = HttpRequest.newBuilder(URI.create("$base/v1/$path"))
            .timeout(requestTimeout)
            .header("Authorization", "Bearer $bearer")
            .header("Content-Type", "application/json")
            .POST(HttpRequest.BodyPublishers.ofString(json.encodeToString(body)))
            .build()
        val response = http.send(request, HttpResponse.BodyHandlers.ofString())
        require(response.statusCode() == 200) {
            "Commander Gym policy callback failed with HTTP ${response.statusCode()}"
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