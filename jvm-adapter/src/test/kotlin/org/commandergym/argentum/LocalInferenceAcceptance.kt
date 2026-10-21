package org.commandergym.argentum

import com.wingedsheep.ai.ActionResponse
import com.wingedsheep.gameserver.ai.AiControllerContext
import com.wingedsheep.ai.llm.BottomCardsInfo
import com.wingedsheep.ai.llm.CardSummary
import com.wingedsheep.ai.llm.MulliganInfo
import com.wingedsheep.engine.core.ActionParameterizer
import com.wingedsheep.engine.core.GameAction
import com.wingedsheep.engine.core.SubmitDecision
import com.wingedsheep.gameserver.config.GameBeansConfig
import com.wingedsheep.gameserver.config.GameProperties
import com.wingedsheep.gameserver.protocol.ServerMessage
import com.wingedsheep.gameserver.persistence.restoreGameSession
import com.wingedsheep.gameserver.persistence.toPersistent
import com.wingedsheep.gameserver.persistence.persistenceJson
import com.wingedsheep.gameserver.persistence.dto.PersistentGameSession
import com.wingedsheep.gameserver.recording.PrivateGameEvidence
import com.wingedsheep.gameserver.session.GameSession
import com.wingedsheep.gameserver.session.PlayerSession
import com.wingedsheep.sdk.model.EntityId
import kotlinx.serialization.json.*
import org.mockito.Mockito
import org.springframework.web.socket.WebSocketSession
import java.net.URI
import java.nio.file.Files
import java.nio.file.Path
import java.time.Duration
import java.util.UUID

/** Bounded local-only synthetic game. Eight basic lands per seat reach native deck-out.
 * Uses actual loaded inference, native callback evidence and authoritative application.
 * This is not a full Commander or human-game qualification. Never run on a live service.
 */
