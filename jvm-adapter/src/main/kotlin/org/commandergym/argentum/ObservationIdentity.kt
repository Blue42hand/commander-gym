package org.commandergym.argentum

import com.wingedsheep.engine.view.ClientGameState
import kotlinx.serialization.Serializable
import java.security.MessageDigest
import java.util.UUID

/**
 * Unique identifier for a pilot's observation at a specific point in the game.
 * Enables detection of unchanged states and safe caching opportunities.
 */
@Serializable
data class ObservationIdentity(
    val gameSessionId: String,
    val turnNumber: Int,
    val phase: String,
    val stateHash: String,
    val observationVersion: Long,
) {
    companion object {
        /**
         * Compute a SHA-256 hash of the client game state for change detection.
         * This is deterministic and allows us to detect when observations are identical.
         */
        fun computeStateHash(state: ClientGameState): String {
            val digest = MessageDigest.getInstance("SHA-256")
            
            // Serialize key observable fields (excluding hidden opponent info)
            val stateData = buildString {
                append(state.turnNumber.toString())
                append("|")
                append(state.phase.name.lowercase())
                append("|")
                
                // Include only seat-visible information:
                // - Current player's hand and board
                // - Public game events
                // - Known legal actions (without opponent-specific details)
                
                state.currentPlayer?.value?.let { playerId ->
                    append("current_player=$playerId|")
                }
                
                // Hand cards (own only)
                state.handCards?.values?.forEach { card ->
                    append("hand:${card.id.value}:${card.name}|")
                }
                
                // Board permanents (own only, with public attributes)
                state.boardPermanents?.values?.forEach { permanent ->
                    append("board:${permanent.entityId.value}:${permanent.cardName}|")
                }
                
                // Public game events summary
                state.gameEvents?.take(100)?.forEachIndexed { index, event ->
                    append("event$index:${event.eventType}:${event.description.take(50)}|")
                }
            }
            
            return digest.digest(stateData.toByteArray()).hexString()
        }

        /**
         * Create an observation identity from a client game state.
         */
        fun fromState(gameSessionId: String, state: ClientGameState): ObservationIdentity {
            val turnNumber = state.turnNumber ?: 0
            val phase = state.phase.name.lowercase()
            val stateHash = computeStateHash(state)
            
            return ObservationIdentity(
                gameSessionId = gameSessionId,
                turnNumber = turnNumber,
                phase = phase,
                stateHash = stateHash,
                observationVersion = System.nanoTime(),
            )
        }

        /**
         * Check if two observations are effectively identical.
         */
        fun isEquivalent(other: ObservationIdentity): Boolean {
            return gameSessionId == other.gameSessionId &&
                   turnNumber == other.turnNumber &&
                   phase == other.phase &&
                   stateHash == other.stateHash
        }

        private fun ByteArray.hexString(): String = joinToString("") { "%02x".format(it) }
    }
}
