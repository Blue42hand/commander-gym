package org.commandergym.argentum

import com.wingedsheep.engine.core.*
import com.wingedsheep.engine.state.GameState
import com.wingedsheep.gameserver.config.GameBeansConfig
import com.wingedsheep.gameserver.config.GameProperties
import kotlinx.serialization.json.*
import java.nio.file.Files
import java.nio.file.Path

/** Offline paired replay of a payment window. Never sends snapshots to a pilot. */
object ManaPaymentSavedStateProbe {
    @JvmStatic fun main(args: Array<String>) {
        val json = Json { serializersModule = engineSerializersModule; allowStructuredMapKeys = true }
        val fixture = Json.parseToJsonElement(Files.readString(Path.of(args[0]))).jsonObject
        val initial = json.decodeFromJsonElement<GameState>(fixture.getValue("state"))
        val processor = ActionProcessor(EngineServices(GameBeansConfig(GameProperties()).cardRegistry()))
        fun step(state: GameState, raw: JsonObject): GameState {
            val action = json.decodeFromJsonElement<GameAction>(raw.getValue("action"))
            val completed = raw["params"]?.let {
                ActionParameterizer.apply(action,json.decodeFromJsonElement<ActionParams>(it),state)
            } ?: action
            val result = processor.process(state,completed).result
            check(result.outcome !is Outcome.Rejected) { "Recorded payment step rejected: ${result.outcome}" }
            return result.state
        }
        var baseline=initial
        for (raw in fixture.getValue("baselineSteps").jsonArray) baseline=step(baseline,raw.jsonObject)
        val spell=fixture.getValue("spellId").jsonPrimitive.content
        check(baseline.stack.none { it.value==spell }) { "Historical payment unexpectedly cast the spell" }
        val replacement=step(initial,fixture.getValue("replacement").jsonObject)
        check(replacement.stack.any { it.value==spell }) { "Corrected payment did not cast the spell" }
        check(replacement.pendingDecision==null)
        val report=buildJsonObject {
            put("scope","Same saved native payment window; recorded manual sequence versus a policy-selected native source plan")
            put("baselineSpellOnStack",false)
            put("replacementSpellOnStack",true)
            put("replacementNoPendingDecision",true)
            put("baselineSteps",fixture.getValue("baselineSteps").jsonArray.size)
        }
        Files.writeString(Path.of(args[1]),report.toString());println(report)
    }
}
