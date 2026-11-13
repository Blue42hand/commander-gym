package org.commandergym.argentum

import com.wingedsheep.gameserver.config.GameBeansConfig
import com.wingedsheep.gameserver.config.GameProperties
import kotlinx.serialization.json.*
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue
import kotlin.test.assertFailsWith

class PublicCardFactsTest {
    private val registry = GameBeansConfig(GameProperties()).cardRegistry()
    private val revision = "12ab2f0ed5bd018334521dc52497e6444f26f5a9"

    @Test fun tappedBasicLandRampContractsUseTheWholeNativeActivatedEffect() {
        val yes=mapOf("Wayfarer's Bauble" to "Wayfarer's Bauble:1","Myriad Landscape" to "Myriad Landscape:2","Sakura-Tribe Elder" to "Sakura-Tribe Elder:1")
        val no=listOf("Mind Stone","Expedition Map","Solemn Simulacrum","Elvish Reclaimer")
        val cards=PublicCardFacts.export(registry,yes.keys+no,revision)["cards"]!!.jsonObject
        for((name,id) in yes)assertEquals(listOf(id),cards[name]!!.jsonObject["selfTappedLandRampAbilities"]!!.jsonArray.map { it.jsonPrimitive.content })
        for(name in no)assertTrue(cards[name]!!.jsonObject["selfTappedLandRampAbilities"]!!.jsonArray.isEmpty(),name)
        val bauble=registry.getCard("Wayfarer's Bauble")!!;val activated=bauble.script.activatedAbilities.single()
        val mixed=bauble.copy(script=bauble.script.copy(activatedAbilities=listOf(activated.copy(
            effect=com.wingedsheep.sdk.scripting.effects.CompositeEffect(listOf(activated.effect,com.wingedsheep.sdk.dsl.Effects.DrawCards(1)))))))
        val overlay=com.wingedsheep.engine.registry.CardRegistry(registry);overlay.register(mixed)
        assertTrue(PublicCardFacts.export(overlay,listOf(mixed.name),revision)["cards"]!!.jsonObject[mixed.name]!!.jsonObject["selfTappedLandRampAbilities"]!!.jsonArray.isEmpty())
    }

    @Test fun combatGrantedDrawsDoNotBecomeDrawPunishers() {
        val cards=PublicCardFacts.export(registry,listOf("Reconnaissance Mission","Underworld Dreams","Laboratory Maniac"),revision)["cards"]!!.jsonObject
        assertFalse(cards["Reconnaissance Mission"]!!.jsonObject["drawSensitive"]!!.jsonPrimitive.boolean)
        for(name in listOf("Underworld Dreams","Laboratory Maniac"))
            assertTrue(cards[name]!!.jsonObject["drawSensitive"]!!.jsonPrimitive.boolean,name)
        // A granted ability's source identity remains distinct from its host.
        assertTrue(cards["Reconnaissance Mission"]!!.jsonObject["freeOptionalDraws"]!!.jsonObject.isEmpty())
        assertEquals(1,cards["Reconnaissance Mission"]!!.jsonObject["grantedFreeOptionalDraws"]!!.jsonObject["Reconnaissance Mission:1"]!!.jsonPrimitive.int)
    }

    @Test fun grantedFreeDrawsRequireTheWholeOptionalControllerDrawEffect() {
        val mission=registry.getCard("Reconnaissance Mission")!!
        val grant=mission.script.staticAbilities.single() as com.wingedsheep.sdk.scripting.GrantTriggeredAbility
        for(effect in listOf(
            com.wingedsheep.sdk.dsl.Effects.DrawCards(1),
            com.wingedsheep.sdk.scripting.effects.CompositeEffect(listOf(grant.ability.effect,com.wingedsheep.sdk.dsl.Effects.GainLife(1)))
        )) {
            val changed=mission.copy(script=mission.script.copy(staticAbilities=listOf(grant.copy(ability=grant.ability.copy(effect=effect)))))
            val overlay=com.wingedsheep.engine.registry.CardRegistry(registry);overlay.register(changed)
            assertTrue(PublicCardFacts.export(overlay,listOf(changed.name),revision)["cards"]!!.jsonObject[changed.name]!!.jsonObject["grantedFreeOptionalDraws"]!!.jsonObject.isEmpty())
        }
    }

