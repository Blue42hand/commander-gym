package org.commandergym.argentum

import com.wingedsheep.ai.ActionResponse
import com.wingedsheep.ai.engine.EngineAiPlayerController
import com.wingedsheep.ai.llm.BottomCardsInfo
import com.wingedsheep.ai.llm.CardSummary
import com.wingedsheep.ai.llm.MulliganInfo
import com.wingedsheep.engine.core.ActionParameterizer
import com.wingedsheep.engine.core.GameAction
import com.wingedsheep.engine.core.SubmitDecision
import com.wingedsheep.gameserver.config.GameBeansConfig
import com.wingedsheep.gameserver.config.GameProperties
import com.wingedsheep.gameserver.protocol.ServerMessage
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

/** Opt-in full native game: real external Lathril policy versus built-in Talrand.
 * Private deck/config inputs are supplied at runtime. No concession, winner fabrication,
 * rules shortcut or provider fallback is accepted as a qualification result.
 */
fun main() {
    val config = Json.parseToJsonElement(Files.readString(Path.of(
        requireNotNull(System.getenv("COMMANDER_GYM_FULL_GAME_CONFIG"))))).jsonObject
    fun text(name: String) = config.getValue(name).jsonPrimitive.content
    val root = Path.of(text("recordingRoot"))
    val gameId = UUID.randomUUID().toString()
    val registry = GameBeansConfig(GameProperties()).cardRegistry()
    System.setProperty("game.recording.root", root.toString())
    System.setProperty("game.recording.engine-revision", text("engineSha"))
    val writer = requireNotNull(PrivateGameEvidence.fromSystemProperties(gameId)) as PrivateGameEvidence
    val session = GameSession(sessionId=gameId, cardRegistry=registry, evidenceSink=writer)
    session.engineFormat = com.wingedsheep.sdk.core.Format.Commander()
    val talrand = EntityId.of("qualification-talrand")
    val lathril = EntityId.of("qualification-lathril")
    val deckSpecs = mapOf(talrand to config.getValue("talrand").jsonObject,
        lathril to config.getValue("lathril").jsonObject)
    fun library(spec: JsonObject): Map<String, Int> {
        val cards = spec.getValue("cards").jsonObject.mapValues { it.value.jsonPrimitive.int }.toMutableMap()
        val commander = spec.getValue("commander").jsonPrimitive.content
        check(cards.values.sum() == 100 && cards[commander] == 1)
        cards.remove(commander)
        return cards
    }
    for ((seat, spec) in deckSpecs) {
        val ws = Mockito.mock(WebSocketSession::class.java)
        Mockito.`when`(ws.id).thenReturn("fixture-${seat.value}")
        val cards = library(spec)
        session.addPlayer(PlayerSession(ws, seat, seat.value), cards, spec.getValue("commander").jsonPrimitive.content)
    }
    val local = EngineAiPlayerController(registry, talrand, { session.getStateSnapshot() })
    val remote = CommanderGymPlayerController(lathril, URI.create(text("sidecarUrl")),
        Files.readString(Path.of(text("tokenPath"))).trim(), Duration.ofSeconds(120),
        profileId=text("bindingId"), gameSessionId=gameId, manualHumanGame=true,
        recorder=RecorderCallbackClient(Path.of(text("recorderSocket"))),
        parameterize={ offered, params -> if (params.isEmpty) offered.action else
            ActionParameterizer.apply(offered, params, requireNotNull(session.getStateSnapshot())) })
    for ((seat, spec) in deckSpecs) {
        val cards = library(spec)
        if (seat == talrand) local.setDeckList(cards) else remote.setDeckList(cards)
    }
    val deadline = System.nanoTime() + Duration.ofMinutes(90).toNanos()
    var actions = 0
    var lastTurn = 0
    var sameTurnActions = 0
    fun summary(status: String) {
        val state = session.getStateSnapshot()
        val value = buildJsonObject {
            put("status", status); put("gameId", gameId); put("actions", actions)
            put("turn", state?.turnNumber ?: 0); put("nativeTerminal", state?.gameOver == true)
            put("winnerId", state?.winnerId?.value?.let(::JsonPrimitive) ?: JsonNull)
            put("syntheticOpponent", "native-engine-talrand"); put("concessionUsed", false)
            put("format", "Commander"); put("libraryCardsPerSeat", 99)
        }
        Files.writeString(Path.of(text("resultPath")), value.toString())
        println(value)
    }
    fun cards(input: Map<EntityId, ServerMessage.MulliganCardInfo>) = input.mapValues { (_, c) ->
        CardSummary(name=c.name, manaCost=c.manaCost, typeLine=c.typeLine,
            power=c.power, toughness=c.toughness, oracleText=c.oracleText) }
    try {
        val initial = session.startGame()
        for (seat in deckSpecs.keys) {
            check(initial.lifeTotal(seat) == 40)
            check(initial.getZone(seat, com.wingedsheep.sdk.core.Zone.COMMAND).size == 1)
            check(initial.getZone(seat, com.wingedsheep.sdk.core.Zone.LIBRARY).size +
                initial.getZone(seat, com.wingedsheep.sdk.core.Zone.HAND).size == 99)
        }
        session.keepHand(talrand)
        while (!session.hasMulliganComplete(lathril)) {
            check(System.nanoTime() < deadline)
            val bottom = session.getChooseBottomCardsMessage(lathril)
            val result = if (bottom != null) {
                val evidence = requireNotNull(session.beginAiBottomEvidence(lathril, bottom))
                val chosen = remote.chooseRecordedBottomCards(BottomCardsInfo(bottom.hand,
                    bottom.cardsToPutOnBottom, cards=cards(bottom.cards)), evidence)
                session.executeRecordedBottom(lathril, evidence, chosen)
            } else {
                val m = session.getMulliganDecision(lathril)
                val evidence = requireNotNull(session.beginAiMulliganEvidence(lathril, m))
                val keep = remote.decideRecordedMulligan(MulliganInfo(m.hand, m.mulliganCount,
                    m.cardsToPutOnBottom, cards=cards(m.cards), isOnThePlay=m.isOnThePlay), evidence)
                session.executeRecordedMulligan(lathril, evidence, keep)
            }
            check(result !is GameSession.MulliganActionResult.Failure) { "Native mulligan rejected" }
        }
        while (!session.isGameOver()) {
            check(System.nanoTime() < deadline && actions < 5000) { "Full game budget exhausted" }
            val state = requireNotNull(session.getStateSnapshot())
            if (state.turnNumber != lastTurn) { lastTurn=state.turnNumber; sameTurnActions=0; summary("running") }
            check(++sameTurnActions <= 300) { "Native game stopped advancing" }
            val actor = state.pendingDecision?.let { state.actorFor(it.playerId) }
                ?: requireNotNull(state.priorityPlayerId)
            session.clearLastSentState(actor) // fixture consumes full masked views, not transport deltas
            val update = session.createStateUpdate(actor, emptyList(), useEngineDecisionIds=true) as ServerMessage.StateUpdate
            val result = if (actor == lathril) {
                val evidence = requireNotNull(session.beginAiCallbackEvidence(actor, update.state,
                    update.legalActions, update.pendingDecision, update.interactionEpoch, emptyList()))
                val choice = remote.chooseRecordedCallback(update.state, update.legalActions,
                    update.pendingDecision, emptyList(), evidence)
                val action: GameAction = when (val response=choice.response) {
                    is ActionResponse.SubmitAction -> response.action
                    is ActionResponse.SubmitDecision -> SubmitDecision(response.playerId, response.response)
                }
                session.executeRecordedAiCallback(actor, action, update.interactionEpoch, evidence, choice)
            } else {
                val response = local.chooseAction(update.state, update.legalActions, update.pendingDecision, emptyList())
                val action = when (response) {
                    is ActionResponse.SubmitAction -> response.action
                    is ActionResponse.SubmitDecision -> SubmitDecision(response.playerId, response.response)
                }
                session.executeAiAction(actor, action, update.interactionEpoch)
            }
            check(result != null && result !is GameSession.ActionResult.Failure) { "Native action rejected" }
            actions++
        }
        check(writer.healthy()) { "Native evidence unhealthy" }
        summary("native-terminal")
    } catch (error: Throwable) {
        summary("failed-incomplete")
        throw error
    } finally { writer.close() }
}
