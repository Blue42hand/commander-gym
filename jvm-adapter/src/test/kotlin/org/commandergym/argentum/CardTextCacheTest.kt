package org.commandergym.argentum

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/**
 * Unit tests for CardTextCache - verifies deduplication and safety.
 */
class CardTextCacheTest {

    @Test
    fun `initialization creates fresh cache`() {
        val manager = CardTextCache("test-session-1")
        
        val entry = manager.initialize("deck-hash-abc123")
        
        assertEquals(1, manager.getCardCount(), "Should have one initialization entry")
        assertTrue(entry.cardId == "deck-initialized", "First entry should be deck initialization marker")
    }

    @Test
    fun `repeated card additions are deduplicated`() {
        val manager = CardTextCache("test-session-2")
        
        // Add same card twice
        val firstAddition = manager.addOrUpdateCard(
            cardId = "card-1",
            name = "Test Card",
            manaCost = "{U}",
            typeLine = "Creature",
            oracleText = "Flying"
        )
        
        val secondAddition = manager.addOrUpdateCard(
            cardId = "card-1",
            name = "Test Card",
            manaCost = "{U}",
            typeLine = "Creature", 
            oracleText = "Flying"
        )
        
        assertTrue(firstAddition, "First addition should be new")
        assertFalse(secondAddition, "Second addition of same card should not be new")
        assertEquals(1, manager.getCardCount(), "Should still have only one entry")
    }

    @Test
    fun `different cards are all cached`() {
        val manager = CardTextCache("test-session-3")
        
        manager.initialize("deck-hash-xyz789")
        
        // Add multiple different cards
        manager.addOrUpdateCard(
            cardId = "card-1", name = "Card One", manaCost = "{U}", typeLine = null, oracleText = null
        )
        manager.addOrUpdateCard(
            cardId = "card-2", name = "Card Two", manaCost = "{R}", typeLine = null, oracleText = null
        )
        manager.addOrUpdateCard(
            cardId = "card-3", name = "Card Three", manaCost = null, typeLine = null, oracleText = null
        )
        
        assertEquals(4, manager.getCardCount(), "Should have initialization + 3 cards")
    }

    @Test
    fun `cache enforces maximum size`() {
        val manager = CardTextCache("test-session-4", maxEntries = 5)
        
        // Add more entries than cache can hold
        for (i in 0..10) {
            manager.addOrUpdateCard(
                cardId = "card-$i", 
                name = "Card $i", 
                manaCost = null, 
                typeLine = null, 
                oracleText = null
            )
        }
        
        // Should have at most maxEntries + 1 (initialization)
        assertTrue(manager.getCardCount() <= 6, "Cache size should not exceed limit")
    }

    @Test
    fun `clearCache removes all entries`() {
        val manager = CardTextCache("test-session-5")
        
        manager.initialize("deck-hash-123")
        manager.addOrUpdateCard(
            cardId = "card-1", name = "Test", manaCost = "{U}", typeLine = null, oracleText = null
        )
        
        assertEquals(2, manager.getCardCount(), "Should have 2 entries before clear")
        
        manager.clearCache()
        
        assertEquals(0, manager.getCardCount(), "Should be empty after clear")
    }

    @Test
    fun `getDeduplicatedCardList returns unique cards`() {
        val manager = CardTextCache("test-session-6")
        
        manager.initialize("deck-hash-test")
        manager.addOrUpdateCard(
            cardId = "card-1", name = "Flying Creature", manaCost = "{U}", typeLine = "Creature — Angel", oracleText = "Flying"
        )
        manager.addOrUpdateCard(
            cardId = "card-2", name = "Ground Creature", manaCost = "{R}", typeLine = "Creature — Human Warrior", oracleText = null
        )
        
        val entries = manager.getDeduplicatedCardList()
        
        assertEquals(2, entries.size, "Should have 2 card descriptions")
        assertTrue(entries.any { it.contains("Flying Creature") }, "Should include first card")
        assertTrue(entries.any { it.contains("Ground Creature") }, "Should include second card")
    }

    @Test
    fun `hasCard correctly identifies cached cards`() {
        val manager = CardTextCache("test-session-7")
        
        manager.initialize("deck-hash-test")
        manager.addOrUpdateCard(
            cardId = "card-1", name = "Test", manaCost = "{U}", typeLine = null, oracleText = null
        )
        
        assertTrue(manager.hasCard("card-1"), "Should have cached card")
        assertFalse(manager.hasCard("card-2"), "Should not have uncached card")
    }

    @Test
    fun `getCardEntry returns correct entry`() {
        val manager = CardTextCache("test-session-8")
        
        manager.initialize("deck-hash-test")
        manager.addOrUpdateCard(
            cardId = "card-1", 
            name = "Flying Angel", 
            manaCost = "{U}{W}", 
            typeLine = "Creature — Angel", 
            oracleText = "Flying, First Strike"
        )
        
        val entry = manager.getCardEntry("card-1")
        
        assertNotNull(entry, "Should return cached entry")
        assertEquals("card-1", entry.cardId)
        assertEquals("Flying Angel", entry.name)
        assertEquals("{U}{W}", entry.manaCost)
        assertEquals("Creature — Angel", entry.typeLine)
        assertEquals("Flying, First Strike", entry.oracleText)
    }

    @Test
    fun `estimateTokenSavings calculates correctly`() {
        val manager = CardTextCache("test-session-9")
        
        manager.initialize("deck-hash-test")
        for (i in 0..4) {
            manager.addOrUpdateCard(
                cardId = "card-$i", name = "Card $i", manaCost = null, typeLine = null, oracleText = null
            )
        }
        
        val savings = manager.estimateTokenSavings()
        
        // 1 initialization + 5 cards * 100 tokens each
        assertEquals(600L, savings, "Should estimate correct token savings")
    }

    @Test
    fun `getAllEntries returns all cached cards`() {
        val manager = CardTextCache("test-session-10")
        
        manager.initialize("deck-hash-test")
        manager.addOrUpdateCard(
            cardId = "card-1", name = "A", manaCost = "{U}", typeLine = null, oracleText = null
        )
        manager.addOrUpdateCard(
            cardId = "card-2", name = "B", manaCost = "{R}", typeLine = null, oracleText = null
        )
        
        val entries = manager.getAllEntries()
        
        assertEquals(3, entries.size, "Should have initialization + 2 cards")
    }

    @Test
    fun `cache is thread-safe under concurrent access`() {
        import java.util.concurrent.CountDownLatch
        import java.util.concurrent.ExecutorService
        import java.util.concurrent.Executors
        
        val manager = CardTextCache("test-session-11", maxEntries = 100)
        
        val executor = Executors.newFixedThreadPool(10)
        val latch = CountDownLatch(100)
        
        try {
            // Submit many concurrent add operations
            for (i in 0..99) {
                executor.submit {
                    manager.addOrUpdateCard(
                        cardId = "card-$i", 
                        name = "Card $i", 
                        manaCost = null, 
                        typeLine = null, 
                        oracleText = null
                    )
                    latch.countDown()
                }
            }
            
            latch.await(30, java.util.concurrent.TimeUnit.SECONDS)
            
            // Cache should be in consistent state
            val count = manager.getCardCount()
            assertTrue(count <= 101, "Cache size should not exceed limit (init + maxEntries)")
        } finally {
            executor.shutdownNow()
        }
    }
}
