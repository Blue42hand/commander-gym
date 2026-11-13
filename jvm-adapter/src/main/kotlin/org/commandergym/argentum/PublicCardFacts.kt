package org.commandergym.argentum

import com.wingedsheep.ai.engine.knowledge.CardIntentAnalyzer
import com.wingedsheep.ai.engine.knowledge.SynergyAxes
import com.wingedsheep.ai.engine.knowledge.TargetPolarityAnalyzer
import com.wingedsheep.engine.registry.CardRegistry
import com.wingedsheep.sdk.dsl.Triggers
import com.wingedsheep.sdk.scripting.TriggeredAbility
import com.wingedsheep.sdk.scripting.SuppressHexproofForGroup
import com.wingedsheep.sdk.scripting.SuppressWardForGroup
import com.wingedsheep.sdk.scripting.targets.TargetPlayer
import com.wingedsheep.sdk.scripting.values.Aggregation
import com.wingedsheep.sdk.scripting.targets.TargetObject
import com.wingedsheep.sdk.scripting.filters.unified.TargetFilter
import com.wingedsheep.sdk.dsl.Patterns
import com.wingedsheep.sdk.dsl.Effects
import com.wingedsheep.sdk.dsl.DynamicAmounts
import com.wingedsheep.sdk.core.CounterType
import com.wingedsheep.sdk.core.Zone
import com.wingedsheep.sdk.scripting.MustAttack
import com.wingedsheep.sdk.scripting.CantAttack
import com.wingedsheep.sdk.scripting.CantBlock
import com.wingedsheep.sdk.scripting.PreventActivatedAbilities
import com.wingedsheep.sdk.scripting.ActivatedAbilityScope
import com.wingedsheep.sdk.scripting.LoseAllAbilities
import com.wingedsheep.sdk.scripting.ModifyStats
import com.wingedsheep.sdk.scripting.GrantKeyword
import com.wingedsheep.sdk.scripting.TimingRule
import com.wingedsheep.sdk.core.Keyword
import com.wingedsheep.sdk.scripting.EntersWithChoice
import com.wingedsheep.sdk.scripting.ChoiceType
import com.wingedsheep.sdk.scripting.Duration
import com.wingedsheep.sdk.scripting.costs.manaCostOrNull
import com.wingedsheep.sdk.scripting.costs.atomOrNull
import com.wingedsheep.sdk.scripting.costs.CostAtom
import com.wingedsheep.sdk.scripting.ActivationRestriction
import com.wingedsheep.sdk.scripting.effects.AddCountersEffect
import com.wingedsheep.sdk.scripting.effects.ModifyStatsEffect
import com.wingedsheep.sdk.scripting.effects.TapUntapEffect
import com.wingedsheep.sdk.scripting.filters.unified.GroupFilter
import com.wingedsheep.sdk.scripting.EntersTapped
import com.wingedsheep.sdk.scripting.GameObjectFilter
import com.wingedsheep.sdk.scripting.conditions.AnyCondition
import com.wingedsheep.sdk.scripting.conditions.Exists
import com.wingedsheep.sdk.scripting.references.Player
import com.wingedsheep.sdk.core.Subtype
import com.wingedsheep.sdk.model.CardDefinition
import com.wingedsheep.sdk.scripting.AbilityCost
import com.wingedsheep.sdk.scripting.values.DynamicAmount
import com.wingedsheep.sdk.scripting.effects.CompositeEffect
import com.wingedsheep.sdk.scripting.effects.DealDamageEffect
import com.wingedsheep.sdk.scripting.effects.AddManaOfChoiceEffect
import com.wingedsheep.sdk.scripting.values.ManaColorSet
import com.wingedsheep.sdk.scripting.effects.AddManaEffect
import com.wingedsheep.sdk.scripting.effects.AddColorlessManaEffect
import com.wingedsheep.sdk.scripting.effects.GatedEffect
import com.wingedsheep.sdk.scripting.effects.Gate
import com.wingedsheep.sdk.scripting.effects.DrawCardsEffect
import com.wingedsheep.sdk.scripting.effects.ManaExpiry
import com.wingedsheep.sdk.scripting.targets.EffectTarget
import com.wingedsheep.sdk.scripting.EventPattern
import com.wingedsheep.sdk.scripting.AdditionalManaOnSourceTap
import com.wingedsheep.sdk.scripting.TappedForManaType
import com.wingedsheep.sdk.scripting.effects.GrantStaticAbilityEffect
import com.wingedsheep.sdk.scripting.effects.GainLifeEffect
import kotlinx.serialization.json.*