    @Test fun grantedDrawEventTriggersRemainDrawSensitive() {
        val mission=registry.getCard("Reconnaissance Mission")!!
        val grant=mission.script.staticAbilities.single() as com.wingedsheep.sdk.scripting.GrantTriggeredAbility
        val drawTrigger=registry.getCard("Underworld Dreams")!!.script.triggeredAbilities.single().trigger
        val changed=mission.copy(script=mission.script.copy(staticAbilities=listOf(
            grant.copy(ability=grant.ability.copy(trigger=drawTrigger)))))
        val overlay=com.wingedsheep.engine.registry.CardRegistry(registry)
        overlay.register(changed)
        assertTrue(PublicCardFacts.export(overlay,listOf(changed.name),revision)["cards"]!!.jsonObject[changed.name]!!.jsonObject["drawSensitive"]!!.jsonPrimitive.boolean)
    }

    @Test fun zeroXDrawContractChecksTheActualBaseEffect() {
        val cards=PublicCardFacts.export(registry,listOf("Finale of Revelation","Blue Sun's Zenith","Chord of Calling","Pull from Tomorrow"),revision)["cards"]!!.jsonObject
        assertTrue(cards["Finale of Revelation"]!!.jsonObject["emptySelfDrawAtZeroX"]!!.jsonPrimitive.boolean)
        for(name in listOf("Blue Sun's Zenith","Chord of Calling","Pull from Tomorrow"))
            assertFalse(cards[name]!!.jsonObject["emptySelfDrawAtZeroX"]!!.jsonPrimitive.boolean,name)
    }

    @Test fun resourceDrawSpellContractExcludesCombatTricksTargetsAndUnreviewedSelection() {
        val positive=listOf("Flow of Knowledge","Thirst for Knowledge","Thirst for Discovery","Divination","Concentrate")
        val negative=listOf("Brainstorm","Opt","Compulsive Research","Inspiring Call","High Tide","Fact or Fiction")
        val cards=PublicCardFacts.export(registry,positive+negative,revision)["cards"]!!.jsonObject
        for(name in positive)assertTrue(cards[name]!!.jsonObject["selfResourceDrawSpell"]!!.jsonPrimitive.boolean,name)
        for(name in negative)assertFalse(cards[name]!!.jsonObject["selfResourceDrawSpell"]!!.jsonPrimitive.boolean,name)
    }

    @Test fun beneficialPlayerDrawContractsMatchWholeNetPositiveEffects() {
        val cards=PublicCardFacts.export(registry,listOf("Compulsive Research","High Tide","Brainstorm"),revision)["cards"]!!.jsonObject
        assertEquals(3,cards["Compulsive Research"]!!.jsonObject["beneficialPlayerDrawCount"]!!.jsonPrimitive.int)
        for(name in listOf("High Tide","Brainstorm"))assertFalse(cards[name]!!.jsonObject.containsKey("beneficialPlayerDrawCount"))
    }

    @Test fun temporaryPumpsAndPowerManaAreCertifiedIndependently() {
        val cards=PublicCardFacts.export(registry,listOf("Timberwatch Elf","Marwyn, the Nurturer","Priest of Titania","Fyndhorn Elves","High Tide"),revision)["cards"]!!.jsonObject
        assertEquals(listOf("Timberwatch Elf:1"),cards["Timberwatch Elf"]!!.jsonObject["positiveStatBoostAbilities"]!!.jsonArray.map { it.jsonPrimitive.content })
        assertEquals(listOf("Marwyn, the Nurturer:2"),cards["Marwyn, the Nurturer"]!!.jsonObject["powerManaAbilities"]!!.jsonArray.map { it.jsonPrimitive.content })
        for(name in listOf("Priest of Titania","Fyndhorn Elves"))assertTrue(cards[name]!!.jsonObject["powerManaAbilities"]!!.jsonArray.isEmpty())
        assertTrue(cards["Fyndhorn Elves"]!!.jsonObject["plainPermanentCast"]!!.jsonPrimitive.boolean)
        assertFalse(cards["High Tide"]!!.jsonObject["plainPermanentCast"]!!.jsonPrimitive.boolean)
    }

