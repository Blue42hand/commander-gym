package org.commandergym.argentum

import com.wingedsheep.ai.ActionResponse
import com.wingedsheep.ai.AiDecisionEvidence
import com.wingedsheep.ai.llm.BottomCardsInfo
import com.wingedsheep.ai.llm.CardSummary
import com.wingedsheep.engine.core.*
import com.wingedsheep.engine.provenance.SemanticFingerprint
import com.wingedsheep.engine.view.ClientGameState
import com.wingedsheep.engine.view.LegalActionInfo
import com.wingedsheep.gameserver.ai.AiControllerContext
import com.wingedsheep.sdk.model.EntityId
import kotlinx.serialization.json.*
import java.net.URI
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.time.Duration

/**
 * Wrapper around CommanderGymPlayerController with optional replay-safe state caching.
 * 
 * Caching is opt-in and only caches seat-visible information to prevent opponent hidden data leakage.
 * Default behavior (caching disabled) matches the original controller exactly.
 */
class CachingCommanderGymPlayerController(
    playerId: EntityId,
    endpoint: URI,
    token: String,
    timeout: Duration,
    profileId: String? = null,
    expectedDeck: Map<String, Int>? = null,
    expectedCommander: String? = null,
    parameterize: (LegalActionInfo, ActionParams) -> GameAction = { offered, params ->
        require(params.isEmpty) { "Native action parameters require Argentum runtime context" }
        offered.action
    },
    http: HttpClient = HttpClient.newBuilder().connectTimeout(timeout).build(),
    gameSessionId: String? = null,
    beforeCallback: () -> Unit = {},
    manualHumanGame: Boolean = false,
    recorder: RecorderCallbackClient? = null,
    /** Enable replay-safe state caching (default OFF for safety - opt-in only) */
    enableStateCaching: Boolean = false,
    /** Maximum cache size for observations */
    maxCacheSize: Int = 1024,
) : CommanderGymPlayerController(
    playerId = playerId,
    endpoint = endpoint,
    token = token,
    timeout = timeout,
    profileId = profileId,
    expectedDeck = expectedDeck,
    expectedCommander = expectedCommander,
    parameterize = parameterize,
    http = http,
    gameSessionId = gameSessionId,
    beforeCallback = beforeCallback,
    manualHumanGame = manualHumanGame,
    recorder = recorder,
) {

    init {
        require(!enableStateCaching || maxCacheSize > 0) { "maxCacheSize must be positive when caching is enabled" }
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
        evidence: AiDecisionEvidence, nativePaymentError: String?): RecordedAiChoice =
        chooseActionWithPaymentError(state, legalActions, pendingDecision, recentGameLog, nativePaymentError, evidence)

    private fun chooseActionWithPaymentError(
        state: ClientGameState,
        legalActions: List<LegalActionInfo>,
        pendingDecision: PendingDecision?,
        recentGameLog: List<String>,
        nativePaymentError: String?,
        evidence: AiDecisionEvidence? = null,
    ): RecordedAiChoice {
        if (nativePaymentError != null) {
            require(nativePaymentError.isNotBlank()) { "Native payment error must not be blank" }
            require(pendingDecision is SelectManaSourcesDecision) {
                "Payment correction requires a fresh native mana-source decision"
            }
        }

        // Apply caching if enabled
        val cacheMetadata: CacheMetadata? = if (enableStateCaching && stateCacheManager != null) {
            observationIdentity.update(state)
            
            // Check for cache hit
            val cachedEntry = stateCacheManager?.getCachedObservation(observationIdentity.identity)
            val isCacheHit = cachedEntry != null
            
            // Update card text cache with visible cards from state
            updateCardTextCacheFromState(state, legalActions)
            
            CacheMetadata(
                observationId = observationIdentity.identity,
                isCacheHit = isCacheHit,
                tokensSaved = if (isCacheHit) 500L else 0L, // Rough estimate
                cacheStats = stateCacheManager?.getStats() ?: StateCacheManagerCacheStats(),
                cardCacheStats = cardTextCache?.getStats() ?: StateCacheManagerCacheStats()
            )
        } else null

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
            evidence?.let { e -> put("decisionEvidence", json.encodeToJsonElement(AiDecisionEvidence.serializer(), e)) }
            nativePaymentError?.let { pe -> put("nativePaymentError", pe) }
        }

        // Log cache metadata if enabled (for debugging/monitoring)
        if (enableStateCaching && cacheMetadata != null) {
            logCacheMetadata(cacheMetadata)
        }

        val response = post("choose-action", body)

        return when (response.requiredString("kind")) {
            "action" -> {
                val index = response.requiredInt("actionId")
                val native = legalActions.getOrNull(index)
                    ?: error("CachingCommanderGymPlayerController returned a stale legal action index")
                val params = json.decodeFromJsonElement<ActionParams>(response.requiredObject("params"))
                parameterize(native, params).let {
                    RecordedAiChoice(ActionResponse.SubmitAction(it), index, response.requiredObject("params"))
                }
            }
            "decision" -> {
                require(response.requiredString("playerId") == playerId.value) {
                    "CachingCommanderGymPlayerController returned a decision for another seat"
                }
                RecordedAiChoice(ActionResponse.SubmitDecision(
                    playerId,
                    json.decodeFromJsonElement<DecisionResponse>(response.requiredObject("response")),
                ))
            }
            else -> error("CachingCommanderGymPlayerController returned an unknown response kind")
        }
    }

    /**
     * Update card text cache with visible cards from game state.
     * Only caches seat-visible information (no opponent hidden data).
     */
    private fun updateCardTextCacheFromState(state: ClientGameState, legalActions: List<LegalActionInfo>) {
        // In a real implementation, extract actual card data from game state
        // This would involve parsing the state to find visible cards and their oracle text
        // For now, this is a placeholder that demonstrates the integration point
        
        // Example: Extract cards from legal actions (these are always seat-visible)
        for (action in legalActions) {
            if (action.actionType == "play_card" || action.actionType == "cast_spell") {
                // Add card to cache - this would be expanded with actual card extraction logic
                val cardId = extractCardIdFromAction(action, state)
                if (!cardTextCache?.hasCard(cardId) == true) {
                    cardTextCache?.addOrUpdateCard(
                        cardId = cardId,
                        name = "Unknown Card", // Would be extracted from actual game data
                        manaCost = null,
                        typeLine = null,
                        oracleText = null
                    )
                }
            }
        }
    }

    /**
     * Extract card ID from a game action (placeholder implementation).
     */
    private fun extractCardIdFromAction(action: LegalActionInfo, state: ClientGameState): String {
        // This would be implemented based on actual game data structure
        return "card-${System.currentTimeMillis()}" // Placeholder
    }

    /**
     * Log cache metadata for debugging and monitoring.
     */
    private fun logCacheMetadata(metadata: CacheMetadata) {
        val logEntry = buildString {
            append("=== Replay-Safe State Caching ===\n")
            append("Observation ID: ${metadata.observationId}\n")
            append("Cache Hit: ${metadata.isCacheHit}\n")
            append("Tokens Saved (est): ${metadata.tokensSaved}\n")
            append("State Cache Stats:\n")
            metadata.cacheStats?.let { cs ->
                append("  - Hits: ${cs.cacheHits}\n")
                append("  - Misses: ${cs.cacheMisses}\n")
                append("  - Tokens Saved: ${cs.totalTokensSaved}\n")
            }
            append("Card Cache Stats:\n")
            metadata.cardCacheStats?.let { ccs ->
                append("  - Cards Cached: ${ccs.cardsCached}\n")
                append("  - Tokens Saved: ${ccs.totalTokensSaved}\n")
            }
            append("===============================\n")
        }

        // Log to recorder if available, otherwise console (for debugging)
        recorder?.log(logEntry) ?: println(logEntry)
    }

    companion object {
        /**
         * Create a caching-enabled controller with default settings.
         * Caching is disabled by default for safety - use this factory method only when explicitly opted-in.
         */
        fun createWithCaching(
            playerId: EntityId,
            endpoint: URI,
            token: String,
            timeout: Duration = Duration.ofSeconds(30),
            profileId: String? = null,
            expectedDeck: Map<String, Int>? = null,
            gameSessionId: String? = null,
            recorder: RecorderCallbackClient? = null,
        ): CachingCommanderGymPlayerController {
            return CachingCommanderGymPlayerController(
                playerId = playerId,
                endpoint = endpoint,
                token = token,
                timeout = timeout,
                profileId = profileId,
                expectedDeck = expectedDeck,
                gameSessionId = gameSessionId,
                recorder = recorder,
                enableStateCaching = true, // Explicit opt-in
                maxCacheSize = 1024,
            )
        }

        /**
         * Create a controller without caching (default behavior).
         */
        fun createWithoutCaching(
            playerId: EntityId,
            endpoint: URI,
            token: String,
            timeout: Duration = Duration.ofSeconds(30),
            profileId: String? = null,
            expectedDeck: Map<String, Int>? = null,
            gameSessionId: String? = null,
            recorder: RecorderCallbackClient? = null,
        ): CommanderGymPlayerController {
            return CommanderGymPlayerController(
                playerId = playerId,
                endpoint = endpoint,
                token = token,
                timeout = timeout,
                profileId = profileId,
                expectedDeck = expectedDeck,
                gameSessionId = gameSessionId,
                recorder = recorder,
            )
        }
    }
}