/** Public definitions only: this API cannot accept GameState or a seat snapshot. */
object PublicCardFacts {
    private val json = Json { encodeDefaults = true }
    private val basicColors = mapOf("Plains" to "W", "Island" to "U", "Swamp" to "B", "Mountain" to "R", "Forest" to "G")

    private fun manaAndTapOnly(cost: AbilityCost): Boolean = when(cost) {
        is AbilityCost.Composite -> cost.costs.isNotEmpty() && cost.costs.all(::manaAndTapOnly)
        else -> cost == AbilityCost.Tap || cost.manaCostOrNull != null
    }

    private fun drawSensitive(value: JsonElement): Boolean = when (value) {
        is JsonObject -> (value["type"] as? JsonPrimitive)?.content?.contains("Draw") == true || value.values.any(::drawSensitive)
        is JsonArray -> value.any(::drawSensitive)
        else -> false
    }

    fun export(registry: CardRegistry, names: Collection<String>, engineRevision: String): JsonObject {
        require(engineRevision.matches(Regex("[0-9a-f]{40}")))
        return buildJsonObject {
            put("schemaVersion", 1)
            put("engineRevision", engineRevision)
            putJsonArray("creatureTypes") { Subtype.ALL_CREATURE_TYPES.sorted().forEach { add(it) } }
            putJsonObject("cards") {
                names.distinct().sorted().forEach { name ->
                    val card = requireNotNull(registry.getCard(name)) { "Unknown native card: $name" }
                    put(name, fact(card, registry))
                }
            }
        }
    }

