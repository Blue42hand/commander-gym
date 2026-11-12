package org.commandergym.argentum

import com.wingedsheep.engine.core.*
import com.wingedsheep.engine.state.GameState
import com.wingedsheep.engine.state.components.stack.SpellOnStackComponent
import com.wingedsheep.gameserver.config.GameBeansConfig
import com.wingedsheep.gameserver.config.GameProperties
import com.wingedsheep.sdk.model.EntityId
import kotlinx.serialization.json.*
import java.nio.file.Files
import java.nio.file.Path

/** Execute a policy-produced public mode plan against its exact native saved state. */
object ModalSavedStateProbe {
    @JvmStatic fun main(args: Array<String>) {
        val json=Json { serializersModule=engineSerializersModule;allowStructuredMapKeys=true }
        val input=Json.parseToJsonElement(Files.readString(Path.of(args[0]))).jsonObject
        val initial=json.decodeFromJsonElement<GameState>(input.getValue("state"))
        val processor=ActionProcessor(EngineServices(GameBeansConfig(GameProperties()).cardRegistry()))
        val spell=EntityId(input.getValue("spellId").jsonPrimitive.content)
        fun apply(state:GameState,action:GameAction):GameState {
            val result=processor.process(state,action).result
            check(result.outcome !is Outcome.Rejected) { "Modal plan rejected: ${result.outcome}" }
            return result.state
        }
        var baseline=initial
        for (row in input.getValue("baselineSteps").jsonArray) {
            baseline=apply(baseline,json.decodeFromJsonElement<GameAction>(row.jsonObject.getValue("action")))
        }
        check(spell in baseline.stack)
        val original=requireNotNull(baseline.getEntity(spell)?.get<SpellOnStackComponent>())
        check(original.chosenModes.size==1)
        val modes=input.getValue("replacementPlan").jsonArray.map { it.jsonObject }
        val chosen=mutableListOf<JsonObject>();var replacement=initial
        repeat(16) {
            if (spell !in replacement.stack) {
                val pending=requireNotNull(replacement.pendingDecision)
                val response:DecisionResponse=when(pending) {
                    is ChooseOptionDecision -> {
                        val mode=modes.firstOrNull { it.getValue("description").jsonPrimitive.content in pending.options }
                        val label=mode?.getValue("description")?.jsonPrimitive?.content ?: "Done"
                        val index=pending.options.indexOf(label);check(index>=0)
                        if (mode!=null) chosen.add(mode)
                        OptionChosenResponse(pending.id,index)
                    }
                    is ChooseTargetsDecision -> {
                        val label=requireNotNull(pending.context.effectHint)
                        val mode=chosen.single { it.getValue("description").jsonPrimitive.content==label }
                        val target=EntityId(mode.getValue("target").jsonPrimitive.content)
                        val slot=pending.targetRequirements.single().index
                        check(target in pending.legalTargets.getValue(slot))
                        TargetsResponse(pending.id,mapOf(slot to listOf(target)))
                    }
                    else -> error("Unexpected modal continuation: ${pending::class.simpleName}")
                }
                replacement=apply(replacement,SubmitDecision(pending.playerId,response))
            }
        }
        check(spell in replacement.stack)
        val completed=requireNotNull(replacement.getEntity(spell)?.get<SpellOnStackComponent>())
        check(completed.chosenModes.size==4)
        check(completed.modeTargetsOrdered.flatten().distinct().size==4)
        val report=buildJsonObject {
            put("scope","Same native modal-choice checkpoint; recorded single mode versus policy-produced independent removal plan; no model calls")
            put("baselineModes",original.chosenModes.size);put("replacementModes",completed.chosenModes.size)
            put("replacementDistinctTargets",completed.modeTargetsOrdered.flatten().distinct().size)
            put("bothCastsAccepted",true)
        }
        Files.writeString(Path.of(args[1]),report.toString());println(report)
    }
}
