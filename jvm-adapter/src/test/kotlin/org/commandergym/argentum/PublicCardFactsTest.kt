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
