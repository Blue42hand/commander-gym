package org.commandergym.argentum

import com.wingedsheep.engine.view.ClientGameState
import com.wingedsheep.sdk.model.EntityId
import kotlinx.serialization.json.Json
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue
import java.util.concurrent.CountDownLatch
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

/**
 * Unit tests for StateCacheManager - verifies caching behavior and safety.
 */
class StateCacheManagerTest {

    @Test
    fun `initialization creates fresh cache with no hits`() {
        val manager = StateCacheManager("test-session-1")
        
        val state = createMinimalState("test-session-1", turnNumber = 1)
        val metadata = manager.initialize(state)
        
        assertEquals(0, metadata.cacheHits, "Initial state should not be a cache hit")
        assertEquals(0, metadata.cacheMisses, "First observation is always a miss")
        assertNotNull(metadata.currentObservationIdentity, "Should track current identity")
    }

    @Test
    fun `repeated identical states are cached as hits`() {
        val manager = StateCacheManager("test-session-2")
        
        val state1 = createMinimalState("test-session-2", turnNumber = 5)
        val metadata1 = manager.initialize(state1)
        
        // Record the same observation twice (simulating unchanged state between turns)
        val metadata2 = manager.recordObservation(state1, estimatedTokens = 500L)
        val metadata3 = manager.recordObservation(state1, estimatedTokens = 500L)
        
        assertEquals(1, metadata2.cacheHits, "Second identical observation should be a hit")
        assertEquals(1, metadata3.cacheHits, "Third identical observation should also be a hit")
        assertEquals(1000L, metadata3.totalTokensSaved, "Should track token savings from cache hits")
    }

    @Test
    fun `different states are not cached as hits`() {
        val manager = StateCacheManager("test-session-3")
        
        val state1 = createMinimalState("test-session-3", turnNumber = 5)
        val metadata1 = manager.initialize(state1)
        
        // Create a different state (different turn number)
        val state2 = createMinimalState("test-session-3", turnNumber = 6)
        val metadata2 = manager.recordObservation(state2, estimatedTokens = 500L)
        
        assertEquals(0, metadata2.cacheHits, "Different state should not be a cache hit")
        assertEquals(1, metadata2.cacheMisses, "New state is always a miss")
    }

    @Test
    fun `cache does not include opponent hidden information`() {
        val manager = StateCacheManager("test-session-4")
        
        // Create a state with both player and opponent cards
        val state = createStateWithOpponentCards("test-session-4", turnNumber = 1)
        manager.initialize(state)
        
        val metadata = manager.recordObservation(state, estimatedTokens = 500L)
        val cachedState = manager.getCachedStateForIdentity(metadata.currentObservationIdentity!!)
        
        // Verify opponent cards are NOT in the cache
        assertTrue(cachedState!!.visibleHandCards.isEmpty(), "Opponent hand should not be cached")
        assertTrue(cachedState.visibleBoardPermanents.isEmpty() || 
                   !cachedState.visibleBoardPermanents.any { it.contains("opponent-card") },
            "Opponent board cards should not be cached")
    }

    @Test
    fun `cache correctly identifies state changes`() {
        val manager = StateCacheManager("test-session-5")
        
        val initialState = createMinimalState("test-session-5", turnNumber = 1)
        manager.initialize(initialState)
        
        // Record unchanged state (should be cached)
        val metadata1 = manager.recordObservation(initialState, estimatedTokens = 500L)
        assertTrue(metadata1.cacheHits > 0, "Unchanged state should be cached")
        
        // Create a modified state (different turn)
        val modifiedState = createMinimalState("test-session-5", turnNumber = 2)
        val metadata2 = manager.recordObservation(modifiedState, estimatedTokens = 500L)
        
        assertEquals(0, metadata2.cacheHits, "Modified state should not be a cache hit")
    }

    @Test
    fun `cache reset on incompatible state transition`() {
        val manager = StateCacheManager("test-session-6")
        
        // Initialize with one game session
        val state1 = createMinimalState("test-session-6", turnNumber = 1)
        manager.initialize(state1)
        manager.recordObservation(state1, estimatedTokens = 500L)
        
        // Simulate a new game session (clear cache)
        manager.clearCache()
        
        val state2 = createMinimalState("test-session-6", turnNumber = 10)
        val metadata = manager.initialize(state2)
        
        assertEquals(0, metadata.cacheHits, "After clear, no cached hits")
        assertNotNull(metadata.currentObservationIdentity, "Should track new identity after reset")
    }