    @Test fun entryReductionCertifiesTheWholeUnconditionalNativeTrigger() {
        val cards=PublicCardFacts.export(registry,listOf("Nowhere to Run","Arrest","Generous Visitor"),revision)["cards"]!!.jsonObject
        val effect=cards["Nowhere to Run"]!!.jsonObject["entryStatReduction"]!!.jsonObject
        assertEquals(-3,effect["toughness"]!!.jsonPrimitive.int)
        assertEquals("Nowhere to Run:1",effect["abilityId"]!!.jsonPrimitive.content)
        assertTrue(effect["suppressesHexproof"]!!.jsonPrimitive.boolean)
        assertTrue(effect["suppressesWard"]!!.jsonPrimitive.boolean)
        for(name in listOf("Arrest","Generous Visitor"))assertFalse(cards[name]!!.jsonObject.containsKey("entryStatReduction"))
    }

    @Test fun selfReturnContractsRequireThePureNativeSelfEffect() {
        val cards=PublicCardFacts.export(registry,listOf("Flickering Ward","Wirewood Symbiote","Bident of Thassa"),revision)["cards"]!!.jsonObject
        assertEquals(listOf("Flickering Ward:1"),cards["Flickering Ward"]!!.jsonObject["selfReturnToHandAbilities"]!!.jsonArray.map { it.jsonPrimitive.content })
        for(name in listOf("Wirewood Symbiote","Bident of Thassa"))assertTrue(cards[name]!!.jsonObject["selfReturnToHandAbilities"]!!.jsonArray.isEmpty())
    }

    @Test fun utilityEffectsMatchNativeScopeAndKeepConditionalEvasionUncertain() {
        val cards=PublicCardFacts.export(registry,listOf("Arcane Lighthouse","Bident of Thassa","Swiftfoot Boots","Wirewood Symbiote"),revision)["cards"]!!.jsonObject
        assertEquals(listOf("Arcane Lighthouse:2"),cards["Arcane Lighthouse"]!!.jsonObject["opponentEvasionRemovalAbilities"]!!.jsonArray.map { it.jsonPrimitive.content })
        assertEquals(listOf("Bident of Thassa:2"),cards["Bident of Thassa"]!!.jsonObject["opponentAttackRequirementAbilities"]!!.jsonArray.map { it.jsonPrimitive.content })
        assertFalse(cards["Arcane Lighthouse"]!!.jsonObject["evasionSensitive"]!!.jsonPrimitive.boolean)
        assertTrue(cards["Swiftfoot Boots"]!!.jsonObject["evasionSensitive"]!!.jsonPrimitive.boolean)
        assertFalse(cards["Wirewood Symbiote"]!!.jsonObject["evasionSensitive"]!!.jsonPrimitive.boolean)
    }

    @Test fun combatLocksPreserveTriggeredAndStaticAbilitiesAndHaveExplicitActivationScope() {
        val cards=PublicCardFacts.export(registry,listOf("Arrest","Pacifism","Darksteel Mutation","Talrand, Sky Summoner","Elvish Archdruid"),revision)["cards"]!!.jsonObject
        assertEquals("all",cards["Arrest"]!!.jsonObject["attachedCombatLock"]!!.jsonObject["activatedAbilities"]!!.jsonPrimitive.content)
        assertEquals("none",cards["Pacifism"]!!.jsonObject["attachedCombatLock"]!!.jsonObject["activatedAbilities"]!!.jsonPrimitive.content)
        assertFalse(cards["Darksteel Mutation"]!!.jsonObject.containsKey("attachedCombatLock"))
        assertEquals(0,cards["Talrand, Sky Summoner"]!!.jsonObject["activatedAbilityCounts"]!!.jsonObject["nonMana"]!!.jsonPrimitive.int)
        assertEquals(1,cards["Elvish Archdruid"]!!.jsonObject["activatedAbilityCounts"]!!.jsonObject["mana"]!!.jsonPrimitive.int)
    }

