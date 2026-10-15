package org.commandergym.argentum

import com.wingedsheep.engine.view.ClientGameState
import kotlinx.serialization.Serializable
import java.time.Instant
import java.util.concurrent.ConcurrentHashMap
import kotlin.math.max

/**
 * Manages caching of pilot observations to reduce repeated model input.
 * Tracks state changes and provides safe deduplication while maintaining replayability.
 */
)

/**
 * Thread-safe state cache manager for pilot observations.
 */
class StateCacheManager(
    private val gameSessionId: String,
    private val maxCacheSize: Int = 1024, // Maximum number of cached states to retain
) {
    companion object {
        const val DEFAULT_TOKEN_ESTIMATE_PER_STATE = 500L // Rough estimate for a full state serialization
    }

    private val cache = ConcurrentHashMap<ObservationIdentity, CachedState>()
    private var currentVersion = 0L
    private var lastKnownIdentity: ObservationIdentity? = null
    
    fun initialize(state: ClientGameState): CacheMetadata {
        val identity = ObservationIdentity.fromState(gameSessionId, state)
        
        // Clear cache on new game session or incompatible state transition
        if (lastKnownIdentity != null && !identity.isEquivalent(lastKnownIdentity!!)) {
            clearCache()
        }
        
        lastKnownIdentity = identity
        currentVersion++
        
        return CacheMetadata(
            cacheHits = 0,
            cacheMisses = 0,
            totalTokensSaved = 0L,
            lastCacheUpdate = Instant.now(),
            currentObservationIdentity = identity,
        )
    }

    /**
     * Check if the current state can be safely cached (i.e., it's unchanged from previous observation).
     */
    fun isStateCached(state: ClientGameState): Boolean {
        val identity = ObservationIdentity.fromState(gameSessionId, state)
        
        // If no prior cache exists, this is a miss
        if (lastKnownIdentity == null) return false
        
        // Check if the observation is equivalent to what we've seen before
        return lastKnownIdentity?.isEquivalent(identity) == true &&
               cache.containsKey(lastKnownIdentity!!)
    }

    /**
     * Get cached state metadata for a given identity.
     */
    fun getCachedState(identity: ObservationIdentity): CachedState? = cache[identity]

    /**
     * Record that an observation was made (either from cache or fresh).
     * Returns whether this was a cache hit.
     */
    fun recordObservation(state: ClientGameState, estimatedTokens: Long = DEFAULT_TOKEN_ESTIMATE_PER_STATE): CacheMetadata {
        val identity = ObservationIdentity.fromState(gameSessionId, state)
        
        // Check if we have a cached version of this observation
        var wasCacheHit = false
        var tokensSaved = 0L
        
        if (cache.containsKey(identity)) {
            wasCacheHit = true
            tokensSaved = estimatedTokens
        } else {
            // Cache miss - store the state for future reference
            cache[identity] = CachedState(
                timestamp = Instant.now(),
                turnNumber = identity.turnNumber,
                phase = identity.phase,
                stateHash = identity.stateHash,
                // Store only seat-visible information (no opponent information)
                visibleHandCards = extractVisibleHand(state),
                visibleBoardPermanents = extractVisibleBoard(state),
                publicGameEvents = extractPublicEvents(state),
            )
            
            // Enforce cache size limit by removing oldest entries if needed
            enforceCacheSize()
        }
        
        // Update current tracking
        lastKnownIdentity = identity
        currentVersion++
        
        val metadata = CacheMetadata(
            cacheHits = cacheHits + (if (wasCacheHit) 1 else 0),
            cacheMisses = cacheMisses + (if (!wasCacheHit) 1 else 0),
            totalTokensSaved = totalTokensSaved + tokensSaved,
            lastCacheUpdate = Instant.now(),
            currentObservationIdentity = identity,
        )
        
        return metadata
    }

    /**
     * Get the cached state for a given observation identity.
     */
    fun getCachedStateForIdentity(identity: ObservationIdentity): CachedState? {
        return cache[identity]
    }

    /**
     * Clear all cached states (e.g., after context reset or incompatible transition).
     */
    fun clearCache() {
        cache.clear()
        lastKnownIdentity = null
        currentVersion++
    }

    /**
     * Get statistics about the cache.
     */
    fun getStats(): CacheMetadata {
        return CacheMetadata(
            cacheHits = cacheHits,
            cacheMisses = cacheMisses,
            totalTokensSaved = totalTokensSaved,
            lastCacheUpdate = Instant.now(),
            currentObservationIdentity = lastKnownIdentity,
        )
    }

    /**
     * Extract only seat-visible hand cards (no opponent information).
     */
    private fun extractVisibleHand(state: ClientGameState): List<String> {
        return state.handCards?.values?.map { card ->
            "${card.id.value}:${card.name}"
        }.orEmpty()
    }

    /**
     * Extract only seat-visible board permanents (no opponent information).
     */
    private fun extractVisibleBoard(state: ClientGameState): List<String> {
        return state.boardPermanents?.values?.map { permanent ->
            "${permanent.entityId.value}:${permanent.cardName}"
        }.orEmpty()
    }

    /**
     * Extract public game events (no opponent-specific details).
     */
    private fun extractPublicEvents(state: ClientGameState): List<String> {
        return state.gameEvents?.mapIndexed { index, event ->
            "event$index:${event.eventType}:${event.description.take(50)}"
        }.orEmpty()
    }

    /**
     * Enforce maximum cache size by removing oldest entries.
     */
    private fun enforceCacheSize() {
        if (cache.size <= maxCacheSize) return
        
        // Remove the oldest entry (first in map iteration order, which is insertion order for ConcurrentHashMap)
        val keysToRemove = cache.keys.toList().take(maxCacheSize - 1)
        keysToRemove.forEach { key ->
            cache.remove(key)
        }
    }

    private var cacheHits: Int = 0
    private var cacheMisses: Int = 0
    private var totalTokensSaved: Long = 0L
}