    private fun fact(card: CardDefinition, registry: CardRegistry): JsonObject {
        val intent = CardIntentAnalyzer.analyze(card)
        val synergy = SynergyAxes.of(card)
        val raw = json.encodeToJsonElement(CardDefinition.serializer(), card).jsonObject
        val script = raw["script"]!!.jsonObject
        val basics = card.typeLine.subtypes.mapNotNull { basicColors[it.value] }
        val activated = card.script.activatedAbilities
        // A small structural whitelist, deliberately excluding variable, restricted,
        // conditional, sacrifice, life-cost and choice-producing mana abilities.
        val simpleAbilities = activated.isNotEmpty() && activated.all {
            it.isManaAbility && it.cost == AbilityCost.Tap && when (val effect = it.effect) {
                is AddManaEffect -> effect.amount is DynamicAmount.Fixed && effect.restriction == null && effect.riders.isEmpty() && effect.expiry == ManaExpiry.END_OF_TURN
                is AddColorlessManaEffect -> effect.amount is DynamicAmount.Fixed && effect.restriction == null && effect.riders.isEmpty() && effect.expiry == ManaExpiry.END_OF_TURN
                else -> false
            }
        }
        val noStatic = card.staticAbilities.isEmpty()
        val noTriggers = card.script.triggeredAbilities.isEmpty()
        val noReplacement = card.script.replacementEffects.isEmpty()
        val friendlyGroups = setOf(GroupFilter.AllCreaturesYouControl, GroupFilter.AllPermanentsYouControl,
            GroupFilter(GameObjectFilter.Creature.withKeyword(Keyword.FLYING).youControl()),
            GroupFilter.ChosenSubtypeCreatures().youControl())
        val helpfulKeywords = setOf("FLYING", "LIFELINK", "VIGILANCE", "REACH", "MENACE", "TRAMPLE", "HASTE",
            "FIRST_STRIKE", "DOUBLE_STRIKE", "DEATHTOUCH", "HEXPROOF", "INDESTRUCTIBLE")
        val friendlyStaticPermanent = card.typeLine.isPermanent && card.script.spellEffect == null && noTriggers && noReplacement && activated.isEmpty() && card.staticAbilities.isNotEmpty() &&
            card.staticAbilities.all { ability ->
                when (ability) {
                    is GrantKeyword -> ability.keyword in helpfulKeywords && ability.filter.copy(excludeSelf = false) in friendlyGroups
                    is ModifyStats -> ability.powerBonus >= 0 && ability.toughnessBonus >= 0 && ability.filter.copy(excludeSelf = false) in friendlyGroups
                    else -> false
                }
            }
        val simpleMana = noStatic && noTriggers && noReplacement &&
            (simpleAbilities || card.typeLine.isLand && basics.isNotEmpty() && activated.isEmpty())
        val manaColors = basics.toMutableSet()
        if (activated.isNotEmpty()) {
            // Read the SDK's serialized fixed mana effect. Unknown shapes supply
            // no color estimate rather than guessing from Oracle text.
            for ((index, ability) in activated.withIndex()) {
                // A utility ability must not hide an independent, ordinary tap
                // mana mode. Color knowledge does not authorize its activation.
                if (!ability.isManaAbility || ability.cost != AbilityCost.Tap) continue
                val nativeEffect = ability.effect
                if (nativeEffect is AddManaOfChoiceEffect && nativeEffect.colorSet == ManaColorSet.AnyColor &&
                    nativeEffect.amount == DynamicAmount.Fixed(1) && nativeEffect.restriction == null &&
                    nativeEffect.riders.isEmpty() && nativeEffect.recipient == EffectTarget.Controller) {
                    manaColors.addAll(listOf("W", "U", "B", "R", "G"))
                }
                if (nativeEffect !is AddManaEffect && nativeEffect !is AddColorlessManaEffect) continue
                if (nativeEffect is AddManaEffect && (nativeEffect.restriction != null || nativeEffect.riders.isNotEmpty())) continue
                if (nativeEffect is AddColorlessManaEffect && (nativeEffect.restriction != null || nativeEffect.riders.isNotEmpty())) continue
                val effect = script["activatedAbilities"]!!.jsonArray[index].jsonObject["effect"]!!.jsonObject
                when (effect["type"]?.jsonPrimitive?.content) {
                    "AddColorlessMana" -> manaColors.add("C")
                    "AddMana" -> effect["color"]?.jsonPrimitive?.content?.let {
                        mapOf("WHITE" to "W", "BLUE" to "U", "BLACK" to "B", "RED" to "R", "GREEN" to "G") [it]
                    }?.let(manaColors::add)
                }
            }
        }
        // Recognize only the exact fixed mana + one self-damage painland shape.
        // This expands color estimates, never the safe activation whitelist.
        val painColors = mutableSetOf<String>()
        for (ability in activated) {
            if (!ability.isManaAbility || ability.cost != AbilityCost.Tap) continue
            val parts = (ability.effect as? CompositeEffect)?.effects ?: continue
            if (parts.size != 2) continue
            val mana = parts[0] as? AddManaEffect ?: continue
            val damage = parts[1] as? DealDamageEffect ?: continue
            if (mana.amount != DynamicAmount.Fixed(1) || mana.restriction != null || mana.riders.isNotEmpty() ||
                damage.amount != DynamicAmount.Fixed(1) || damage.target != EffectTarget.PlayerRef(Player.You)) continue
            mapOf("WHITE" to "W", "BLUE" to "U", "BLACK" to "B", "RED" to "R", "GREEN" to "G")[mana.color.name]?.let(painColors::add)
        }
        manaColors.addAll(painColors)
        val commanderMana = activated.any {
            val effect = it.effect as? AddManaOfChoiceEffect
            it.isManaAbility && it.cost == AbilityCost.Tap && effect != null &&
                effect.colorSet == ManaColorSet.CommanderIdentity && effect.amount == DynamicAmount.Fixed(1) && effect.restriction == null && effect.riders.isEmpty() && effect.recipient == EffectTarget.Controller
        }
        // A narrow, reusable development contract, independent of safe manual
        // activation. Free tribal choice and a positive own-tribe anthem are
        // understood; imprint, sacrifice, damage and conditional mana are not.
        val developmentAmounts = activated.map { ability ->
            if (!ability.isManaAbility || ability.cost != AbilityCost.Tap) 0
            else when (val effect = ability.effect) {
                is AddManaEffect -> if (effect.restriction == null && effect.riders.isEmpty()) (effect.amount as? DynamicAmount.Fixed)?.amount ?: 0 else 0
                is AddColorlessManaEffect -> if (effect.restriction == null && effect.riders.isEmpty()) (effect.amount as? DynamicAmount.Fixed)?.amount ?: 0 else 0
                is AddManaOfChoiceEffect -> if (effect.colorSet in listOf(ManaColorSet.AnyColor, ManaColorSet.CommanderIdentity) &&
                    effect.amount == DynamicAmount.Fixed(1) && effect.restriction == null && effect.riders.isEmpty() &&
                    effect.recipient == EffectTarget.Controller) 1 else 0
                else -> 0
            }
        }
        val safeEntry = card.script.replacementEffects.all {
            it is EntersWithChoice && it.choiceType == ChoiceType.CREATURE_TYPE && it.chooser == Player.You
        }
        val safeStatics = card.staticAbilities.all {
            it is ModifyStats && it.powerBonus >= 0 && it.toughnessBonus >= 0 &&
                it.filter == GroupFilter.ChosenSubtypeCreatures().youControl()
        }
        val developmentMana = if (noTriggers && safeEntry && safeStatics && developmentAmounts.isNotEmpty() &&
            developmentAmounts.all { it > 0 }) developmentAmounts.max() else 0
        // Export only an exact OR of own battlefield land-subtype tests.
        // Other ETB replacements and additional conditions remain unknown.
        val tapped = card.script.replacementEffects.singleOrNull() as? EntersTapped
        val condition = tapped?.unlessCondition
        val branches = if (condition is AnyCondition) condition.conditions else listOfNotNull(condition)
        val untappedSubtypes = if (tapped?.payLifeCost == null && branches.isNotEmpty()) branches.mapNotNull { branch ->
            val exists = branch as? Exists ?: return@mapNotNull null
            if (exists.player != Player.You || exists.zone != Zone.BATTLEFIELD || exists.negate || exists.excludeSelf) return@mapNotNull null
            basicColors.keys.firstOrNull { exists.filter == GameObjectFilter.Land.withSubtype(it) }
        }.takeIf { it.size == branches.size } ?: emptyList() else emptyList()
        return buildJsonObject {
            put("developmentMana", developmentMana)
            putJsonArray("powerManaAbilities") {
                activated.filter { ability ->
                    if(!ability.isManaAbility || ability.activateFromZone!=Zone.BATTLEFIELD) false
                    else when(val effect=ability.effect) {
                        is AddManaEffect -> effect.amount==DynamicAmounts.sourcePower()
                        is AddColorlessManaEffect -> effect.amount==DynamicAmounts.sourcePower()
                        is AddManaOfChoiceEffect -> effect.amount==DynamicAmounts.sourcePower() && effect.recipient==EffectTarget.Controller
                        else -> false
                    }
                }.forEach { add(it.id.value) }
            }
            putJsonArray("positiveStatBoostAbilities") {
                activated.filter { ability ->
                    val effect=ability.effect as? ModifyStatsEffect ?: return@filter false
                    val target=ability.targetRequirements.singleOrNull() as? TargetObject ?: return@filter false
                    val amount=effect.powerModifier
                    val nonnegative=amount is DynamicAmount.Fixed && amount.amount>0 || amount is DynamicAmount.Count ||
                        amount is DynamicAmount.AggregateBattlefield && amount.aggregation==Aggregation.COUNT
                    !ability.isManaAbility && ability.timing==TimingRule.InstantSpeed && ability.activateFromZone==Zone.BATTLEFIELD && ability.restrictions.isEmpty() &&
                        manaAndTapOnly(ability.cost) && effect.target is EffectTarget.BoundVariable &&
                        effect.duration==Duration.EndOfTurn && amount==effect.toughnessModifier && nonnegative &&
                        target==TargetObject(filter=TargetFilter.Creature,id=target.id)
                }.forEach { add(it.id.value) }
            }
            val drawParts=(card.script.spellEffect as? CompositeEffect)?.effects
            val targetedDraw=(card.script.spellEffect as? DrawCardsEffect) ?: drawParts?.firstOrNull() as? DrawCardsEffect
            val drawCount=(targetedDraw?.count as? DynamicAmount.Fixed)?.amount
            val playerTarget=card.targetRequirements.singleOrNull() as? TargetPlayer
            if(targetedDraw!=null && targetedDraw.target is EffectTarget.BoundVariable && drawCount!=null && drawCount in 1..100 &&
                playerTarget==TargetPlayer(id=playerTarget?.id)) {
                val ordinaryPositive=card.script.spellEffect==targetedDraw || (1 until drawCount).any { discard ->
                    card.script.spellEffect==(targetedDraw then Effects.Discard(discard,targetedDraw.target)) ||
                        card.script.spellEffect==(targetedDraw then Effects.DiscardUnlessMatching(discard,GameObjectFilter.Land,target=targetedDraw.target))
                }
                if(ordinaryPositive)put("beneficialPlayerDrawCount",drawCount)
            }
            putJsonArray("manaOnlySelfUntapAbilities") {
                activated.filter {
                    val effect = it.effect as? TapUntapEffect
                    it.cost.manaCostOrNull != null && effect?.tap == false && effect.target == EffectTarget.Self
                }.forEach { add(it.id.value) }
            }
            putJsonArray("simpleTargetUntapAbilities") {
                activated.filter {
                    val effect=it.effect as? TapUntapEffect ?: return@filter false
                    it.isManaAbility==false && it.timing==TimingRule.InstantSpeed && it.restrictions.isEmpty() && manaAndTapOnly(it.cost) &&
                        effect.tap==false && effect.target is EffectTarget.BoundVariable && it.targetRequirements.size==1
                }.forEach { add(it.id.value) }
            }
            putJsonArray("bounceTargetUntapAbilities") {
                activated.filter {
                    val effect = it.effect as? TapUntapEffect
                    val cost = it.cost.atomOrNull as? CostAtom.ReturnToHand
                    cost != null && cost.count == 1 && cost.youControl &&
                        !it.isManaAbility && it.timing == TimingRule.InstantSpeed && it.activateFromZone == Zone.BATTLEFIELD &&
                        it.restrictions.all { restriction -> restriction == ActivationRestriction.OncePerTurn } &&
                        effect?.tap == false && effect.target is EffectTarget.BoundVariable && it.targetRequirements.size == 1
                }.forEach { add(it.id.value) }
            }
            // Match exact SDK descriptions at the native target-selection seam;
            // no Oracle text parsing or guesses from a generic REMOVAL tag.
            val entry = card.script.triggeredAbilities.singleOrNull()
            val entryEffect = entry?.effect as? ModifyStatsEffect
            val entryTarget = entry?.targetRequirement as? TargetObject
            val entryPower = entryEffect?.powerModifier as? DynamicAmount.Fixed
            val entryToughness = entryEffect?.toughnessModifier as? DynamicAmount.Fixed
            val entrySpec = Triggers.self.enters()
            val hexSuppression = SuppressHexproofForGroup(GroupFilter.AllCreaturesOpponentsControl)
            val wardSuppression = SuppressWardForGroup(GroupFilter.AllCreaturesOpponentsControl)
            if(entry!=null && entryEffect!=null && entryTarget!=null && entryPower!=null && entryToughness!=null &&
                entryPower.amount<=0 && entryToughness.amount<0 && entryEffect.duration==Duration.EndOfTurn &&
                entryEffect.target is EffectTarget.BoundVariable &&
                entryTarget==TargetObject(filter=TargetFilter.CreatureOpponentControls,id=entryTarget.id) &&
                entry.copy(descriptionOverride=null)==TriggeredAbility(entry.id,entrySpec.event,entrySpec.binding,entryEffect,targetRequirement=entryTarget) &&
                noReplacement && activated.isEmpty() && card.script.spellEffect==null &&
                card.script.staticAbilities.all { it==hexSuppression || it==wardSuppression }) {
                putJsonObject("entryStatReduction") {
                    put("abilityId",entry.id.value);put("power",entryPower.amount);put("toughness",entryToughness.amount)
                    put("suppressesHexproof",hexSuppression in card.script.staticAbilities)
                    put("suppressesWard",wardSuppression in card.script.staticAbilities)
                }
            }
            putJsonObject("fixedStatReductions") {
                card.script.triggeredAbilities.forEach {
                    val effect = it.effect as? ModifyStatsEffect ?: return@forEach
                    val power = effect.powerModifier as? DynamicAmount.Fixed ?: return@forEach
                    val toughness = effect.toughnessModifier as? DynamicAmount.Fixed ?: return@forEach
                    if (power.amount <= 0 && toughness.amount < 0 && effect.duration == Duration.EndOfTurn &&
                        it.allTargetRequirements.size == 1 && effect.target is EffectTarget.BoundVariable) {
                        putJsonObject(effect.description) {
                            put("power", power.amount); put("toughness", toughness.amount)
                        }
                    }
                }
            }
            putJsonArray("untappedIfLandSubtypes") { untappedSubtypes.sorted().forEach { add(it) } }
            put("variableMana", activated.any { ability ->
                ability.isManaAbility && when (val effect = ability.effect) {
                    is AddManaEffect -> effect.amount !is DynamicAmount.Fixed
                    is AddColorlessManaEffect -> effect.amount !is DynamicAmount.Fixed
                    is AddManaOfChoiceEffect -> effect.amount !is DynamicAmount.Fixed
                    else -> false
                }
            })
            put("untappedPayLife", if (tapped?.unlessCondition == null) tapped?.payLifeCost ?: 0 else 0)
            put("conditionalTappedEntry", card.typeLine.isLand && noTriggers && tapped?.unlessCondition != null && tapped.payLifeCost == null)
            // A whole spell granting one fixed land-subtype mana bonus for this turn.
            // Mixed effects, extra costs, riders and conditional production remain unknown.
            val boost = card.script.spellEffect as? GrantStaticAbilityEffect
            val bonus = boost?.ability as? AdditionalManaOnSourceTap
            val boostSubtype = if (boost?.target == EffectTarget.Controller && boost.duration == Duration.EndOfTurn &&
                bonus?.amount == DynamicAmount.Fixed(1) && bonus.color != null && bonus.rider == null &&
                bonus.whenProducing == TappedForManaType.ANY && noStatic && noTriggers && noReplacement && activated.isEmpty() &&
                card.script.additionalCosts.isEmpty()) {
                Subtype.ALL_BASIC_LAND_TYPES.singleOrNull { bonus.sourceFilter == GameObjectFilter.Land.withSubtype(Subtype(it)) }
            } else null
            if (boostSubtype != null) putJsonObject("temporaryLandManaBoost") {
                put("subtype", boostSubtype); put("color", bonus!!.color!!.symbol.toString()); put("amount", 1)
            }
            put("commanderColorMana", commanderMana)
            putJsonArray("painManaColors") { painColors.sorted().forEach { add(it) } }
            putJsonArray("colorIdentity") { card.colorIdentity.mapNotNull { mapOf("WHITE" to "W", "BLUE" to "U", "BLACK" to "B", "RED" to "R", "GREEN" to "G")[it.name] }.sorted().forEach { add(it) } }
            put("manaCost", card.manaCost.toString())
            putJsonArray("types") { card.typeLine.cardTypes.map { it.name }.sorted().forEach { add(it) } }
            putJsonArray("subtypes") { card.typeLine.subtypes.map { it.value }.sorted().forEach { add(it) } }
            putJsonArray("keywords") { card.keywords.map { it.name }.sorted().forEach { add(it) } }
            putJsonArray("tags") { intent.tags.map { it.name }.sorted().forEach { add(it) } }
            putJsonArray("spellPolarity") { TargetPolarityAnalyzer.forSpell(card, registry::getCard).forEach { add(it.name) } }
            putJsonArray("manaColors") { manaColors.sorted().forEach { add(it) } }
            put("simpleMana", simpleMana)
            put("simplePermanent", noStatic && noTriggers && noReplacement && (activated.isEmpty() || simpleMana))
            put("plainPermanentCast", card.typeLine.isPermanent && card.script.spellEffect==null && noStatic && noTriggers && noReplacement && (activated.isEmpty() || simpleMana))
            val opponentCreatures = GroupFilter(GameObjectFilter.Creature.opponentControls())
            val removeEvasion = Effects.ForEachInGroup(opponentCreatures,
                Effects.CantHaveKeywords(setOf(Keyword.HEXPROOF,Keyword.SHROUD),EffectTarget.IterationEntity))
            val requireAttacks = Effects.GrantStaticAbility(MustAttack(opponentCreatures),EffectTarget.Controller,Duration.EndOfTurn)
            fun utilityIds(effect: com.wingedsheep.sdk.scripting.effects.Effect) = activated.filter {
                !it.isManaAbility && manaAndTapOnly(it.cost) && it.restrictions.isEmpty() &&
                    it.activateFromZone == Zone.BATTLEFIELD && it.targetRequirements.isEmpty() && it.effect == effect
            }.map { it.id.value }
            putJsonArray("selfReturnToHandAbilities") { utilityIds(Effects.ReturnToHand(EffectTarget.Self)).forEach { add(it) } }
            val evasionRemovalIds = utilityIds(removeEvasion)
            putJsonArray("opponentEvasionRemovalAbilities") { evasionRemovalIds.forEach { add(it) } }
            putJsonArray("opponentAttackRequirementAbilities") { utilityIds(requireAttacks).forEach { add(it) } }
            // An uncertainty signal from native data, not a positive gameplay rule.
            // Exclude certified negative effects; retain every other possible
            // hexproof/shroud reference so future or conditional grants defer.
            val evasionScript = buildJsonObject {
                for ((key,value) in script) {
                    if(key=="activatedAbilities") {
                        val entries=value.jsonArray
                        check(entries.size==activated.size)
                        put(key,JsonArray(entries.filterIndexed { index,_ -> activated[index].id.value !in evasionRemovalIds }))
                    } else put(key,value)
                }
            }.toString()
            put("evasionSensitive", card.keywords.any { it in setOf(Keyword.HEXPROOF,Keyword.SHROUD) } ||
                evasionScript.contains("hexproof",ignoreCase=true) || evasionScript.contains("shroud",ignoreCase=true))
            putJsonArray("equipAbilities") {
                activated.filter { it.isEquipAbility }.forEach { add(it.id.value) }
            }
            putJsonArray("selfDrawAbilities") {
                // Whole effects only: self draw, or the burden-counter draw pattern.
                // Modes, targets, protection, pumps and mixed effects stay uncertain.
                val burdenDraw = Effects.AddCounters(CounterType.BURDEN, 1, EffectTarget.Self) then
                    Effects.DrawCards(DynamicAmounts.countersOnSelf(CounterType.BURDEN))
                for (ability in activated) {
                    val effect = ability.effect
                    val drawCount = (effect as? DrawCardsEffect)?.count as? DynamicAmount.Fixed
                    val fixedDraw = effect is DrawCardsEffect && effect.target == EffectTarget.Controller &&
                        drawCount != null && drawCount.amount in 1..10
                    if (!ability.isManaAbility && ability.timing == TimingRule.InstantSpeed && ability.restrictions.isEmpty() &&
                        ability.targetRequirements.isEmpty() && ability.activateFromZone == Zone.BATTLEFIELD &&
                        (fixedDraw || effect == burdenDraw)) add(ability.id.value)
                }
            }
            put("friendlyStaticPermanent", friendlyStaticPermanent)
            put("unconditionalUntapped", noReplacement)
            put("entersTapped", intent.entersTapped)
            // Only a whole trigger consisting of a free May(Draw N) qualifies.
            // Costs, alternative branches, choices and mixed effects remain strategic.
            putJsonObject("freeOptionalDraws") {
                for (ability in card.script.triggeredAbilities) {
                    val gate = ability.effect as? GatedEffect ?: continue
                    val draw = gate.then as? DrawCardsEffect ?: continue
                    val count = draw.count as? DynamicAmount.Fixed ?: continue
                    if (gate.gate is Gate.MayDecide && gate.otherwise == null &&
                        (gate.decisionMaker == null || gate.decisionMaker == EffectTarget.Controller) &&
                        draw.target == EffectTarget.Controller && count.amount in 1..10) {
                        put(ability.id.value, count.amount)
                    }
                }
            }
            putJsonObject("simpleLifeGainTriggers") {
                for (ability in card.script.triggeredAbilities) {
                    val gain = ability.effect as? GainLifeEffect ?: continue
                    val count = gain.amount as? DynamicAmount.Fixed ?: continue
                    if (gain.target == EffectTarget.Controller && count.amount in 1..10) {
                        put(ability.id.value, count.amount)
                    }
                }
            }
            putJsonObject("freeOptionalLibraryReorders") {
                for (ability in card.script.triggeredAbilities) {
                    val gate=ability.effect as? GatedEffect ?: continue
                    if (gate.gate !is Gate.MayDecide || gate.otherwise!=null ||
                        gate.decisionMaker!=null && gate.decisionMaker!=EffectTarget.Controller) continue
                    val count=(1..10).firstOrNull { gate.then==Patterns.Library.lookAtTopAndReorder(it) } ?: continue
                    put(ability.id.value,count)
                }
            }
            putJsonObject("positiveCounterTriggers") {
                for (ability in card.script.triggeredAbilities) {
                    val effect=ability.effect as? AddCountersEffect ?: continue
                    if(effect.counterType == CounterType.PLUS_ONE_PLUS_ONE && effect.count in 1..10 &&
                        effect.target is EffectTarget.BoundVariable && ability.allTargetRequirements.size == 1) {
                        put(effect.description,effect.count)
                    }
                }
            }
            putJsonObject("activatedAbilityCounts") {
                put("mana", activated.count { it.isManaAbility })
                put("nonMana", activated.count { !it.isManaAbility })
            }
            val statics = card.script.staticAbilities
            val attackLock = CantAttack(filter = GroupFilter.attachedCreature())
            val blockLock = CantBlock(filter = GroupFilter.attachedCreature())
            val activationLocks = statics.filterIsInstance<PreventActivatedAbilities>()
            val activationLock = activationLocks.singleOrNull()
            val attachedActivationLock = activationLock != null && !activationLock.anyZone &&
                activationLock.filter == GameObjectFilter.Permanent.attachedToBySource() &&
                activationLock.abilities in setOf(ActivatedAbilityScope.ALL, ActivatedAbilityScope.NON_MANA)
            if (noTriggers && noReplacement && activated.isEmpty() && attackLock in statics && blockLock in statics &&
                (activationLocks.isEmpty() || attachedActivationLock) && statics.all {
                    it == attackLock || it == blockLock || it == activationLock
                }) {
                putJsonObject("attachedCombatLock") {
                    put("activatedAbilities", when(activationLock?.abilities) {
                        ActivatedAbilityScope.ALL -> "all"
                        ActivatedAbilityScope.NON_MANA -> "nonMana"
                        else -> "none"
                    })
                }
            }
            put("removesAttachedAbilities", card.script.staticAbilities.any {
                it is LoseAllAbilities && !it.exceptManaAbilities && it.filter == GroupFilter.attachedCreature()
            })
            put("drawSensitive", card.script.triggeredAbilities.any {
                drawSensitive(json.encodeToJsonElement(EventPattern.serializer(), it.trigger))
            } || drawSensitive(script["staticAbilities"] ?: JsonNull) ||
                drawSensitive(script["replacementEffects"] ?: JsonNull))
            put("staticValue", intent.staticPriorValue)
            put("repeatable", intent.repeatable)
            put("affectsOpponent", intent.affectsOpponent)
            putJsonArray("feeds") { synergy.feeds.map { it.name }.sorted().forEach { add(it) } }
            putJsonArray("rewards") { synergy.rewards.map { it.name }.sorted().forEach { add(it) } }
            putJsonArray("tribesRewarded") { synergy.tribesRewarded.map { it.value }.sorted().forEach { add(it) } }
        }
    }
}