    @Test fun positiveCounterTriggersRequireWholeFixedPlusOneEffect() {
        val cards=PublicCardFacts.export(registry,listOf("Generous Visitor","The One Ring","Beastmaster Ascension"),revision)["cards"]!!.jsonObject
        assertEquals(listOf(1),cards["Generous Visitor"]!!.jsonObject["positiveCounterTriggers"]!!.jsonObject.values.map { it.jsonPrimitive.int })
        for(name in listOf("The One Ring","Beastmaster Ascension")) assertTrue(cards[name]!!.jsonObject["positiveCounterTriggers"]!!.jsonObject.isEmpty())
    }

    @Test fun bounceUntapContractUsesTheWholeTypedCostAndEffect() {
        val cards = PublicCardFacts.export(registry,listOf("Wirewood Symbiote","Quirion Ranger","Wirewood Lodge","Skullclamp"),revision)["cards"]!!.jsonObject
        assertEquals(listOf("Wirewood Symbiote:1"),cards["Wirewood Symbiote"]!!.jsonObject["bounceTargetUntapAbilities"]!!.jsonArray.map { it.jsonPrimitive.content })
        for(name in listOf("Wirewood Lodge","Skullclamp")) assertTrue(cards[name]!!.jsonObject["bounceTargetUntapAbilities"]!!.jsonArray.isEmpty())
        assertEquals(listOf("Quirion Ranger:1"),cards["Quirion Ranger"]!!.jsonObject["bounceTargetUntapAbilities"]!!.jsonArray.map { it.jsonPrimitive.content })
    }

    @Test fun equipAbilityIdsUseTheNativeEquipFlag() {
        val cards=PublicCardFacts.export(registry,listOf("Skullclamp","The One Ring"),revision)["cards"]!!.jsonObject
        assertEquals(listOf("Skullclamp:2"),cards["Skullclamp"]!!.jsonObject["equipAbilities"]!!.jsonArray.map { it.jsonPrimitive.content })
        assertTrue(cards["The One Ring"]!!.jsonObject["equipAbilities"]!!.jsonArray.isEmpty())
    }

    @Test fun protectionEquipmentRequiresTheWholeCanonicalNativeShape() {
        val names=listOf("Swiftfoot Boots","Skullclamp","The One Ring")
        val cards=PublicCardFacts.export(registry,names,revision)["cards"]!!.jsonObject
        assertEquals(listOf("HASTE","HEXPROOF"),cards["Swiftfoot Boots"]!!.jsonObject["protectionEquipmentKeywords"]!!.jsonArray.map { it.jsonPrimitive.content })
        for(name in names.drop(1))assertFalse(cards[name]!!.jsonObject.containsKey("protectionEquipmentKeywords"))
        val boots=registry.getCard("Swiftfoot Boots")!!
        val equip=boots.script.activatedAbilities.single()
        val variants=listOf(
            boots.copy(script=boots.script.copy(activatedAbilities=listOf(equip.copy(timing=com.wingedsheep.sdk.scripting.TimingRule.InstantSpeed)))),
            boots.copy(script=boots.script.copy(activatedAbilities=listOf(equip.copy(effect=com.wingedsheep.sdk.dsl.Effects.DrawCards(1))))),
            boots.copy(script=boots.script.copy(staticAbilities=listOf(com.wingedsheep.sdk.scripting.GrantKeyword(com.wingedsheep.sdk.core.Keyword.HEXPROOF,com.wingedsheep.sdk.scripting.filters.unified.GroupFilter.AllCreaturesYouControl)))),
            boots.copy(script=boots.script.copy(triggeredAbilities=registry.getCard("Alela, Artful Provocateur")!!.script.triggeredAbilities))
        )
        for(card in variants) {
            val overlay=com.wingedsheep.engine.registry.CardRegistry(registry)
            overlay.register(card)
            assertFalse(PublicCardFacts.export(overlay,listOf(card.name),revision)["cards"]!!.jsonObject[card.name]!!.jsonObject.containsKey("protectionEquipmentKeywords"))
        }
    }

