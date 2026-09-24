package org.commandergym.argentum

import com.sun.net.httpserver.HttpServer
import com.wingedsheep.gameserver.ai.AiControllerContext
import com.wingedsheep.gameserver.lobby.AiDeckSpec
import com.wingedsheep.sdk.model.EntityId
import java.net.InetSocketAddress
import java.net.URI
import java.nio.charset.StandardCharsets
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertIs

class CommanderGymControllerProviderProfileTest {
    @Test
    fun `advertises opaque Binding profile and validates delivered deck`() {
        val server = profileServer()
        try {
            val provider = CommanderGymControllerProvider(
                URI.create("http://127.0.0.1:${server.address.port}"),
                TOKEN,
            )

            val profile = provider.profiles.single()
            assertEquals("binding-alpha", profile.id)
            assertEquals("Alpha Binding", profile.displayName)
            assertEquals("pilot-alpha v3", profile.description)
            val deck = assertIs<AiDeckSpec.Fixed>(profile.deckSpec)
            assertEquals(mapOf("Forest" to 99), deck.deckList)
            assertEquals("Alpha Deck", deck.label)
            assertEquals("Aeve, Progenitor Ooze", deck.commander)

            val controller = provider.create(
                AiControllerContext(
                    playerId = EntityId("ai-alpha"),
                    gameSessionId = "game-alpha",
                    profileId = "binding-alpha",
                    snapshot = { null },
                )
            )
            controller.setDeckList(mapOf("Forest" to 99))
            assertFailsWith<IllegalArgumentException> {
                controller.setDeckList(mapOf("Forest" to 98, "Mountain" to 1))
            }
        } finally {
            server.stop(0)
        }
    }

    @Test
    fun `unknown explicit Binding profile fails closed`() {
        val server = profileServer()
        try {
            val provider = CommanderGymControllerProvider(
                URI.create("http://127.0.0.1:${server.address.port}"),
                TOKEN,
            )
            assertFailsWith<IllegalArgumentException> {
                provider.create(
                    AiControllerContext(
                        playerId = EntityId("ai-missing"),
                        gameSessionId = null,
                        profileId = "missing-binding",
                        snapshot = { null },
                    )
                )
            }
        } finally {
            server.stop(0)
        }
    }

    private fun profileServer(): HttpServer {
        val server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        server.createContext("/v1/controller-profiles") { exchange ->
            val authorized = exchange.requestHeaders.getFirst("Authorization") == "Bearer $TOKEN"
            val body = if (authorized) PROFILE_BODY else "{\"error\":\"unauthorized\"}"
            val status = if (authorized) 200 else 401
            val bytes = body.toByteArray(StandardCharsets.UTF_8)
            exchange.responseHeaders.add("Content-Type", "application/json")
            exchange.sendResponseHeaders(status, bytes.size.toLong())
            exchange.responseBody.use { it.write(bytes) }
        }
        server.start()
        return server
    }

    private companion object {
        const val TOKEN = "profile-test-token"
        const val PROFILE_BODY = """{"profiles":[{"id":"binding-alpha","displayName":"Alpha Binding","description":"pilot-alpha v3","deck":{"label":"Alpha Deck","cards":{"Forest":99},"commander":"Aeve, Progenitor Ooze"}}]}"""
    }
}
