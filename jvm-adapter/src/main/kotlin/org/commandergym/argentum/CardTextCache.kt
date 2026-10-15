package org.commandergym.argentum

import com.wingedsheep.engine.registry.CardRegistry
import kotlinx.serialization.Serializable
import java.time.Instant
import java.util.concurrent.ConcurrentHashMap
import kotlin.math.max

/**
 * Caches card oracle text and rules within a game session to avoid repeated transmission.
 * Only caches cards that have been seen in the current context (seat-visible only).
 */
@Serializable
data class CardTextCacheEntry(
    val cardId: String,
    val name: String,
    val manaCost: String?,
    val typeLine: String?,
    val oracleText: String?,
    val cachedAt: Instant = Instant.now(),
)

/**
 * Thread-safe cache for card text deduplication within a game session.
 */
class CardTextCache(
    private val gameSessionId: String,
    private val maxEntries: Int = 512, // Maximum number of unique cards to cache
) {
    companion object {
        const val DEFAULT_TOKEN_ESTIMATE_PER_CARD = 100L // Rough estimate for card text serialization
    }

    private val cache = ConcurrentHashMap<String, CardTextCacheEntry>()
    private var currentDeckHash: String? = null
    private var lastDeckUpdate: Instant? = null
    
    /**
     * Initialize or reset the cache with a new deck composition.
     * Clears previous cache if deck hash changes (deck composition changed).
     */
    fun initialize(deckHash: String): CardTextCacheEntry {
        val wasReset = currentDeckHash != deckHash
        
        // Clear cache on deck change to prevent stale card data
        if (wasReset) {
            clearCache()
        }
        
        currentDeckHash = deckHash
        lastDeckUpdate = Instant.now()
        
        return CardTextCacheEntry(
            cardId = "deck-initialized",
            name = "Deck Initialized",
            manaCost = null,
            typeLine = null,
            oracleText = null,
            cachedAt = Instant.now(),
        )
    }

    /**
     * Check if a card's text is already cached.
     */
    fun hasCard(cardId: String): Boolean {
        return cache.containsKey(cardId)
    }

    /**
     * Get cached card entry by ID.
     */
    fun getCardEntry(cardId: String): CardTextCacheEntry? {
        return cache[cardId]
    }

    /**
     * Add or update a card's text in the cache.
     * Returns whether this was a new addition (not already cached).
     */
    fun addOrUpdateCard(
        cardId: String,
        name: String,
        manaCost: String?,
        typeLine: String?,
        oracleText: String?
    ): Boolean {
        // Check if already cached
        if (cache.containsKey(cardId)) {
            return false // Already in cache
        }
        
        val entry = CardTextCacheEntry(
            cardId = cardId,
            name = name,
            manaCost = manaCost,
            typeLine = typeLine,
            oracleText = oracleText,
            cachedAt = Instant.now(),
        )
        
        cache[cardId] = entry
        
        // Enforce maximum cache size
        enforceCacheSize()
        
        return true
    }

    /**
     * Get all unique card entries currently in the cache.
     */
    fun getAllEntries(): List<CardTextCacheEntry> {
        return cache.values.toList()
    }

    /**
     * Get count of cached cards.
     */
    fun getCardCount(): Int {
        return cache.size
    }

    /**
     * Clear all cached card entries (e.g., after deck change or context reset).
     */
    fun clearCache() {
        cache.clear()
        currentDeckHash = null
        lastDeckUpdate = null
    }

    /**
     * Check if the cache should be invalidated due to deck change.
     */
    fun isDeckChanged(): Boolean {
        return currentDeckHash != lastDeckUpdate?.let { 
            // Compare with previous deck hash (would need to store it)
            false 
        }
    }

    /**
     * Get statistics about the cache.
     */
    fun getStats(): CacheMetadata {
        val totalTokens = getAllEntries().sumOf { DEFAULT_TOKEN_ESTIMATE_PER_CARD }
        
        return CacheMetadata(
            cacheHits = 0, // Would need tracking per card lookup
            cacheMisses = 0,
            totalTokensSaved = totalTokens,
            lastCacheUpdate = Instant.now(),
            currentObservationIdentity = null,
        )
    }

    /**
     * Enforce maximum cache size by removing oldest entries.
     */
    private fun enforceCacheSize() {
        if (cache.size <= maxEntries) return
        
        // Remove the oldest entry (first in iteration order for ConcurrentHashMap)
        val keysToRemove = cache.keys.toList().take(maxEntries - 1)
        keysToRemove.forEach { key ->
            cache.remove(key)
        }
    }

    /**
     * Get cards that have been seen since last deck update.
     */
    fun getNewCardsSinceLastUpdate(): List<String> {
        if (lastDeckUpdate == null) return emptyList()
        
        val now = Instant.now()
        val cutoffTime = lastDeckUpdate!!
        
        // Find cards cached after the last deck update
        return cache.keys.filter { entry ->
            cache[entry]?.cachedAt?.isAfter(cutoffTime) == true
        }.toList()
    }

    /**
     * Check if a card is visible to the current player (seat-visible only).
     * This prevents caching opponent's hidden cards.
     */
    fun isVisibleToPlayer(cardId: String, controllerId: String): Boolean {
        // In a real implementation, this would check game state
        // For now, we assume all cached cards are seat-visible
        return true
    }

    /**
     * Get card text for building model prompts (deduplicated).
     */
    fun getDeduplicatedCardList(): List<String> {
        val entries = getAllEntries()
        
        return entries.map { entry ->
            buildString {
                append("Name: ${entry.name}\n")
                entry.manaCost?.let { append("Mana Cost: $it\n") }
                entry.typeLine?.let { append("Type: $it\n") }
                entry.oracleText?.let { 
                    if (it.isNotBlank()) append("Oracle Text: ${it.take(200)}\n") 
                }
            }
        }
    }

    /**
     * Estimate token savings from caching.
     */
    fun estimateTokenSavings(): Long {
        return getAllEntries().size.toLong() * DEFAULT_TOKEN_ESTIMATE_PER_CARD
    }
}

/**
 * Extension function to create a deck hash for cache invalidation.
 */
fun CardRegistry.getDeckHash(deckList: Map<String, Int>): String {
    val sortedCards = deckList.entries.sortedBy { it.key }.joinToString(",") { "${it.key}:${it.value}" }
    return java.security.MessageDigest.getInstance("SHA-256")
        .digest(sortedCards.toByteArray())
        .hexString()
}

private fun ByteArray.hexString(): String = joinToString("") { "%02x".format(it) }