    @Test fun selfDrawAbilitiesIdentifyTheActivatedEffectInsteadOfWholeCardTags() {
        val cards=PublicCardFacts.export(registry,listOf("The One Ring","Skullclamp","Solemn Simulacrum"),revision)["cards"]!!.jsonObject
        assertEquals(listOf("The One Ring:3"),cards["The One Ring"]!!.jsonObject["selfDrawAbilities"]!!.jsonArray.map { it.jsonPrimitive.content })
        for(name in listOf("Skullclamp","Solemn Simulacrum")) assertTrue(cards[name]!!.jsonObject["selfDrawAbilities"]!!.jsonArray.isEmpty())
    }

    @Test fun simpleLifeGainTriggerRequiresAWholeFixedControllerGain() {
        val cards = PublicCardFacts.export(registry, listOf("Authority of the Consuls", "Sythis, Harvest's Hand", "High Tide"), revision)["cards"]!!.jsonObject
        val gains = cards["Authority of the Consuls"]!!.jsonObject["simpleLifeGainTriggers"]!!.jsonObject
        assertEquals(1, gains["Authority of the Consuls:1"]!!.jsonPrimitive.int)
        for (name in listOf("Sythis, Harvest's Hand", "High Tide")) assertTrue(cards[name]!!.jsonObject["simpleLifeGainTriggers"]!!.jsonObject.isEmpty())
    }

    @Test fun temporaryLandManaBonusRequiresTheWholeReviewedEffect() {
        val cards = PublicCardFacts.export(registry, listOf("High Tide", "Dark Ritual", "Forest", "Elvish Archdruid"), revision)["cards"]!!.jsonObject
        val bonus = cards["High Tide"]!!.jsonObject["temporaryLandManaBoost"]!!.jsonObject
        assertEquals("Island", bonus["subtype"]!!.jsonPrimitive.content)
        assertEquals("U", bonus["color"]!!.jsonPrimitive.content)
        assertEquals(1, bonus["amount"]!!.jsonPrimitive.int)
        for (name in listOf("Dark Ritual", "Forest", "Elvish Archdruid")) assertFalse("temporaryLandManaBoost" in cards[name]!!.jsonObject)
    }

    @Test fun utilityManaAndCheckLandEntryRemainSeparateFromSimpleActivation() {
        val cards = PublicCardFacts.export(registry, listOf("Myriad Landscape", "Arcane Lighthouse", "Sunpetal Grove", "Underground River", "Serra's Sanctum", "Sanctum Weaver", "Command Tower", "Adarkar Wastes", "Alela, Artful Provocateur"), revision)["cards"]!!.jsonObject
        fun fact(name: String) = cards[name]!!.jsonObject
        assertEquals(listOf("C"), fact("Myriad Landscape")["manaColors"]!!.jsonArray.map { it.jsonPrimitive.content })
        assertEquals(listOf("C"), fact("Arcane Lighthouse")["manaColors"]!!.jsonArray.map { it.jsonPrimitive.content })
        assertEquals(listOf("B", "C", "U"), fact("Underground River")["manaColors"]!!.jsonArray.map { it.jsonPrimitive.content })
        assertEquals(listOf("C", "U", "W"), fact("Adarkar Wastes")["manaColors"]!!.jsonArray.map { it.jsonPrimitive.content })
        assertEquals(listOf("U", "W"), fact("Adarkar Wastes")["painManaColors"]!!.jsonArray.map { it.jsonPrimitive.content })
        assertTrue(fact("Command Tower")["commanderColorMana"]!!.jsonPrimitive.boolean)
        assertEquals(listOf("B", "U", "W"), fact("Alela, Artful Provocateur")["colorIdentity"]!!.jsonArray.map { it.jsonPrimitive.content })
        assertFalse(fact("Myriad Landscape")["simpleMana"]!!.jsonPrimitive.boolean)
        assertFalse(fact("Arcane Lighthouse")["simpleMana"]!!.jsonPrimitive.boolean)
        assertTrue(fact("Myriad Landscape")["entersTapped"]!!.jsonPrimitive.boolean)
        assertEquals(listOf("Forest", "Plains"), fact("Sunpetal Grove")["untappedIfLandSubtypes"]!!.jsonArray.map { it.jsonPrimitive.content })
        assertFalse(fact("Sunpetal Grove")["unconditionalUntapped"]!!.jsonPrimitive.boolean)
        assertTrue(fact("Serra's Sanctum")["variableMana"]!!.jsonPrimitive.boolean)
        assertTrue(fact("Sanctum Weaver")["variableMana"]!!.jsonPrimitive.boolean)
        assertFalse(fact("Command Tower")["variableMana"]!!.jsonPrimitive.boolean)
        assertEquals(listOf("W"), fact("Serra's Sanctum")["manaColors"]!!.jsonArray.map { it.jsonPrimitive.content })
    }

