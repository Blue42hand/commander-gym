package org.commandergym.argentum

import com.sun.net.httpserver.HttpServer
import com.wingedsheep.gameserver.ai.AiControllerProvider
import org.springframework.boot.autoconfigure.AutoConfigurations
import org.springframework.boot.test.context.runner.ApplicationContextRunner
import java.net.InetSocketAddress
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNotNull
import kotlin.test.assertNull

class CommanderGymAutoConfigurationTest {
    private val runner = ApplicationContextRunner()
        .withConfiguration(AutoConfigurations.of(CommanderGymAutoConfiguration::class.java))

    @Test
    fun `installing artifact alone leaves keyless engine default untouched`() {
        runner.withPropertyValues("game.ai.mode=engine").run { context ->
            assertNull(context.startupFailure)
            assertEquals(0, context.getBeansOfType(AiControllerProvider::class.java).size)
        }
    }

    @Test
    fun `explicit enabled and legacy default mode both register authenticated provider`() {
        val server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        server.createContext("/v1/controller-profiles") { exchange ->
            val authorized = exchange.requestHeaders.getFirst("Authorization") == "Bearer fixture-token"
            val body = """{"profiles":[]}""".toByteArray()
            exchange.sendResponseHeaders(if (authorized) 200 else 401, body.size.toLong())
            exchange.responseBody.use { it.write(body) }
        }
        server.start()
        try {
            for (selection in listOf(
                arrayOf("game.ai.mode=engine", "commander-gym.enabled=true"),
                arrayOf("game.ai.mode=commander-gym"),
            )) {
                runner.withPropertyValues(
                    *selection,
                    "commander-gym.sidecar.url=http://127.0.0.1:${server.address.port}",
                    "commander-gym.sidecar.token=fixture-token",
                ).run { context ->
                    assertNull(context.startupFailure)
                    assertEquals("commander-gym", context.getBean(AiControllerProvider::class.java).mode)
                }
            }
        } finally {
            server.stop(0)
        }
    }

    @Test
    fun `enabled provider with missing connection configuration fails closed`() {
        runner.withPropertyValues("game.ai.mode=engine", "commander-gym.enabled=true").run { context ->
            assertNotNull(context.startupFailure)
        }
    }
}
