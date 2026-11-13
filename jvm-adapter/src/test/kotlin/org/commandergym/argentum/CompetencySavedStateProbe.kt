package org.commandergym.argentum

import com.wingedsheep.engine.core.*
import com.wingedsheep.engine.state.GameState
import com.wingedsheep.engine.state.ZoneKey
import com.wingedsheep.engine.mechanics.layers.StateProjector
import com.wingedsheep.sdk.core.Zone
import com.wingedsheep.sdk.model.EntityId
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
            put("scope","Offline branches executed from authenticated native checkpoints; offered comparisons are explicitly labelled; no provider calls or new game")
            putJsonArray("cases") {
                for (element in fixture.getValue("cases").jsonArray) {
                    val entry=element.jsonObject
                    val initial=json.decodeFromJsonElement<GameState>(entry.getValue("state"))
                    add(buildJsonObject {
                        put("callbackSequence",entry.getValue("sequence"))
                        entry["baselineIsOfferedComparison"]?.let { put("baselineIsOfferedComparison",it) }
                        for (branch in listOf("baseline","replacement")) {
                            val choice=entry.getValue(branch).jsonObject
                            val template=json.decodeFromJsonElement<GameAction>(choice.getValue("action"))
                            val params=choice["params"]
                            val action=if(params==null) template else ActionParameterizer.apply(template,json.decodeFromJsonElement<ActionParams>(params),initial)
                            var result=processor.process(initial,action).result
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
                            val modalQuestions=mutableListOf<JsonElement>()
                            val modalResponses=mutableListOf<JsonElement>()
                            var modalCompleted=false
                            var modalResolved=false
                            val modal=entry["completeModalPlan"]?.jsonObject
                            if(modal!=null) {
                                val source=json.decodeFromJsonElement<EntityId>(modal.getValue("source"))
                                val commander=json.decodeFromJsonElement<EntityId>(entry.getValue("restoredCommander"))
                                val aura=json.decodeFromJsonElement<EntityId>(entry.getValue("bouncedAura"))
                                val projector=StateProjector()
                                check(projector.project(initial).hasLostAllAbilities(commander))
                                if(branch=="replacement") {
                                    var steps=0
                                    while(result.state.pendingDecision!=null) {
                                        check(++steps<=8)
                                        val question=requireNotNull(result.state.pendingDecision)
                                        modalQuestions.add(json.encodeToJsonElement(PendingDecision.serializer(),question))
                                        val modes=modal.getValue("modes").jsonArray.map { it.jsonObject }
                                        val response:DecisionResponse=when(question) {
                                            is ChooseOptionDecision -> {
                                                val selected=modes.first { it.getValue("description").jsonPrimitive.content in question.options }
                                                OptionChosenResponse(question.id,question.options.indexOf(selected.getValue("description").jsonPrimitive.content))
                                            }
                                            is ChooseTargetsDecision -> {
                                                check(question.targetRequirements.size==1)
                                                val mode=modes.single { it["description"]?.jsonPrimitive?.content==question.context.effectHint }
                                                val target=json.decodeFromJsonElement<EntityId>(mode.getValue("target"))
                                                val slot=question.targetRequirements.single()
                                                check(slot.minTargets==1 && slot.maxTargets==1 && target in question.legalTargets.getValue(slot.index))
                                                TargetsResponse(question.id,mapOf(slot.index to listOf(target)))
                                            }
                                            else -> error("Unexpected modal stage: $question")
                                        }
                                        modalResponses.add(json.encodeToJsonElement(DecisionResponse.serializer(),response))
                                        result=processor.process(result.state,SubmitDecision(question.playerId,response)).result
                                        check(result.outcome !is Outcome.Rejected)
                                    }
                                    check(source in result.state.stack && source !in result.state.getZone(ZoneKey(action.playerId,Zone.HAND)))
                                    modalCompleted=true
                                    var passes=0
                                    while(source in result.state.stack) {
                                        check(++passes<=64 && result.state.pendingDecision==null)
                                        result=processor.process(result.state,PassPriority(requireNotNull(result.state.priorityPlayerId))).result
                                        check(result.outcome !is Outcome.Rejected)
                                    }
                                    check(result.state.pendingDecision==null)
                                    check(!projector.project(result.state).hasLostAllAbilities(commander))
                                    check(result.state.turnOrder.any { aura in result.state.getZone(ZoneKey(it,Zone.HAND)) })
                                    check(result.state.getZone(ZoneKey(action.playerId,Zone.LIBRARY)).size==initial.getZone(ZoneKey(action.playerId,Zone.LIBRARY)).size-1)
                                    modalResolved=true
                                } else {
                                    check(result.state.pendingDecision==null && source in result.state.getZone(ZoneKey(action.playerId,Zone.HAND)))
                                    check(projector.project(result.state).hasLostAllAbilities(commander))
                                }
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
                            if(entry["freeLibraryReorder"]?.jsonPrimitive?.boolean==true) {
                                check(action is SubmitDecision && action.response is YesNoResponse)
                                if(branch=="replacement") {
                                    check(result.state.pendingDecision is ReorderLibraryDecision)
                                } else {
                                    check(result.state.pendingDecision==null)
                                }
                            }
                            if(branch=="replacement" && entry["announcedXCast"]?.jsonPrimitive?.boolean==true) {
                                check(action is CastSpell && action.xValue==1)
                                check(result.outcome is Outcome.Done && result.state.pendingDecision==null)
                                check(action.cardId in result.state.stack)
                            }
                            putJsonObject(branch) {
                                put("accepted",result.outcome !is Outcome.Rejected)
                                if(modal!=null) { put("modalCastCompleted",modalCompleted);put("resolvedWithOpponentsPassing",modalResolved);put("commanderRestored",modalResolved);put("nativeCastingQuestions",JsonArray(modalQuestions));put("nativeCastingResponses",JsonArray(modalResponses)) }
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