    @Test fun attachedAbilitySuppressionComesFromTypedStaticAbilities() {
        val cards = PublicCardFacts.export(registry, listOf("Utter Insignificance", "Lignify", "Darksteel Mutation", "Arrest", "Faith's Fetters", "Forest"), revision)["cards"]!!.jsonObject
        for (name in listOf("Utter Insignificance", "Lignify", "Darksteel Mutation")) {
            assertTrue(cards[name]!!.jsonObject["removesAttachedAbilities"]!!.jsonPrimitive.boolean)
        }
        for (name in listOf("Arrest", "Faith's Fetters", "Forest")) {
            assertFalse(cards[name]!!.jsonObject["removesAttachedAbilities"]!!.jsonPrimitive.boolean)
        }
    }

    @Test fun developmentAndTargetingContractsComeFromSmallTypedPrimitives() {
        val cards = PublicCardFacts.export(registry, listOf("Patchwork Banner", "Arcane Signet", "Sol Ring", "Staff of Domination", "Nowhere to Run", "Mox Amber", "Serra's Sanctum"), revision)["cards"]!!.jsonObject
        fun fact(name: String) = cards[name]!!.jsonObject
        assertEquals(1, fact("Patchwork Banner")["developmentMana"]!!.jsonPrimitive.int)
        assertEquals(1, fact("Arcane Signet")["developmentMana"]!!.jsonPrimitive.int)
        assertEquals(2, fact("Sol Ring")["developmentMana"]!!.jsonPrimitive.int)
        assertEquals(setOf("W", "U", "B", "R", "G"), fact("Patchwork Banner")["manaColors"]!!.jsonArray.map { it.jsonPrimitive.content }.toSet())
        for (name in listOf("Staff of Domination", "Mox Amber", "Serra's Sanctum")) assertEquals(0, fact(name)["developmentMana"]!!.jsonPrimitive.int)
        assertEquals(listOf("Staff of Domination:1"), fact("Staff of Domination")["manaOnlySelfUntapAbilities"]!!.jsonArray.map { it.jsonPrimitive.content })
        val reduction = fact("Nowhere to Run")["fixedStatReductions"]!!.jsonObject.values.single().jsonObject
        assertEquals(-3, reduction["power"]!!.jsonPrimitive.int)
        assertEquals(-3, reduction["toughness"]!!.jsonPrimitive.int)
    }

    @Test fun friendlyStaticProtectionIsSeparateFromReactiveOrMixedProtection() {
        val cards=PublicCardFacts.export(registry,listOf("Sephara, Sky's Blade","Heroic Intervention","Darksteel Mutation","Etchings of the Chosen","Imperious Perfect","Forest"),revision)["cards"]!!.jsonObject
        assertTrue(cards["Sephara, Sky's Blade"]!!.jsonObject["friendlyStaticPermanent"]!!.jsonPrimitive.boolean)
        for(name in listOf("Heroic Intervention","Darksteel Mutation","Etchings of the Chosen","Imperious Perfect","Forest")) {
            assertFalse(cards[name]!!.jsonObject["friendlyStaticPermanent"]!!.jsonPrimitive.boolean,name)
        }
    }

