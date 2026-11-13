package org.commandergym.argentum

import com.wingedsheep.engine.core.*
import com.wingedsheep.engine.state.GameState
import com.wingedsheep.engine.state.ZoneKey
import com.wingedsheep.engine.mechanics.layers.StateProjector
import com.wingedsheep.engine.state.components.stack.SpellOnStackComponent
import com.wingedsheep.gameserver.config.GameBeansConfig
import com.wingedsheep.gameserver.config.GameProperties
import com.wingedsheep.sdk.core.Zone
import com.wingedsheep.sdk.model.EntityId
import kotlinx.serialization.json.*
import java.nio.file.Files
import java.nio.file.Path

/** Compare the recorded protection spell with immediate native modal commander rescue. */
object CommanderRescueSavedStateProbe {
    @JvmStatic fun main(args:Array<String>) {
        val json=Json { serializersModule=engineSerializersModule;allowStructuredMapKeys=true }
        val f=Json.parseToJsonElement(Files.readString(Path.of(args[0]))).jsonObject
        val initial=json.decodeFromJsonElement<GameState>(f.getValue("state"))
        val processor=ActionProcessor(EngineServices(GameBeansConfig(GameProperties()).cardRegistry()))
        fun id(key:String)=EntityId(f.getValue(key).jsonPrimitive.content)
        val player=id("playerId");val commander=id("commanderId");val aura=id("auraId");val conserved=id("conservedCardId")
        val battlefield=ZoneKey(player,Zone.BATTLEFIELD);val hand=ZoneKey(player,Zone.HAND)
        val projector=StateProjector()
        check(commander in initial.getZone(battlefield) && projector.project(initial).hasLostAllAbilities(commander))
        fun apply(state:GameState,action:GameAction):GameState {
            val result=processor.process(state,action).result
            check(result.outcome !is Outcome.Rejected) { "Recovery branch rejected: ${result.outcome}" }
            return result.state
        }
        fun branch(name:String):GameState {
            val template=json.decodeFromJsonElement<GameAction>(f.getValue(name+"Action"))
            val params=json.decodeFromJsonElement<ActionParams>(f.getValue(name+"Params"))
            var state=apply(initial,ActionParameterizer.apply(template,params,initial))
            val spell=id(name+"SpellId")
            check(spell in state.stack)
            if(name=="replacement") check(state.getEntity(spell)?.get<SpellOnStackComponent>()?.chosenModes==listOf(1))
            var steps=0
            while(spell in state.stack || state.pendingDecision!=null) {
                check(++steps<=24)
                check(state.pendingDecision==null) { "Unexpected recovery continuation: ${state.pendingDecision}" }
                state=apply(state,PassPriority(requireNotNull(state.priorityPlayerId)))
            }
            return state
        }
        val baseline=branch("baseline")
        check(projector.project(baseline).hasLostAllAbilities(commander))
        check(conserved !in baseline.getZone(hand))
        val replacement=branch("replacement")
        check(commander in replacement.getZone(battlefield))
        check(!projector.project(replacement).hasLostAllAbilities(commander))
        check(aura in replacement.getZone(ZoneKey(id("auraOwner"),Zone.GRAVEYARD)))
        check(conserved in replacement.getZone(hand))
        val report=buildJsonObject {
            put("scope","Exact saved native state before Heroic Intervention: recorded cast versus policy-selected Golgari Charm destruction mode, each resolved through native priority without opposing responses")
            put("bothCastsAccepted",true);put("replacementMode",1)
            put("baselineCommanderStillSuppressed",true);put("replacementCommanderOnBattlefield",true)
            put("replacementPrintedAbilitiesRestored",true);put("hostileAuraInGraveyard",true)
            put("protectionCardConserved",true)
        }
        Files.writeString(Path.of(args[1]),report.toString());println(report)
    }
}
