package org.commandergym.argentum

import com.wingedsheep.engine.core.*
import com.wingedsheep.engine.state.GameState
import com.wingedsheep.engine.state.ZoneKey
import com.wingedsheep.engine.state.components.stack.ChosenTarget
import com.wingedsheep.engine.state.components.identity.CardComponent
import com.wingedsheep.engine.state.components.identity.ControllerComponent
import com.wingedsheep.engine.state.components.identity.LifeTotalComponent
import com.wingedsheep.engine.state.components.battlefield.TappedComponent
import com.wingedsheep.engine.state.components.battlefield.AttachedToComponent
import com.wingedsheep.engine.state.components.player.ManaPoolComponent
import com.wingedsheep.engine.mechanics.layers.StateProjector
import com.wingedsheep.engine.state.components.battlefield.CountersComponent
import com.wingedsheep.sdk.core.CounterType
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
                        entry["replacementLabel"]?.let { put("replacementLabel",it) }
                        if(entry["exportInitialObservation"]?.jsonPrimitive?.boolean==true) {
                            val actor=json.decodeFromJsonElement<GameAction>(entry.getValue("replacement").jsonObject.getValue("action")).playerId
                            val offers=services.legalActionEnumerator.enumerate(initial,actor)
                            val expectedUnaffordable=entry["nativeUnaffordableSpell"]?.let { json.decodeFromJsonElement<EntityId>(it) }
                            if(expectedUnaffordable!=null) {
                                val offered=offers.filter { (it.action as? CastSpell)?.cardId==expectedUnaffordable }
                                check(offered.none { it.affordable })
                                put("unaffordableCastConfirmed",true)
                                put("unaffordableOfferRetained",offered.isNotEmpty())
                            }
                            val view=com.wingedsheep.engine.view.ClientStateTransformer(services.cardRegistry,
                                predicateEvaluator=services.predicateEvaluator).transform(initial,actor)
                            val presented=com.wingedsheep.engine.view.LegalActionEnricher(services.manaSolver,services.cardRegistry).enrich(offers,initial,actor)
                            val evidence=com.wingedsheep.gameserver.recording.actionCallbackEvidence(view,presented,initial.pendingDecision,actor.value,emptyList())
                            put("recomputedNativeObservation",Json.parseToJsonElement(evidence.observationBody))
                        }

                        if(entry["diagnoseSelected"]?.jsonPrimitive?.boolean==true) {
                            val frame=(initial.continuationStack.last() as Suspension).answer as ManaActionPaymentContinuation
                            val actor=frame.action.playerId
                            val plan=services.manaSolver.planSelectedActivations(initial,actor,frame.cost,
                                frame.paymentContext,setOf(EntityId("e482")))
                            put("plannerResult",plan.javaClass.simpleName)
                            if(plan is com.wingedsheep.engine.mechanics.mana.ScopedManaPlanResult.Unknown)
                                put("plannerLimits",plan.reasons.toString())
                        }

                        entry["baselineIsOfferedComparison"]?.let { put("baselineIsOfferedComparison",it) }
                        for (branch in if(entry["replacementOnly"]?.jsonPrimitive?.boolean==true) listOf("replacement") else listOf("baseline","replacement")) {
                            val choice=entry.getValue(branch).jsonObject
                            val template=json.decodeFromJsonElement<GameAction>(choice.getValue("action"))
                            val params=choice["params"]
                            fun parameterized():GameAction {
                                if(params==null)return template
                                val parsed=json.decodeFromJsonElement<ActionParams>(params)
                                return if(entry["usePresentedAction"]?.jsonPrimitive?.boolean==true) {
                                    val offered=services.legalActionEnumerator.enumerate(initial,template.playerId).single { it.action==template }
                                    ActionParameterizer.apply(offered,parsed,initial)
                                } else ActionParameterizer.apply(template,parsed,initial)
                            }
                            val expectedParameterError=if(branch=="baseline") entry["expectedBaselineParameterError"]?.jsonPrimitive?.content else null
                            if(expectedParameterError!=null) {
                                val failure=runCatching { parameterized() }.exceptionOrNull()
                                check(failure is IllegalArgumentException && expectedParameterError in failure.message.orEmpty())
                                putJsonObject(branch) { put("accepted",false);put("stage","native-parameterization");put("error",failure.message) }
                                continue
                            }
                            val action=parameterized()
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
                            if(branch=="replacement" && entry["manaBeforeSacrifice"]?.jsonPrimitive?.boolean==true) {
                                check(result.outcome is Outcome.Done && result.state.pendingDecision==null)
                                val next=entry.getValue("followingCast").jsonObject
                                val template=json.decodeFromJsonElement<GameAction>(next.getValue("action"))
                                val following=ActionParameterizer.apply(template,json.decodeFromJsonElement<ActionParams>(next.getValue("params")),result.state)
                                check(following is CastSpell)
                                result=processor.process(result.state,following).result
                                check(result.outcome is Outcome.Done && result.state.pendingDecision==null)
                                check(following.cardId in result.state.stack)
                            }
                            if(branch=="replacement" && entry["completeManualPayment"]?.jsonPrimitive?.boolean==true) {
                                val payment=entry.getValue("manualPayment").jsonObject
                                val expectedColor=json.decodeFromJsonElement<com.wingedsheep.sdk.core.Color>(payment.getValue("color"))
                                val expectedSpell=json.decodeFromJsonElement<EntityId>(payment.getValue("spell"))
                                val spentSource=json.decodeFromJsonElement<EntityId>(payment.getValue("spentSource"))
                                val preservedSource=json.decodeFromJsonElement<EntityId>(payment.getValue("preservedSource"))
                                check(result.outcome is Outcome.Paused)
                                fun maskedObservation():JsonElement {
                                    val view=com.wingedsheep.engine.view.ClientStateTransformer(services.cardRegistry,
                                        predicateEvaluator=services.predicateEvaluator).transform(result.state,action.playerId)
                                    val offers=if(com.wingedsheep.engine.mechanics.mana.ManaPaymentWindow.openFor(result.state,action.playerId)!=null)
                                        services.legalActionEnumerator.enumerateManaAbilities(result.state,action.playerId)
                                        else if(result.state.priorityPlayerId==action.playerId)services.legalActionEnumerator.enumerate(result.state,action.playerId)
                                        else emptyList()
                                    val legal=com.wingedsheep.engine.view.LegalActionEnricher(services.manaSolver,services.cardRegistry).enrich(offers,result.state,action.playerId)
                                    val evidence=com.wingedsheep.gameserver.recording.actionCallbackEvidence(view,legal,
                                        result.state.pendingDecision,action.playerId.value,emptyList())
                                    return Json.parseToJsonElement(evidence.observationBody)
                                }
                                if(result.state.pendingDecision is ChooseColorDecision) {
                                    put("manualColorObservation",maskedObservation())
                                    val colorQuestion=result.state.pendingDecision as ChooseColorDecision
                                    check(colorQuestion.availableColors.contains(expectedColor))
                                    result=processor.process(result.state,SubmitDecision(action.playerId,
                                        json.decodeFromJsonElement<DecisionResponse>(payment.getValue("colorResponse")))).result
                                }
                                put("fundedPaymentObservation",maskedObservation())
                                val question=result.state.pendingDecision as SelectManaSourcesDecision
                                check(question.canAutoPayNow==true)
                                result=processor.process(result.state,SubmitDecision(action.playerId,
                                    json.decodeFromJsonElement<DecisionResponse>(payment.getValue("fundedResponse")))).result
                                check(result.outcome is Outcome.Done && result.state.pendingDecision==null)
                                check(expectedSpell in result.state.stack)
                                check(spentSource !in result.state.getBattlefield())
                                check(preservedSource in result.state.getBattlefield())
                                put("completedManualPayment",true)
                            }
                            val emptyDraw=entry["emptyDrawComparison"]?.jsonObject
                            if(emptyDraw!=null) {
                                val source=json.decodeFromJsonElement<EntityId>(emptyDraw.getValue("source"))
                                val beforeLibrary=initial.getZone(ZoneKey(action.playerId,Zone.LIBRARY)).size
                                val beforeHand=initial.getZone(ZoneKey(action.playerId,Zone.HAND)).size
                                if(branch=="baseline") {
                                    var passes=0
                                    while(result.state.stack.isNotEmpty()) {
                                        check(++passes<=64 && result.state.pendingDecision==null)
                                        result=processor.process(result.state,PassPriority(requireNotNull(result.state.priorityPlayerId))).result
                                        check(result.outcome !is Outcome.Rejected)
                                    }
                                    check(source in result.state.getZone(ZoneKey(action.playerId,Zone.EXILE)))
                                    check(result.state.getZone(ZoneKey(action.playerId,Zone.HAND)).size==beforeHand-1)
                                } else {
                                    check(action is PassPriority && source in result.state.getZone(ZoneKey(action.playerId,Zone.HAND)))
                                    check(initial.getBattlefield().all { id ->
                                        initial.getEntity(id)?.has<TappedComponent>()==result.state.getEntity(id)?.has<TappedComponent>() })
                                }
                                check(result.state.pendingDecision==null)
                                check(result.state.getZone(ZoneKey(action.playerId,Zone.LIBRARY)).size==beforeLibrary)
                                if(branch=="baseline")put("zeroDrawBaselineFullyResolved",true)
                                if(branch=="replacement")put("replacementCardAndManaPreserved",true)
                                put("zeroCardsDrawn",true)
                            }
                            val directManaBeforeSacrifice=entry["expectDirectManaBeforeSacrifice"]?.jsonPrimitive?.boolean==true
                            if(directManaBeforeSacrifice) {
                                val template=json.decodeFromJsonElement<GameAction>(entry.getValue("followingCast").jsonObject.getValue("action"))
                                check(template is CastSpell)
                                check(result.outcome is Outcome.Done && result.state.pendingDecision==null)
                                check(template.cardId in result.state.stack)
                                val sacrifice=entry.getValue("followingCast").jsonObject.getValue("params").jsonObject.getValue("sacrificedPermanents").jsonArray
                                    .map { json.decodeFromJsonElement<EntityId>(it) }
                                check(sacrifice.all { it !in result.state.getBattlefield() && it in result.state.getZone(ZoneKey(template.playerId,Zone.GRAVEYARD)) })
                                check(result.state.getEntity(template.playerId)!!.get<LifeTotalComponent>()!!.life==
                                    initial.getEntity(template.playerId)!!.get<LifeTotalComponent>()!!.life-1)
                            }
                            val modalQuestions=mutableListOf<JsonElement>()
                            val equipment=entry["equipmentComparison"]?.jsonObject
                            if(equipment!=null) {
                                val source=json.decodeFromJsonElement<EntityId>(equipment.getValue("source"))
                                val host=json.decodeFromJsonElement<EntityId>(equipment.getValue("host"))
                                check(initial.stack.isEmpty())
                                check(initial.getEntity(source)!!.get<AttachedToComponent>()!!.targetId==host)
                                var passes=0
                                while(result.state.stack.isNotEmpty()) {
                                    check(++passes<=128 && result.state.pendingDecision==null)
                                    result=processor.process(result.state,PassPriority(requireNotNull(result.state.priorityPlayerId))).result
                                    check(result.outcome !is Outcome.Rejected)
                                }
                                val expectedHost=if(branch=="baseline" && equipment.containsKey("newHost"))
                                    json.decodeFromJsonElement<EntityId>(equipment.getValue("newHost")) else host
                                check(result.state.getEntity(source)!!.get<AttachedToComponent>()!!.targetId==expectedHost)
                                val changed=initial.getBattlefield().count { id ->
                                    initial.getEntity(id)?.has<TappedComponent>()!=result.state.getEntity(id)?.has<TappedComponent>()
                                }
                                val beforePool=initial.getEntity(action.playerId)?.get<ManaPoolComponent>()?.total ?: 0
                                val afterPool=result.state.getEntity(action.playerId)?.get<ManaPoolComponent>()?.total ?: 0
                                if(branch=="baseline") check(changed>0 || afterPool<beforePool)
                                else check(changed==0 && beforePool==afterPool)
                            }
                            val heldCard=entry["heldCard"]
                            val auraScope=entry["auraScopeComparison"]?.jsonObject
                            val positiveCounter=entry["positiveCounterComparison"]?.jsonObject
                            val neutralization=entry["neutralizationComparison"]?.jsonObject
                            val resolutionChoices=mutableListOf<JsonElement>()
                            if(auraScope!=null || positiveCounter!=null || neutralization!=null) {
                                val ownCounterTarget=json.decodeFromJsonElement<EntityId>(entry.getValue("positiveTriggerTarget"))
                                var steps=0
                                while(result.state.stack.isNotEmpty() || result.state.pendingDecision!=null) {
                                    check(++steps<=128)
                                    val question=result.state.pendingDecision
                                    val next=when(question) {
                                        null -> PassPriority(requireNotNull(result.state.priorityPlayerId))
                                        is ChooseTargetsDecision -> {
                                            check(question.context.sourceName=="Generous Visitor")
                                            val slot=question.targetRequirements.single()
                                            check(slot.minTargets==1 && slot.maxTargets==1 && ownCounterTarget in question.legalTargets.getValue(slot.index))
                                            val response=TargetsResponse(question.id,mapOf(slot.index to listOf(ownCounterTarget)))
                                            resolutionChoices.add(json.encodeToJsonElement(DecisionResponse.serializer(),response))
                                            SubmitDecision(question.playerId,response)
                                        }
                                        is YesNoDecision -> {
                                            val response=YesNoResponse(question.id,false)
                                            resolutionChoices.add(json.encodeToJsonElement(DecisionResponse.serializer(),response))
                                            SubmitDecision(question.playerId,response)
                                        }
                                        else -> error("Unexpected Aura/counter continuation: $question")
                                    }
                                    result=processor.process(result.state,next).result
                                    check(result.outcome !is Outcome.Rejected)
                                }
                                if(auraScope!=null) {
                                    val threat=json.decodeFromJsonElement<EntityId>(auraScope.getValue("threat"))
                                    val engine=json.decodeFromJsonElement<EntityId>(auraScope.getValue("engine"))
                                    val aura=json.decodeFromJsonElement<EntityId>(auraScope.getValue("aura"))
                                    val projected=StateProjector().project(result.state)
                                    check(result.state.getEntity(aura)!!.get<AttachedToComponent>()!!.targetId==(if(branch=="baseline")engine else threat))
                                    check(projected.cantAttack(threat)==(branch=="replacement"))
                                    check(projected.cantBlock(threat)==(branch=="replacement"))
                                    check(!projected.hasLostAllAbilities(engine))
                                }
                                if(positiveCounter!=null) {
                                    val enemy=json.decodeFromJsonElement<EntityId>(positiveCounter.getValue("enemy"))
                                    val own=json.decodeFromJsonElement<EntityId>(positiveCounter.getValue("own"))
                                    fun count(state:GameState,id:EntityId)=state.getEntity(id)!!.get<CountersComponent>()?.getCount(CounterType.PLUS_ONE_PLUS_ONE) ?: 0
                                    check(count(result.state,enemy)-count(initial,enemy)==if(branch=="baseline")1 else 0)
                                    check(count(result.state,own)-count(initial,own)==if(branch=="replacement")1 else 0)
                                }
                                if(neutralization!=null) {
                                    val aura=json.decodeFromJsonElement<EntityId>(neutralization.getValue("aura"))
                                    val target=json.decodeFromJsonElement<EntityId>(neutralization.getValue(if(branch=="baseline")"originalTarget" else "replacementTarget"))
                                    val other=json.decodeFromJsonElement<EntityId>(neutralization.getValue(if(branch=="baseline")"replacementTarget" else "originalTarget"))
                                    val projected=StateProjector().project(result.state)
                                    check(result.state.getEntity(aura)!!.get<AttachedToComponent>()!!.targetId==target)
                                    check(projected.hasLostAllAbilities(target))
                                    if(other!=target)check(!projected.hasLostAllAbilities(other))
                                }
                            }
                            val bounceUntap=entry["bounceUntapComparison"]?.jsonObject
                            if(bounceUntap!=null) {
                                val target=json.decodeFromJsonElement<EntityId>(bounceUntap.getValue("target"))
                                val untouched=json.decodeFromJsonElement<EntityId>(bounceUntap.getValue("baselineUntappedTarget"))
                                val payment=json.decodeFromJsonElement<EntityId>(bounceUntap.getValue("replacementPayment"))
                                check(action is ActivateAbility && initial.stack.isEmpty())
                                check(initial.getEntity(target)!!.has<TappedComponent>())
                                check(!initial.getEntity(untouched)!!.has<TappedComponent>())
                                var passes=0
                                while(result.state.stack.isNotEmpty()) {
                                    check(++passes<=128 && result.state.pendingDecision==null)
                                    result=processor.process(result.state,PassPriority(result.state.priorityPlayerId!!)).result
                                    check(result.outcome !is Outcome.Rejected)
                                }
                                if(branch=="baseline") {
                                    check(target in result.state.getZone(ZoneKey(action.playerId,Zone.HAND)))
                                    check(untouched in result.state.getBattlefield() && !result.state.getEntity(untouched)!!.has<TappedComponent>())
                                } else {
                                    check(target in result.state.getBattlefield() && !result.state.getEntity(target)!!.has<TappedComponent>())
                                    check(payment in result.state.getZone(ZoneKey(action.playerId,Zone.HAND)))
                                }
                            }
                            val removedTarget=entry["removedUntapTarget"]
                            if(removedTarget!=null) {
                                val target=json.decodeFromJsonElement<EntityId>(removedTarget)
                                check(target in initial.getBattlefield())
                                if(branch=="baseline") {
                                    check(action is ActivateAbility && result.outcome !is Outcome.Rejected)
                                    entry["recordedCommanderResponse"]?.jsonObject?.let { response ->
                                        val question=result.state.pendingDecision as YesNoDecision
                                        check(response.getValue("type").jsonPrimitive.content=="YesNoResponse")
                                        check(json.encodeToJsonElement(question.id)==response.getValue("decisionId"))
                                        check(question.context.sourceId==target && question.context.phase==DecisionPhase.STATE_BASED)
                                        result=processor.process(result.state,SubmitDecision(question.playerId,
                                            YesNoResponse(question.id,response.getValue("choice").jsonPrimitive.boolean))).result
                                        check(result.outcome !is Outcome.Rejected)
                                        put("recordedCommanderHandChoiceApplied",true)
                                    }
                                    check(target !in result.state.getBattlefield())
                                    check(target in result.state.getZone(ZoneKey(action.playerId,Zone.HAND)))
                                } else {
                                    check(action is PassPriority && result.outcome !is Outcome.Rejected)
                                    check(target in result.state.getBattlefield())
                                }
                                if(branch=="baseline")put("returnedTargetCannotReceiveUntap",true)
                                else put("replacementPreservesTarget",true)
                            }
                            val heldBounce=entry["heldBouncePayment"]
                            if(heldBounce!=null) {
                                val material=json.decodeFromJsonElement<EntityId>(heldBounce)
                                check(material in initial.getBattlefield())
                                if(branch=="baseline") {
                                    var steps=0
                                    while(result.state.stack.isNotEmpty() || result.state.pendingDecision!=null) {
                                        check(++steps<=128)
                                        val question=result.state.pendingDecision
                                        val next=when(question) {
                                            null -> PassPriority(requireNotNull(result.state.priorityPlayerId))
                                            is YesNoDecision -> SubmitDecision(question.playerId,YesNoResponse(question.id,false))
                                            else -> error("Unexpected bounce continuation: $question")
                                        }
                                        result=processor.process(result.state,next).result
                                        check(result.outcome !is Outcome.Rejected)
                                    }
                                    check(material !in result.state.getBattlefield())
                                } else {
                                    check(action is PassPriority && result.outcome is Outcome.Done)
                                    check(material in result.state.getBattlefield())
                                }
                            }
                            val playerDraw=entry["beneficialPlayerDrawComparison"]?.jsonObject
                            if(playerDraw!=null) {
                                check(action is CastSpell && result.outcome is Outcome.Done && result.state.pendingDecision==null)
                                val original=json.decodeFromJsonElement<EntityId>(playerDraw.getValue("originalTarget"))
                                val target=(action.targets.single() as ChosenTarget.Player).playerId
                                check(target==(if(branch=="baseline")original else action.playerId))
                                check(action.cardId in result.state.stack)
                            }
                            val strandedBoost=entry["strandedBoostComparison"]?.jsonObject
                            if(strandedBoost!=null) {
                                val land=json.decodeFromJsonElement<EntityId>(strandedBoost.getValue("land"))
                                val spell=json.decodeFromJsonElement<EntityId>(strandedBoost.getValue("spell"))
                                check(!initial.getEntity(land)!!.has<TappedComponent>())
                                var steps=0
                                while(result.state.stack.isNotEmpty()) {
                                    check(++steps<=128 && result.state.pendingDecision==null)
                                    result=processor.process(result.state,PassPriority(requireNotNull(result.state.priorityPlayerId))).result
                                    check(result.outcome !is Outcome.Rejected)
                                }
                                check((result.state.getEntity(action.playerId)?.get<ManaPoolComponent>()?.total ?: 0)==0)
                                check(result.state.getEntity(land)!!.has<TappedComponent>()==(branch=="baseline"))
                                check((spell in result.state.getZone(ZoneKey(action.playerId,Zone.HAND)))==(branch=="replacement"))
                            }
                            val modelCounter=entry["modelCounterComparison"]?.jsonObject
                            var modelCounterElfIncrease:Int?=null
                            var modelCounterTreasureIncrease:Int?=null
                            if(modelCounter!=null) {
                                val opponent=json.decodeFromJsonElement<EntityId>(modelCounter.getValue("opponent"))
                                check(initial.stack.isNotEmpty() && opponent!=action.playerId)
                                fun countSubtype(state:GameState,subtype:String)=state.getBattlefield().count {
                                    val entity=state.getEntity(it)!!
                                    entity.get<ControllerComponent>()?.playerId==opponent &&
                                        entity.get<CardComponent>()?.typeLine?.subtypes?.any { it.value==subtype }==true
                                }
                                var steps=0
                                while(result.state.stack.isNotEmpty() || result.state.pendingDecision!=null) {
                                    check(++steps<=256)
                                    val next=when(val q=result.state.pendingDecision) {
                                        null -> PassPriority(requireNotNull(result.state.priorityPlayerId))
                                        is YesNoDecision -> SubmitDecision(q.playerId,YesNoResponse(q.id,false))
                                        else -> error("Unexpected counter-comparison continuation: $q")
                                    }
                                    result=processor.process(result.state,next).result
                                    check(result.outcome !is Outcome.Rejected)
                                }
                                modelCounterElfIncrease=countSubtype(result.state,"Elf")-countSubtype(initial,"Elf")
                                modelCounterTreasureIncrease=countSubtype(result.state,"Treasure")-countSubtype(initial,"Treasure")
                                if(branch=="baseline") {
                                    check(action is CastSpell && modelCounterElfIncrease==0 && modelCounterTreasureIncrease==2)
                                } else {
                                    check(action is PassPriority && modelCounterElfIncrease>=4 && modelCounterTreasureIncrease==0)
                                }
                            }
                            val entryReduction=entry["entryReductionProof"]?.jsonObject
                            if(entryReduction!=null) {
                                val spell=json.decodeFromJsonElement<EntityId>(entryReduction.getValue("spell"))
                                val target=json.decodeFromJsonElement<EntityId>(entryReduction.getValue("target"))
                                check(initial.stack.isEmpty() && target in initial.getBattlefield())
                                if(branch=="replacement") {
                                    check(action is CastSpell)
                                    var steps=0
                                    while(result.state.stack.isNotEmpty() || result.state.pendingDecision!=null) {
                                        check(++steps<=128)
                                        val next=when(val q=result.state.pendingDecision) {
                                            null -> PassPriority(requireNotNull(result.state.priorityPlayerId))
                                            is ChooseTargetsDecision -> {
                                                check(q.context.sourceName=="Nowhere to Run")
                                                val slot=q.targetRequirements.single()
                                                check(slot.minTargets==1 && slot.maxTargets==1 && target in q.legalTargets.getValue(slot.index))
                                                val response=TargetsResponse(q.id,mapOf(slot.index to listOf(target)))
                                                resolutionChoices.add(json.encodeToJsonElement(DecisionResponse.serializer(),response))
                                                SubmitDecision(q.playerId,response)
                                            }
                                            is YesNoDecision -> SubmitDecision(q.playerId,YesNoResponse(q.id,false))
                                            else -> error("Unexpected entry removal continuation: $q")
                                        }
                                        result=processor.process(result.state,next).result
                                        check(result.outcome !is Outcome.Rejected)
                                    }
                                    check(spell in result.state.getBattlefield() && target !in result.state.getBattlefield())
                                } else {
                                    check(action is PassPriority && target in result.state.getBattlefield())
                                    check(spell in result.state.getZone(ZoneKey(action.playerId,Zone.HAND)))
                                }
                            }
                            val duplicateReturn=entry["heldDuplicateSelfReturn"]
                            if(duplicateReturn!=null) {
                                val source=json.decodeFromJsonElement<EntityId>(duplicateReturn)
                                check(source in initial.getBattlefield() && initial.stack.isNotEmpty())
                                var steps=0
                                while(result.state.stack.isNotEmpty() || result.state.pendingDecision!=null) {
                                    check(++steps<=128)
                                    val next=when(val q=result.state.pendingDecision) {
                                        null -> PassPriority(requireNotNull(result.state.priorityPlayerId))
                                        is YesNoDecision -> SubmitDecision(q.playerId,YesNoResponse(q.id,false))
                                        else -> error("Unexpected self-return continuation: $q")
                                    }
                                    result=processor.process(result.state,next).result
                                    check(result.outcome !is Outcome.Rejected)
                                }
                                check(source in result.state.getZone(ZoneKey(action.playerId,Zone.HAND)))
                                fun tapped(state:GameState)=state.getBattlefield().count {
                                    state.getEntity(it)!!.get<ControllerComponent>()?.playerId==action.playerId && state.getEntity(it)!!.has<TappedComponent>()
                                }
                                check(tapped(result.state)-tapped(initial)==if(branch=="baseline")1 else 0)
                            }
                            val heldUtility=entry["heldUtilitySource"]
                            if(heldUtility!=null) {
                                val source=json.decodeFromJsonElement<EntityId>(heldUtility)
                                check(!initial.getEntity(source)!!.has<TappedComponent>())
                                check(result.outcome is Outcome.Done && result.state.pendingDecision==null)
                                if(branch=="baseline") {
                                    check(action is ActivateAbility)
                                    check(result.state.getEntity(source)!!.has<TappedComponent>())
                                    check(result.state.stack.size==initial.stack.size+1)
                                } else {
                                    check(action is PassPriority)
                                    check(!result.state.getEntity(source)!!.has<TappedComponent>())
                                }
                            }
                            val heldSource=entry["heldSource"]
                            if(branch=="replacement" && heldSource!=null) {
                                check(action is PassPriority && result.outcome is Outcome.Done)
                                val source=json.decodeFromJsonElement<EntityId>(heldSource)
                                check(!initial.getEntity(source)!!.has<TappedComponent>())
                                check(!result.state.getEntity(source)!!.has<TappedComponent>())
                            }
                            if(branch=="replacement" && heldCard!=null) {
                                check(action is PassPriority && result.outcome is Outcome.Done)
                                val kept=json.decodeFromJsonElement<EntityId>(heldCard)
                                check(kept in result.state.getZone(ZoneKey(action.playerId,Zone.HAND)) && kept !in result.state.stack)
                            }
                            val tapCost=entry["tapCost"]?.jsonObject
                            if(branch=="replacement" && tapCost!=null) {
                                check(result.outcome !is Outcome.Rejected)
                                for(key in listOf("source","creature")) {
                                    val permanent=json.decodeFromJsonElement<EntityId>(tapCost.getValue(key))
                                    check(result.state.getEntity(permanent)!!.has<TappedComponent>())
                                }
                            }
                            val heldRemoval=entry["heldRemoval"]?.jsonObject
                            if(branch=="replacement" && heldRemoval!=null) {
                                check(action is PassPriority && result.outcome is Outcome.Done && result.state.pendingDecision==null)
                                val kept=json.decodeFromJsonElement<EntityId>(heldRemoval.getValue("keptCard"))
                                val pending=json.decodeFromJsonElement<EntityId>(heldRemoval.getValue("pendingSpell"))
                                val target=json.decodeFromJsonElement<EntityId>(heldRemoval.getValue("target"))
                                check(kept in result.state.getZone(ZoneKey(action.playerId,Zone.HAND)) && kept !in result.state.stack)
                                check(pending in result.state.stack && target in result.state.getBattlefield())
                            }
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
                                if(auraScope!=null) {
                                    put("largeCombatThreatStopped",branch=="replacement")
                                    put("triggerEngineAbilitiesRemain",true)
                                }
                                if(positiveCounter!=null)put("positiveCounterGivenToOwnCreature",branch=="replacement")
                                if(auraScope!=null || positiveCounter!=null) {
                                    put("resolvedWithOpponentsPassing",true)
                                    put("continuationChoices",JsonArray(resolutionChoices))
                                }
                                if(playerDraw!=null)put("beneficialDrawTargetsCaster",branch=="replacement")
                                if(strandedBoost!=null) {
                                    put("noManaGenerated",true);put("cardAndLandPreserved",branch=="replacement");put("resolvedWithOpponentsPassing",true)
                                }
                                if(modelCounter!=null) {
                                    put("opposingElfIncrease",requireNotNull(modelCounterElfIncrease));put("opposingTreasureIncrease",requireNotNull(modelCounterTreasureIncrease));put("resolvedWithOpponentsPassing",true)
                                }
                                if(entryReduction!=null) {
                                    put("opposingEngineRemoved",branch=="replacement");put("resolvedWithOpponentsPassing",branch=="replacement");put("continuationChoices",JsonArray(resolutionChoices))
                                }
                                if(duplicateReturn!=null) {
                                    put("samePermanentReturnedToHand",true);put("additionalManaSourceSpent",branch=="baseline");put("resolvedWithOpponentsPassing",true)
                                }
                                if(heldUtility!=null)put("utilitySourceUntapped",branch=="replacement")
                                if(heldBounce!=null) {
                                    put("bouncedMaterialPreserved",branch=="replacement")
                                    put("resolvedWithOpponentsPassing",branch=="baseline")
                                }
                                if(bounceUntap!=null) {
                                    put("resolvedWithOpponentsPassing",true)
                                    put("usefulUntap",branch=="replacement")
                                    put("tappedResourcePreserved",branch=="replacement")
                                }
                                if(equipment!=null) {
                                    put("resolvedWithOpponentsPassing",true)
                                    put("attachmentUnchanged",branch=="replacement" || !equipment.containsKey("newHost"))
                                    put("attachmentHost",if(branch=="baseline" && equipment.containsKey("newHost")) equipment.getValue("newHost") else equipment.getValue("host"))
                                    put("manaAndSourceTapsPreserved",branch=="replacement")
                                }
                                if(neutralization!=null) {
                                    put("resolvedWithOpponentsPassing",true)
                                    put("neutralizedTarget",neutralization.getValue(if(branch=="baseline")"originalTarget" else "replacementTarget"))
                                    put("counterTriggerTarget",entry.getValue("positiveTriggerTarget"))
                                    put("nativeResolutionChoices",JsonArray(resolutionChoices))
                                }
                                if(branch=="replacement" && heldCard!=null) put("cardRetainedInHand",true)
                                if(branch=="replacement" && heldSource!=null) put("drawSourceUntapped",true)
                                if(branch=="replacement" && tapCost!=null) put("offeredSourceAndCreatureTapped",true)
                                if(branch=="replacement" && heldRemoval!=null) {
                                    put("cardRetainedInHand",true);put("pendingRemovalPreserved",true)
                                }
                                if(directManaBeforeSacrifice) {
                                    put("castOnStack",true);put("sacrificeInGraveyard",true);put("manaAbilityDamageExactlyOnce",true)
                                }
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