    @Test fun simpleUntapContractsExcludeMaterialCostsAndMixedEffects() {
        val cards=PublicCardFacts.export(registry,listOf("Wirewood Lodge","Staff of Domination","Wirewood Symbiote","Timberwatch Elf"),revision)["cards"]!!.jsonObject
        fun ids(name:String)=cards[name]!!.jsonObject["simpleTargetUntapAbilities"]!!.jsonArray.map { it.jsonPrimitive.content }
        assertEquals(listOf("Wirewood Lodge:2"),ids("Wirewood Lodge"))
        assertEquals(listOf("Staff of Domination:3"),ids("Staff of Domination"))
        assertTrue(ids("Wirewood Symbiote").isEmpty());assertTrue(ids("Timberwatch Elf").isEmpty())
    }

    @Test fun freeLibraryReorderContractExcludesDrawAndMaterialSideEffects() {
        val cards=PublicCardFacts.export(registry,listOf("Mirri's Guile","Sylvan Library","Sensei's Divining Top","Curiosity","Forest"),revision)["cards"]!!.jsonObject
        val guile=cards["Mirri's Guile"]!!.jsonObject["freeOptionalLibraryReorders"]!!.jsonObject
        assertEquals(3,guile["Mirri's Guile:1"]!!.jsonPrimitive.int)
        for(name in listOf("Sylvan Library","Sensei's Divining Top","Curiosity","Forest")) {
            assertTrue(cards[name]!!.jsonObject["freeOptionalLibraryReorders"]!!.jsonObject.isEmpty(),name)
        }
    }

    @Test fun ordinaryConditionalTappedLandsExposeAConservativeEntryContract() {
        val cards=PublicCardFacts.export(registry,listOf("Shattered Sanctum","Canopy Vista","Sunpetal Grove","Forest","Overgrown Tomb"),revision)["cards"]!!.jsonObject
        for(name in listOf("Shattered Sanctum","Canopy Vista","Sunpetal Grove")) assertTrue(cards[name]!!.jsonObject["conditionalTappedEntry"]!!.jsonPrimitive.boolean,name)
        for(name in listOf("Forest","Overgrown Tomb")) assertFalse(cards[name]!!.jsonObject["conditionalTappedEntry"]!!.jsonPrimitive.boolean,name)
    }

    @Test fun publicFactsDistinguishOrdinaryManaFromChoicesAndTriggeredBoards() {
        val names = listOf("Forest", "Sol Ring", "Arcane Signet", "Sythis, Harvest's Hand", "Vanquisher's Banner", "Mesa Enchantress", "Underworld Dreams")
        val export = PublicCardFacts.export(registry, names, revision)
        assertEquals(revision, export["engineRevision"]!!.jsonPrimitive.content)
        val cards = export["cards"]!!.jsonObject
        fun flag(name: String, field: String) = cards[name]!!.jsonObject[field]!!.jsonPrimitive.boolean
        assertTrue(flag("Forest", "simpleMana"))
        assertTrue(flag("Sol Ring", "simpleMana"))
        assertFalse(flag("Arcane Signet", "simpleMana"))
        assertFalse(flag("Sythis, Harvest's Hand", "simplePermanent"))
        assertEquals(1, cards["Mesa Enchantress"]!!.jsonObject["freeOptionalDraws"]!!.jsonObject["Mesa Enchantress:1"]!!.jsonPrimitive.int)
        assertTrue(flag("Underworld Dreams", "drawSensitive"))
        assertTrue(cards["Vanquisher's Banner"]!!.jsonObject["tags"]!!.jsonArray.any { it.jsonPrimitive.content == "ANTHEM" })
        assertEquals(export, PublicCardFacts.export(registry, names.reversed(), revision))
        assertFalse(export.toString().contains("libraryOrder"))
        assertFailsWith<IllegalArgumentException> { PublicCardFacts.export(registry, listOf("not a card"), revision) }
    }
}
