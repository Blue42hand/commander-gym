package org.commandergym.argentum

import com.sun.net.httpserver.HttpServer
import com.wingedsheep.ai.llm.MulliganInfo
import com.wingedsheep.sdk.model.EntityId
import kotlinx.serialization.json.*
import java.net.InetSocketAddress
import java.net.StandardProtocolFamily
import java.net.URI
import java.net.UnixDomainSocketAddress
import java.nio.ByteBuffer
import java.nio.channels.ServerSocketChannel
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.attribute.PosixFilePermissions
import java.time.Duration
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFails
import kotlin.test.assertTrue

class RecorderCallbackClientTest {
    @Test
    fun `native registration precedes policy HTTP and close follows receipt`() {
        val directory = Files.createTempDirectory(Path.of("/tmp").toRealPath(), "rc-").toRealPath()
        val path = directory.resolve("native.sock")
        val listener = ServerSocketChannel.open(StandardProtocolFamily.UNIX)
        listener.bind(UnixDomainSocketAddress.of(path))
        Files.setPosixFilePermissions(path, PosixFilePermissions.fromString("rw-------"))
        val events = CopyOnWriteArrayList<String>()
        val ids = CopyOnWriteArrayList<String>()
        val worker = Executors.newSingleThreadExecutor()
        val future = worker.submit {
            repeat(2) {
                listener.accept().use { connection ->
                    val buffer = ByteBuffer.allocate(65536)
                    while (true) {
                        assertTrue(connection.read(buffer) > 0)
                        val data = buffer.array().copyOf(buffer.position())
                        if (data.last() == 10.toByte()) {
                            val request = Json.parseToJsonElement(data.toString(Charsets.UTF_8)).jsonObject
                            events.add(request.getValue("op").jsonPrimitive.content)
                            ids.add(request.getValue("callbackId").jsonPrimitive.content)
                            if (request["op"]?.jsonPrimitive?.content == "register") {
                                val masked = request.getValue("request").jsonObject
                                assertEquals("seat-a", masked.getValue("playerId").jsonPrimitive.content)
                                assertTrue("snapshot" !in masked && "token" !in masked)
                            }
                            val response = buildJsonObject {
                                put("protocol", 1); put("ok", true)
                                put("callbackId", request.getValue("callbackId")); put("receiptSha256", "a".repeat(64))
                            }
                            connection.write(ByteBuffer.wrap((response.toString() + "\n").toByteArray()))
                            break
                        }
                    }
                }
            }
        }
        val http = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        http.createContext("/v1/decide-mulligan") { exchange ->
            events.add("http")
            assertEquals(ids.first(), exchange.requestHeaders.getFirst("X-Commander-Gym-Callback-Id"))
            val bytes = "{\"keep\":true}".toByteArray()
            exchange.sendResponseHeaders(200, bytes.size.toLong())
            exchange.responseBody.use { it.write(bytes) }
        }
        http.start()
        try {
            val controller = CommanderGymPlayerController(EntityId("seat-a"),
                URI.create("http://127.0.0.1:${http.address.port}"), "literal-fake-bearer", Duration.ofSeconds(5),
                profileId="binding-a", gameSessionId="game-a", manualHumanGame=true,
                recorder=RecorderCallbackClient(path))
            assertTrue(controller.decideMulligan(MulliganInfo(emptyList(), 0, 0)))
            future.get(5, TimeUnit.SECONDS)
            assertEquals(listOf("register", "http", "closed"), events.toList())
            assertEquals(1, ids.toSet().size)
        } finally {
            http.stop(0); listener.close(); worker.shutdownNow()
            assertTrue(worker.awaitTermination(2, TimeUnit.SECONDS))
            Files.deleteIfExists(path); Files.delete(directory)
        }
    }

    @Test
    fun `recorder failure prevents HTTP and has bounded shutdown`() {
        val directory = Files.createTempDirectory(Path.of("/tmp").toRealPath(), "rc-").toRealPath()
        val path = directory.resolve("native.sock")
        val listener = ServerSocketChannel.open(StandardProtocolFamily.UNIX)
        listener.bind(UnixDomainSocketAddress.of(path))
        Files.setPosixFilePermissions(path, PosixFilePermissions.fromString("rw-------"))
        val worker = Executors.newSingleThreadExecutor()
        val future = worker.submit {
            listener.accept().use { channel ->
                val buffer = ByteBuffer.allocate(4096)
                while (channel.read(buffer) >= 0) { buffer.clear() } // no acknowledgment
            }
        }
        val http = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        var calls = 0
        http.createContext("/v1/decide-mulligan") { exchange -> calls++; exchange.close() }
        http.start()
        try {
            val controller = CommanderGymPlayerController(EntityId("seat-a"),
                URI.create("http://127.0.0.1:${http.address.port}"), "literal-fake-bearer", Duration.ofSeconds(5),
                profileId="binding-a", gameSessionId="game-a", manualHumanGame=true,
                recorder=RecorderCallbackClient(path, Duration.ofMillis(100)))
            val before = System.nanoTime()
            assertFails { controller.decideMulligan(MulliganInfo(emptyList(), 0, 0)) }
            assertTrue((System.nanoTime() - before) / 1_000_000 < 2500)
            assertEquals(0, calls)
            future.get(2, TimeUnit.SECONDS)
            Files.setPosixFilePermissions(path, PosixFilePermissions.fromString("rw-rw----"))
            assertFails { RecorderCallbackClient(path).exchange(buildJsonObject { put("callbackId", "fake") }) }
        } finally {
            http.stop(0); listener.close(); worker.shutdownNow()
            assertTrue(worker.awaitTermination(2, TimeUnit.SECONDS))
            Files.deleteIfExists(path); Files.delete(directory)
        }
    }
}