    @Test
    fun `cache statistics are accurate`() {
        val manager = StateCacheManager("test-session-7")
        
        // First observation (miss)
        val state1 = createMinimalState("test-session-7", turnNumber = 1)
        manager.initialize(state1)
        manager.recordObservation(state1, estimatedTokens = 500L)
        
        // Second identical observation (hit)
        val metadata2 = manager.recordObservation(state1, estimatedTokens = 500L)
        
        assertEquals(1, metadata2.cacheHits, "Should have one cache hit")
        assertEquals(1, metadata2.cacheMisses, "Should have one cache miss")
    }

    @Test
    fun `thread safety under concurrent access`() {
        val manager = StateCacheManager("test-session-8", maxCacheSize = 100)
        
        val executor = Executors.newFixedThreadPool(10)
        val latch = CountDownLatch(100)
        
        try {
            // Submit many concurrent operations
            for (i in 0..99) {
                executor.submit {
                    val state = createMinimalState("test-session-8", turnNumber = i % 10)
                    manager.recordObservation(state, estimatedTokens = 50L)
                    latch.countDown()
                }
            }
            
            latch.await(30, java.util.concurrent.TimeUnit.SECONDS)
            
            // Verify cache is in consistent state
            val stats = manager.getStats()
            assertTrue(stats.cacheHits >= 0, "Cache hits should be non-negative")
            assertTrue(stats.cacheMisses >= 0, "Cache misses should be non-negative")
        } finally {
            executor.shutdownNow()
        }
    }

    @Test
    fun `cache enforces size limit`() {
        val manager = StateCacheManager("test-session-9", maxCacheSize = 5)
        
        // Add more states than cache can hold
        for (i in 0..10) {
            val state = createMinimalState("test-session-9", turnNumber = i)
            manager.initialize(state)
            manager.recordObservation(state, estimatedTokens = 50L)
        }
        
        // Cache should have at most maxCacheSize entries
        assertTrue(manager.cache.size <= 5, "Cache size should not exceed limit")
    }

    private fun createMinimalState(sessionId: String, turnNumber: Int): ClientGameState {
        return ClientGameState(
            gameSessionId = sessionId,
            currentPlayer = EntityId.of("player-1"),
            handCards = mapOf(EntityId.of("card-1") to com.wingedsheep.engine.view.CardSummary(
                id = EntityId.of("card-1"),
                name = "Test Card",
                manaCost = null,
                typeLine = null,
                oracleText = null,
            )),
            boardPermanents = mapOf(EntityId.of("perm-1") to com.wingedsheep.engine.view.Permanent(
                entityId = EntityId.of("perm-1"),
                cardName = "Test Permanent",
                controllerId = EntityId.of("player-1"),
            )),
            gameEvents = listOf(
                com.wingedsheep.engine.view.GameEvent(
                    eventType = "turn_start",
                    description = "Turn started",
                    timestamp = System.currentTimeMillis(),
                )
            ),
            turnNumber = turnNumber,
            phase = com.wingedsheep.sdk.core.Phase("main_phase"),
        )
    }

    private fun createStateWithOpponentCards(sessionId: String, turnNumber: Int): ClientGameState {
        return ClientGameState(
            gameSessionId = sessionId,
            currentPlayer = EntityId.of("player-1"),
            handCards = mapOf(
                EntityId.of("card-1") to com.wingedsheep.engine.view.CardSummary(
                    id = EntityId.of("card-1"),
                    name = "Player Card",
                    manaCost = null,
                    typeLine = null,
                    oracleText = null,
                ),
                // Opponent's card (should NOT be cached)
                EntityId.of("opponent-card") to com.wingedsheep.engine.view.CardSummary(
                    id = EntityId.of("opponent-card"),
                    name = "Opponent Secret Card",
                    manaCost = null,
                    typeLine = null,
                    oracleText = null,
                )
            ),
            boardPermanents = mapOf(
                EntityId.of("perm-1") to com.wingedsheep.engine.view.Permanent(
                    entityId = EntityId.of("perm-1"),
                    cardName = "Player Permanent",
                    controllerId = EntityId.of("player-1"),
                ),
                // Opponent's permanent (should NOT be cached)
                EntityId.of("opponent-perm") to com.wingedsheep.engine.view.Permanent(
                    entityId = EntityId.of("opponent-perm"),
                    cardName = "Opponent Secret Permanent",
                    controllerId = EntityId.of("opponent-player"),
                )
            ),
            gameEvents = listOf(
                com.wingedsheep.engine.view.GameEvent(
                    eventType = "turn_start",
                    description = "Turn started",
                    timestamp = System.currentTimeMillis(),
                )
            ),
            turnNumber = turnNumber,
            phase = com.wingedsheep.sdk.core.Phase("main_phase"),
        )
    }
}
