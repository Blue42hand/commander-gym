package org.commandergym.argentum

import com.wingedsheep.engine.core.*
import com.wingedsheep.engine.state.GameState
import com.wingedsheep.gameserver.config.GameBeansConfig
import com.wingedsheep.gameserver.config.GameProperties
import kotlinx.serialization.json.*
import java.nio.file.Files
import java.nio.file.Path

/** Explicit private offline recovery proof; full snapshots never enter pilot prompts. */
object SavedStateActionProbe {
    @JvmStatic
    fun main(args: Array<String>) {
        val json = Json { serializersModule = engineSerializersModule; allowStructuredMapKeys = true }
        val fixture = Json.parseToJsonElement(Files.readString(Path.of(args[0]))).jsonObject
        val state = json.decodeFromJsonElement<GameState>(fixture.getValue("state"))
        val services = EngineServices(GameBeansConfig(GameProperties()).cardRegistry())
        val processor = ActionProcessor(services)
        fun action(key: String): GameAction {
            val template = json.decodeFromJsonElement<GameAction>(fixture.getValue(key))
            val params = fixture[key.removeSuffix("Action") + "Params"] ?: return template
            check(template is CastSpell)
            val offered = services.legalActionEnumerator.enumerate(state, template.playerId).single {
                val cast = it.action as? CastSpell
                cast?.cardId == template.cardId && cast.declaredCostSlot == template.declaredCostSlot &&
                    cast.useAlternativeCost == template.useAlternativeCost && cast.alternativeCostType == template.alternativeCostType
            }
            return ActionParameterizer.apply(offered, json.decodeFromJsonElement<ActionParams>(params), state)
        }
        val baseline = processor.process(state, action("baselineAction")).result
        check(baseline.outcome is Outcome.Rejected)
        val expected = fixture["expectedFailureText"]?.jsonPrimitive?.content ?: "Cannot pay attack tax"
        check(baseline.outcome.toString().contains(expected))
        val replacement = processor.process(state, action("replacementAction")).result
        check(replacement.outcome is Outcome.Done) { "Recovery rejected: ${replacement.outcome}" }
        check(replacement.state.pendingDecision == null)
        val spell = fixture["expectedSpellOnStack"]?.jsonPrimitive?.content
        if (spell != null) check(replacement.state.stack.any { it.value == spell })
        val followup = fixture["followupAction"]?.let {
            processor.process(replacement.state, action("followupAction")).result.also { result ->
                check(result.outcome is Outcome.Done) { "Followup rejected: ${result.outcome}" }
                check(result.state.pendingDecision == null)
            }
        }
        val report = buildJsonObject {
            put("scope", "Same saved native checkpoint; rejected baseline and corrected native action executed offline")
            put("baselineOutcome", baseline.outcome.toString())
            put("replacementAccepted", true)
            if (followup != null) put("followupAccepted", true)
            if (spell != null) put("spellOnStack", true)
            put("noPendingPayment", true)
        }
        Files.writeString(Path.of(args[1]), report.toString())
        println(report)
    }
}