fun main() {
    val config = Json.parseToJsonElement(Files.readString(Path.of(
        requireNotNull(System.getenv("COMMANDER_GYM_LOCAL_ACCEPTANCE_CONFIG"))))).jsonObject
    fun text(name: String) = config.getValue(name).jsonPrimitive.content
    val root = Path.of(text("recordingRoot"))
    val gameId = UUID.randomUUID().toString()
    val registry = GameBeansConfig(GameProperties()).cardRegistry()
    System.setProperty("game.recording.root", root.toString())
    System.setProperty("game.recording.engine-revision", text("engineSha"))
    val writer = requireNotNull(PrivateGameEvidence.fromSystemProperties(gameId)) as PrivateGameEvidence
    val session = GameSession(sessionId=gameId, cardRegistry=registry, evidenceSink=writer)
    val seats = listOf(EntityId.of("local-synthetic-a"), EntityId.of("local-synthetic-b"))
    val deck = mapOf("Plains" to 8)
    for (seat in seats) {
        val ws = Mockito.mock(WebSocketSession::class.java)
        Mockito.`when`(ws.id).thenReturn("fixture-${seat.value}")
        session.addPlayer(PlayerSession(ws, seat, seat.value), deck)
    }
    val provider = CommanderGymControllerProvider(URI.create(text("sidecarUrl")),
        Files.readString(Path.of(text("tokenPath"))).trim(), Duration.ofSeconds(90),
        mode="commander-gym-local", profileLabel="Local", requireExplicitProfile=true)
    check(provider.mode == "commander-gym-local" && !provider.supportsDefaultController)
    check(provider.profiles.single().id == "local-qualification")
    val controllers = seats.associateWith { seat ->
        (provider.create(AiControllerContext(playerId=seat, gameSessionId=gameId,
            profileId="local-qualification", snapshot={ session.getAiRuntimeSnapshot() })) as CommanderGymPlayerController).also {
            it.setDeckList(deck)
        }
    }
    val budgetSeconds = config["gameBudgetSeconds"]?.jsonPrimitive?.int ?: 300
    require(budgetSeconds in 60..900) { "Synthetic game budget must be 60..900 seconds" }
    val started = System.nanoTime()
    val deadline = started + Duration.ofSeconds(budgetSeconds.toLong()).toNanos()
    var actions = 0
    var lastTurn = 0
    var sameTurnActions = 0
    var restoreVerified = false
    fun summary(status: String) {
        val state = session.getStateSnapshot()
        val value = buildJsonObject {
            put("status", status); put("gameId", gameId); put("actions", actions)
            put("gameBudgetSeconds", budgetSeconds)
            put("elapsedSeconds", (System.nanoTime() - started) / 1_000_000_000.0)
            put("turn", state?.turnNumber ?: 0); put("nativeTerminal", state?.gameOver == true)
            put("winnerId", state?.winnerId?.value?.let(::JsonPrimitive) ?: JsonNull)
            put("terminalSnapshotRestoreVerified", restoreVerified)
            put("inference", "local-only-both-seats"); put("concessionUsed", false)
            put("format", "synthetic-eight-Plains"); put("libraryCardsPerSeat", 8)
        }
        Files.writeString(Path.of(text("resultPath")), value.toString())
        println(value)
    }
    fun cards(input: Map<EntityId, ServerMessage.MulliganCardInfo>) = input.mapValues { (_, c) ->
        CardSummary(name=c.name, manaCost=c.manaCost, typeLine=c.typeLine,
            power=c.power, toughness=c.toughness, oracleText=c.oracleText) }
    try {
        session.startGame()
        for (seat in seats) {
            var mulligans = 0
            while (!session.hasMulliganComplete(seat)) {
                check(System.nanoTime() < deadline && ++mulligans <= 16) { "Mulligan test budget exhausted" }
                val remote = controllers.getValue(seat)
                val bottom = session.getChooseBottomCardsMessage(seat)
                val result = if (bottom != null) {
                    val evidence = requireNotNull(session.beginAiBottomEvidence(seat, bottom))
                    val chosen = remote.chooseRecordedBottomCards(BottomCardsInfo(bottom.hand,
                        bottom.cardsToPutOnBottom, cards=cards(bottom.cards)), evidence)
                    session.executeRecordedBottom(seat, evidence, chosen)
                } else {
                    val m = session.getMulliganDecision(seat)
                    val evidence = requireNotNull(session.beginAiMulliganEvidence(seat, m))
                    val keep = remote.decideRecordedMulligan(MulliganInfo(m.hand, m.mulliganCount,
                        m.cardsToPutOnBottom, cards=cards(m.cards), isOnThePlay=m.isOnThePlay), evidence)
                    session.executeRecordedMulligan(seat, evidence, keep)
                }
                check(result !is GameSession.MulliganActionResult.Failure) { "Native mulligan rejected" }
            }
        }
        while (!session.isGameOver()) {
            check(System.nanoTime() < deadline && actions < 150) { "Full game budget exhausted" }
            val state = requireNotNull(session.getStateSnapshot())
            if (state.turnNumber != lastTurn) { lastTurn=state.turnNumber; sameTurnActions=0; summary("running") }
            check(++sameTurnActions <= 60) { "Native game stopped advancing" }
            val actor = state.pendingDecision?.let { state.actorFor(it.playerId) }
                ?: requireNotNull(state.priorityPlayerId)
            session.clearLastSentState(actor) // fixture consumes full masked views, not transport deltas
            val update = session.createStateUpdate(actor, emptyList(), useEngineDecisionIds=true) as ServerMessage.StateUpdate
            val result = run {
                val remote = controllers.getValue(actor)
                val evidence = requireNotNull(session.beginAiCallbackEvidence(actor, update.state,
                    update.legalActions, update.pendingDecision, update.interactionEpoch, emptyList()))
                val choice = remote.chooseRecordedCallback(update.state, update.legalActions,
                    update.pendingDecision, emptyList(), evidence)
                val action: GameAction = when (val response=choice.response) {
                    is ActionResponse.SubmitAction -> response.action
                    is ActionResponse.SubmitDecision -> SubmitDecision(response.playerId, response.response)
                }
                session.executeRecordedAiCallback(actor, action, update.interactionEpoch, evidence, choice)
            }
            check(result != null && result !is GameSession.ActionResult.Failure) { "Native action rejected" }
            actions++
        }
        check(writer.healthy()) { "Native evidence unhealthy" }
        // A real disk/serialization round trip through the native persistence seam.
        // Keep the restored session detached from the finalized evidence journal.
        val snapshot = root.resolve("$gameId-terminal-snapshot.json")
        Files.writeString(snapshot, persistenceJson.encodeToString(PersistentGameSession.serializer(), session.toPersistent(null)))
        Files.setPosixFilePermissions(snapshot, java.nio.file.attribute.PosixFilePermissions.fromString("rw-------"))
        val saved = persistenceJson.decodeFromString(PersistentGameSession.serializer(), Files.readString(snapshot))
        // The public native restorer uses the recording system property. This isolated
        // fixture has no concurrent callbacks; detach recording only during restoration.
        val recordingProperty = System.clearProperty("game.recording.root")
        val restored = try { restoreGameSession(saved, registry).first }
            finally { recordingProperty?.let { System.setProperty("game.recording.root", it) } }
        check(restored.isGameOver() && restored.getStateSnapshot() == session.getStateSnapshot()) { "Terminal native state restore mismatch" }
        check(restored.toPersistent(null) == saved) { "Native persisted session restore mismatch" }
        restoreVerified = true
        summary("native-terminal")
    } catch (error: Throwable) {
        summary("failed-incomplete")
        throw error
    } finally { writer.close() }
}
