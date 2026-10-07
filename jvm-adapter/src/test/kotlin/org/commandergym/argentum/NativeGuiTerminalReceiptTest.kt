package org.commandergym.argentum

import ch.qos.logback.classic.Level
import ch.qos.logback.classic.Logger
import com.wingedsheep.ai.AiPlayerController
import com.wingedsheep.engine.core.engineSerializersModule
import com.wingedsheep.gameserver.ai.AiWebSocketSession
import com.wingedsheep.gameserver.protocol.GameOverReason
import com.wingedsheep.gameserver.protocol.ServerMessage
import com.wingedsheep.sdk.model.EntityId
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import org.mockito.Mockito.mock
import org.slf4j.LoggerFactory
import org.springframework.web.socket.TextMessage
import java.nio.file.Files
import java.nio.file.attribute.PosixFilePermissions

class NativeGuiTerminalReceiptTest {
    private val json = Json { serializersModule = engineSerializersModule; classDiscriminator = "type" }

    @Test
    fun nativeThreeAiGameOverWritesOneMinimalReceiptWithoutCallingPolicy() {
        for (disconnected in listOf(false, true)) {
            val root = Files.createTempDirectory("native-gui-terminal")
            val path = root.resolve("native-terminal.json")
            val logger = LoggerFactory.getLogger(NativeGuiTerminalReceipt.NATIVE_LOGGER) as Logger
            val oldLevel = logger.level
            logger.level = Level.INFO
            val sessions = (0..2).map { index ->
                AiWebSocketSession(EntityId("ai-$index"), mock(AiPlayerController::class.java),
                    thinkingDelayMs = 0, onActionReady = { _, _, _ -> fail("policy action") },
                    onMulliganKeep = { fail("mulligan") }, onMulliganTake = { fail("mulligan") },
                    onBottomCards = { _, _ -> fail("bottom cards") })
            }
            try {
                NativeGuiTerminalReceipt(path).use {
                    logger.info("unrelated private text AI game over. Winner: ai-0")
                    assertFalse(Files.exists(path))
                    if (disconnected) sessions.drop(1).forEach { it.shutdown() }
                    val message = TextMessage(json.encodeToString<ServerMessage>(ServerMessage.GameOver(
                        EntityId("ai-0"), GameOverReason.LIFE_ZERO, gameId = "offline-human-three-ai")))
                    sessions.forEach { it.sendMessage(message) }
                    val deadline = System.nanoTime() + 5_000_000_000L
                    while (sessions.any { it.isOpen } && System.nanoTime() < deadline) Thread.sleep(10)
                    assertTrue(sessions.none { it.isOpen })
                    val first = Files.readString(path)
                    sessions.forEach { it.sendMessage(message) }
                    logger.info("AI game over. Winner: {}", EntityId("different-duplicate"))
                    assertEquals(first, Files.readString(path))
                    val row = json.parseToJsonElement(first).jsonObject
                    assertEquals(setOf("schemaVersion", "source", "winnerId"), row.keys)
                    assertEquals("ai-0", row.getValue("winnerId").jsonPrimitive.content)
                    assertEquals(PosixFilePermissions.fromString("rw-------"), Files.getPosixFilePermissions(path))
                }
            } finally {
                sessions.forEach { it.shutdown() }
                logger.level = oldLevel
                Files.deleteIfExists(path)
                Files.delete(root)
            }
        }
    }
}