/**
 * Cached representation of a pilot's observation (seat-visible only).
 */
@Serializable
data class CachedState(
    val timestamp: Instant,
    val turnNumber: Int,
    val phase: String,
    val stateHash: String,
    val visibleHandCards: List<String>,
    val visibleBoardPermanents: List<String>,
    val publicGameEvents: List<String>,
)

/**
 * Metadata about cache performance and statistics.
 */
@Serializable
data class CacheMetadata(
    val observationId: String? = null,
    val isCacheHit: Boolean = false,
    val tokensSaved: Long = 0L,
    val cacheStats: StateCacheManagerCacheStats? = null,
    val cardCacheStats: CardTextCacheStats? = null,
) { }

/**
 * Stats for StateCacheManager.
 */
@Serializable
data class StateCacheManagerCacheStats(
    val cacheHits: Int = 0,
    val cacheMisses: Int = 0,
    val totalTokensSaved: Long = 0L,
) {
    companion object {
        fun from(manager: StateCacheManager): StateCacheManagerCacheStats {
            return StateCacheManagerCacheStats(
                cacheHits = manager.cacheHits,
                cacheMisses = manager.cacheMisses,
                totalTokensSaved = manager.totalTokensSaved
            )
        }
    }
}

/**
 * Stats for CardTextCache.
 */
@Serializable
data class CardTextCacheStats(
    val cardsCached: Int = 0,
    val cacheHits: Int = 0,
    val cacheMisses: Int = 0,
    val totalTokensSaved: Long = 0L,
) {
    companion object {
        fun from(cache: CardTextCache): CardTextCacheStats {
            return CardTextCacheStats(
                cardsCached = cache.getCardCount(),
                cacheHits = 0, // Would need tracking per lookup
                cacheMisses = 0,
                totalTokensSaved = cache.estimateTokenSavings()
            )
        }
    }
}
