package org.commandergym.argentum

import com.wingedsheep.engine.core.*
import com.wingedsheep.engine.state.GameState
import com.wingedsheep.engine.state.ZoneKey
import com.wingedsheep.gameserver.config.GameBeansConfig
import com.wingedsheep.gameserver.config.GameProperties
import com.wingedsheep.sdk.core.Zone
import com.wingedsheep.sdk.model.EntityId
import kotlinx.serialization.json.*
import java.nio.file.Files
import java.nio.file.Path

/** Exact saved-state proof of waiting for an unopposed conditional counter. */
object CounterSavedStateProbe {
    @JvmStatic fun main(args:Array<String>) {
        val json=Json { serializersModule=engineSerializersModule;allowStructuredMapKeys=true }
        val fixture=Json.parseToJsonElement(Files.readString(Path.of(args[0]))).jsonObject
        val initial=json.decodeFromJsonElement<GameState>(fixture.getValue("state"))
        val processor=ActionProcessor(EngineServices(GameBeansConfig(GameProperties()).cardRegistry()))
        fun apply(state:GameState,action:GameAction):GameState {
            val result=processor.process(state,action).result
            check(result.outcome !is Outcome.Rejected) { "Saved-state action rejected: ${result.outcome}" }
            return result.state
        }
        val template=json.decodeFromJsonElement<GameAction>(fixture.getValue("baselineAction"))
        val original=ActionParameterizer.apply(template,json.decodeFromJsonElement<ActionParams>(fixture.getValue("baselineParams")),initial)
        val baseline=apply(initial,original)
        val player=EntityId(fixture.getValue("playerId").jsonPrimitive.content)
        val ownCounter=EntityId(fixture.getValue("ownCounterId").jsonPrimitive.content)
        val target=EntityId(fixture.getValue("targetId").jsonPrimitive.content)
        val conserved=fixture.getValue("conservedIds").jsonArray.map { EntityId(it.jsonPrimitive.content) }
        check(conserved.all { it in initial.getZone(ZoneKey(player,Zone.HAND)) })
        check(conserved.none { it in baseline.getZone(ZoneKey(player,Zone.HAND)) })
        var replacement=apply(initial,json.decodeFromJsonElement<GameAction>(fixture.getValue("replacementAction")))
        var steps=0
        while (ownCounter in replacement.stack || replacement.pendingDecision != null) {
            check(++steps<=24)
            val pending=replacement.pendingDecision
            val action=when(pending) {
                null -> PassPriority(requireNotNull(replacement.priorityPlayerId))
                is SelectManaSourcesDecision -> {
                    check(pending.canDecline)
                    SubmitDecision(pending.playerId,ManaSourcesSelectedResponse(pending.id,autoPay=false,declined=true))
                }
                is YesNoDecision -> SubmitDecision(pending.playerId,YesNoResponse(pending.id,false))
                else -> error("Unexpected counter continuation: ${pending::class.simpleName}")
            }
            replacement=apply(replacement,action)
        }
        check(target !in replacement.stack)
        val targetOwner=EntityId(fixture.getValue("targetOwnerId").jsonPrimitive.content)
        check(target in replacement.getZone(ZoneKey(targetOwner,Zone.GRAVEYARD)))
        check(conserved.all { it in replacement.getZone(ZoneKey(player,Zone.HAND)) })
        val report=buildJsonObject {
            put("scope","Same saved native checkpoint: recorded backup cast versus policy pass, then native priority and declined conditional-payment branch; no model calls or new game")
            put("baselineBackupAccepted",true);put("baselineCardsSpent",conserved.size)
            put("replacementPassAccepted",true);put("pendingCounterResolved",true)
            put("originalTargetRemovedFromStack",true);put("originalTargetCounteredToGraveyard",true);put("replacementCardsConserved",conserved.size)
        }
        Files.writeString(Path.of(args[1]),report.toString());println(report)
    }
}
