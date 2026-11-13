package org.commandergym.argentum

import com.wingedsheep.engine.core.*
import com.wingedsheep.engine.state.GameState
import com.wingedsheep.engine.state.ZoneKey
import com.wingedsheep.engine.mechanics.layers.StateProjector
import com.wingedsheep.sdk.core.Zone
import com.wingedsheep.gameserver.config.GameBeansConfig
import com.wingedsheep.gameserver.config.GameProperties
import kotlinx.serialization.json.*
import java.nio.file.Files
import java.nio.file.Path

/** Offline legality checks on both branches of authenticated pre-callback states. */
object CompetencySavedStateProbe {
    @JvmStatic fun main(args:Array<String>) {
        val json=Json { serializersModule=engineSerializersModule;allowStructuredMapKeys=true }
        val fixture=Json.parseToJsonElement(Files.readString(Path.of(args[0]))).jsonObject
        val services=EngineServices(GameBeansConfig(GameProperties()).cardRegistry())
        val processor=ActionProcessor(services)
        val report=buildJsonObject {
            put("scope","Both recorded and bounded replacement choices executed from the same authenticated native checkpoints; no provider calls or new game")
            putJsonArray("cases") {
                for (element in fixture.getValue("cases").jsonArray) {
                    val entry=element.jsonObject
                    val initial=json.decodeFromJsonElement<GameState>(entry.getValue("state"))
                    add(buildJsonObject {
                        put("callbackSequence",entry.getValue("sequence"))
                        for (branch in listOf("baseline","replacement")) {
                            val choice=entry.getValue(branch).jsonObject
                            val template=json.decodeFromJsonElement<GameAction>(choice.getValue("action"))
                            val params=choice["params"]
                            val action=if(params==null) template else ActionParameterizer.apply(template,json.decodeFromJsonElement<ActionParams>(params),initial)
                            val result=processor.process(initial,action).result
                            val expectedError=if(branch=="baseline") entry["expectedBaselineError"]?.jsonPrimitive?.content else null
                            if(expectedError!=null) {
                                check(result.outcome is Outcome.Rejected && expectedError in result.outcome.toString())
                            } else {
                                check(result.outcome !is Outcome.Rejected) { "$branch rejected for callback ${entry["sequence"]}: ${result.outcome}" }
                            }
                            if(branch=="replacement" && entry["delvePayment"]?.jsonPrimitive?.boolean==true) {
                                check(action is CastSpell)
                                check(result.outcome is Outcome.Done && result.state.pendingDecision==null)
                                check(action.cardId in result.state.stack)
                            }
                            var flyers:Int?=null
                            if(branch=="replacement" && entry["ordinaryManaCast"]?.jsonPrimitive?.boolean==true) {
                                check(action is CastSpell && !action.useAlternativeCost)
                                check(result.outcome is Outcome.Done && result.state.pendingDecision==null)
                                check(action.cardId in result.state.stack)
                                val projected=StateProjector().project(initial)
                                flyers=initial.getZone(ZoneKey(action.playerId,Zone.BATTLEFIELD)).count { "FLYING" in projected.getKeywords(it) }
                                check(flyers<4)
                            }
                            if(branch=="replacement" && entry["landDevelopment"]?.jsonPrimitive?.boolean==true) {
                                check(action is PlayLand)
                                check(result.outcome is Outcome.Done && result.state.pendingDecision==null)
                                check(action.cardId in initial.getZone(ZoneKey(action.playerId,Zone.HAND)))
                                check(action.cardId in result.state.getZone(ZoneKey(action.playerId,Zone.BATTLEFIELD)))
                                check(action.cardId !in result.state.getZone(ZoneKey(action.playerId,Zone.HAND)))
                            }
                            putJsonObject(branch) {
                                put("accepted",result.outcome !is Outcome.Rejected)
                                put("outcome",result.outcome.toString())
                                put("pendingDecision",result.state.pendingDecision?.javaClass?.simpleName)
                                put("actionType",action.javaClass.simpleName)
                                if(flyers!=null) { put("ordinaryManaCastAccepted",true);put("initialFlyingCreatures",flyers) }
                            }
                        }
                    })
                }
            }
        }
        Files.writeString(Path.of(args[1]),report.toString());println(report)
    }
}
