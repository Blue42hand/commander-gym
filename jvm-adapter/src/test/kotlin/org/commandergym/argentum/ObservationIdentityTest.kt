package org.commandergym.argentum

import com.wingedsheep.engine.view.ClientGameState
import com.wingedsheep.sdk.model.EntityId
import kotlinx.serialization.json.Json
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * Unit tests for ObservationIdentity - verifies state hashing and change detection.
 */
class ObservationIdentityTest {

    @Test
    fun `identical states produce identical hashes`() {
        val gameSessionId = "test-session-123"
        
        // Create two identical game states
        val state1 = createMinimalState(gameSessionId, turnNumber = 5)
        val state2 = createMinimalState(gameSessionId, turnNumber = 5)
        
        val identity1 = ObservationIdentity.fromState(gameSessionId, state1)
        val identity2 = ObservationIdentity.fromState(gameSessionId, state2)
        
        // Hashes should be identical for identical states
        assertEquals(identity1.stateHash, identity2.stateHash, "Identical states must produce same hash")
    }

    @Test
    fun `different turn numbers produce different hashes`() {
        val gameSessionId = "test-session-456"
        
        val state1 = createMinimalState(gameSessionId, turnNumber = 3)
        val state2 = createMinimalState(gameSessionId, turnNumber = 7)
        
        val identity1 = ObservationIdentity.fromState(gameSessionId, state1)
        val identity2 = ObservationIdentity.fromState(gameSessionId, state2)
        
        assertTrue(identity1.stateHash != identity2.stateHash, "Different turns must produce different hashes")
    }

    @Test
    fun `different phases produce different hashes`() {
        val gameSessionId = "test-session-789"
        
        // Create states with different phases by modifying the state object directly
        val state1 = createMinimalState(gameSessionId, turnNumber = 5)
        val state2 = createMinimalState(gameSessionId, turnNumber = 5)
        
        // Manually change phase to simulate different game phase
        val originalPhase = state1.phase.name.lowercase()
        state1.phase = com.wingedsheep.sdk.core.Phase("combat")
        state2.phase = com.wingedsheep.sdk.core.Phase(originalPhase.uppercase())
        
        val identity1 = ObservationIdentity.fromState(gameSessionId, state1)
        val identity2 = ObservationIdentity.fromState(gameSessionId, state2)
        
        assertTrue(identity1.stateHash != identity2.stateHash, "Different phases must produce different hashes")
    }

    @Test
    fun `isEquivalent correctly identifies same observations`() {
        val gameSessionId = "test-session-equiv"
        val state = createMinimalState(gameSessionId, turnNumber = 10)
        
        val identity1 = ObservationIdentity.fromState(gameSessionId, state)
        val identity2 = ObservationIdentity.fromState(gameSessionId, state)
        
        assertTrue(identity1.isEquivalent(identity2), "Identical observations should be equivalent")
    }

    @Test
    fun `isEquivalent correctly identifies different observations`() {
        val gameSessionId = "test-session-diff"
        
        val state1 = createMinimalState(gameSessionId, turnNumber = 5)
        val state2 = createMinimalState(gameSessionId, turnNumber = 6)
        
        val identity1 = ObservationIdentity.fromState(gameSessionId, state1)
        val identity2 = ObservationIdentity.fromState(gameSessionId, state2)
        
        assertFalse(identity1.isEquivalent(identity2), "Different observations should not be equivalent")
    }

    @Test
    fun `hash is deterministic across multiple calls`() {
        val gameSessionId = "test-session-deterministic"
        val state = createMinimalState(gameSessionId, turnNumber = 42)
        
        val hash1 = ObservationIdentity.computeStateHash(state)
        val hash2 = ObservationIdentity.computeStateHash(state)
        val hash3 = ObservationIdentity.computeStateHash(state)
        
        assertEquals(hash1, hash2, "Hash must be deterministic")
        assertEquals(hash2, hash3, "Hash must be deterministic across calls")
    }

    @Test
    fun `hash format is valid hex string`() {
        val gameSessionId = "test-session-hex"
        val state = createMinimalState(gameSessionId, turnNumber = 1)
        
        val identity = ObservationIdentity.fromState(gameSessionId, state)
        
        // SHA-256 produces 32 bytes = 64 hex characters
        assertTrue(identity.stateHash.length == 64, "SHA-256 hash should be 64 hex characters")
        assertTrue(identity.stateHash.all { it.isDigit() || it in 'a'..'f' }, "Hash must contain only valid hex characters")
    }

    @Test
    fun `observationVersion is monotonically increasing`() {
        val gameSessionId = "test-session-version"
        val state = createMinimalState(gameSessionId, turnNumber = 1)
        
        val identity1 = ObservationIdentity.fromState(gameSessionId, state)
        Thread.sleep(1) // Ensure different nano time
        val identity2 = ObservationIdentity.fromState(gameSessionId, state)
        
        assertTrue(identity2.observationVersion > identity1.observationVersion, 
            "Observation version should be monotonically increasing")
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
}
