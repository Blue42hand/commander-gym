package org.commandergym.argentum

import com.wingedsheep.engine.core.*
import com.wingedsheep.engine.state.GameState
import com.wingedsheep.gameserver.config.GameBeansConfig
import com.wingedsheep.gameserver.config.GameProperties
import kotlinx.serialization.json.*
import java.nio.file.Files
import java.nio.file.Path

/** Private offline native replay. Full engine snapshots never enter pilot prompts. */
object ConvokeSavedStateProbe {
    @JvmStatic
    fun main(args: Array<String>) {
        fun path(name: String) = Path.of(requireNotNull(System.getenv(name)))
        val json = Json { serializersModule = engineSerializersModule; allowStructuredMapKeys = true }
        val fixture = Json.parseToJsonElement(Files.readString(path("COMMANDER_GYM_CONVOKE_REPLAY_INPUT"))).jsonObject
        val state = json.decodeFromJsonElement<GameState>(fixture.getValue("result").jsonObject.getValue("state"))
        val original = json.decodeFromJsonElement<GameAction>(fixture.getValue("action")) as CastSpell
        val params = json.decodeFromString<ActionParams>(Files.readString(path("COMMANDER_GYM_CONVOKE_REPLAY_PARAMS")))
        val registry = GameBeansConfig(GameProperties()).cardRegistry()
        val services = EngineServices(registry)
        val processor = ActionProcessor(services)
        val baseline = processor.process(state, original).result
        check(baseline.outcome is Outcome.Rejected) { "Historical incomplete payment unexpectedly accepted" }
        val offered = services.legalActionEnumerator.enumerate(state, original.playerId).single {
            val action = it.action as? CastSpell
            action?.cardId == original.cardId && action.declaredCostSlot == original.declaredCostSlot
        }
        check(offered.affordable)
        val completed = ActionParameterizer.apply(offered, params, state) as CastSpell
        val result = processor.process(state, completed).result
        check(result.outcome is Outcome.Done) { "Completed payment rejected: ${result.outcome}" }
        check(completed.cardId in result.state.stack) { "Spell did not reach the native stack" }
        val report = buildJsonObject {
            put("scope", "Same saved native state; baseline and completed payment executed without model calls or a new game")
            put("baselineRejected", true)
            put("baselineOutcome", baseline.outcome.toString())
            put("completedAccepted", true)
            put("spellOnStack", true)
            put("announcedX", requireNotNull(completed.xValue))
            put("params", json.encodeToJsonElement(params))
            put("newNativeParameterSpec", json.encodeToJsonElement(ActionParameterizer.spec(offered)))
        }
        Files.writeString(path("COMMANDER_GYM_CONVOKE_REPLAY_OUTPUT"), report.toString())
        println(report)
    }
}
