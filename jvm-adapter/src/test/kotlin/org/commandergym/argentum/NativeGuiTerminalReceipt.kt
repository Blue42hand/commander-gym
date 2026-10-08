package org.commandergym.argentum

import ch.qos.logback.classic.Logger
import ch.qos.logback.classic.spi.ILoggingEvent
import ch.qos.logback.core.AppenderBase
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.slf4j.LoggerFactory
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardCopyOption
import java.nio.file.attribute.PosixFilePermissions

/** Local supervisor bridge for the native notification that never enters a policy callback.
 * Match the logger and unformatted template, never arbitrary text in a private observation.
 * One native notification suffices; the other two AI seats may already be disconnected.
 */
internal class NativeGuiTerminalReceipt(private val path: Path) : AppenderBase<ILoggingEvent>(), AutoCloseable {
    private val nativeLogger = LoggerFactory.getLogger(NATIVE_LOGGER) as Logger
    private var written = false

    init {
        require(path.isAbsolute && !Files.exists(path)) { "fresh absolute terminal receipt required" }
        context = nativeLogger.loggerContext
        start()
        nativeLogger.addAppender(this)
    }

    @Synchronized
    override fun append(event: ILoggingEvent) {
        if (written || event.loggerName != NATIVE_LOGGER || event.message != "AI game over. Winner: {}") return
        val arguments = event.argumentArray ?: return
        if (arguments.size != 1) return
        val winner = arguments[0]?.toString()
        val receipt = buildJsonObject {
            put("schemaVersion", 1)
            put("source", "native_ai_websocket_game_over")
            put("winnerId", winner)
        }.toString()
        val temporary = Files.createTempFile(path.parent, ".native-terminal-", ".tmp",
            PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------")))
        try {
            Files.writeString(temporary, receipt)
            Files.move(temporary, path, StandardCopyOption.ATOMIC_MOVE)
            written = true
        } finally {
            Files.deleteIfExists(temporary)
        }
    }

    override fun close() {
        nativeLogger.detachAppender(this)
        stop()
    }

    companion object {
        const val NATIVE_LOGGER = "com.wingedsheep.gameserver.ai.AiWebSocketSession"
    }
}
