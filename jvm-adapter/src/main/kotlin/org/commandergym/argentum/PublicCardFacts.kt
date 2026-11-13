package org.commandergym.argentum

import com.wingedsheep.ai.engine.knowledge.CardIntentAnalyzer
import com.wingedsheep.ai.engine.knowledge.SynergyAxes
import com.wingedsheep.ai.engine.knowledge.TargetPolarityAnalyzer
import com.wingedsheep.engine.registry.CardRegistry
import com.wingedsheep.sdk.core.Zone
import com.wingedsheep.sdk.scripting.LoseAllAbilities
import com.wingedsheep.sdk.scripting.ModifyStats
import com.wingedsheep.sdk.scripting.GrantKeyword
import com.wingedsheep.sdk.scripting.TimingRule
import com.wingedsheep.sdk.core.Keyword
import com.wingedsheep.sdk.scripting.EntersWithChoice
import com.wingedsheep.sdk.scripting.ChoiceType
import com.wingedsheep.sdk.scripting.Duration
import com.wingedsheep.sdk.scripting.costs.manaCostOrNull
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
            // Match exact SDK descriptions at the native target-selection seam;
            // no Oracle text parsing or guesses from a generic REMOVAL tag.
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
