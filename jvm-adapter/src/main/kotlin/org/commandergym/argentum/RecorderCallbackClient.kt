package org.commandergym.argentum

import kotlinx.serialization.json.*
import jdk.net.ExtendedSocketOptions
import java.net.StandardProtocolFamily
import java.net.UnixDomainSocketAddress
import java.nio.ByteBuffer
import java.nio.channels.SocketChannel
import java.nio.file.Files
import java.nio.file.LinkOption
import java.nio.file.Path
import java.nio.file.attribute.PosixFilePermission
import java.time.Duration
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/** Native identity registers masked callbacks; no pilot, journal or finalizer runs here. */
class RecorderCallbackClient(private val path: Path, private val deadline: Duration = Duration.ofSeconds(3)) {
    private val maxBytes = 4 * 1024 * 1024

    fun exchange(message: JsonObject): String {
        // The 0600 native socket and owner-controlled parent exclude sidecar UID.
        val absolute = path.toAbsolutePath()
        require(!Files.isSymbolicLink(absolute)) { "Recorder socket must not be a symlink" }
        var parent: Path? = absolute.parent
        while (parent != null) {
            require(!Files.isSymbolicLink(parent)) { "Recorder socket parents must not be symlinks" }
            parent = parent.parent
        }
        val permissions = Files.getPosixFilePermissions(absolute, LinkOption.NOFOLLOW_LINKS)
        require(permissions.none { it.name.startsWith("GROUP_") || it.name.startsWith("OTHERS_") }) {
            "Native recorder socket must be private"
        }
        val directoryPermissions = Files.getPosixFilePermissions(absolute.parent, LinkOption.NOFOLLOW_LINKS)
        require(PosixFilePermission.GROUP_WRITE !in directoryPermissions &&
            PosixFilePermission.OTHERS_WRITE !in directoryPermissions) { "Recorder directory must be owner-controlled" }
        require(Files.getOwner(absolute) == Files.getOwner(absolute.parent)) { "Recorder socket custody mismatch" }
        val data = (message.toString() + "\n").toByteArray(Charsets.UTF_8)
        require(data.size <= maxBytes) { "Recorder request exceeds limit" }
        val channel = SocketChannel.open(StandardProtocolFamily.UNIX)
        val executor = Executors.newSingleThreadExecutor { task -> Thread(task, "private-recorder-exchange").apply { isDaemon = true } }
        try {
            return executor.submit<String> {
                channel.connect(UnixDomainSocketAddress.of(absolute))
                require(channel.getOption(ExtendedSocketOptions.SO_PEERCRED).user() == Files.getOwner(absolute)) {
                    "Recorder peer custody mismatch"
                }
                val output = ByteBuffer.wrap(data)
                while (output.hasRemaining()) channel.write(output)
                val buffer = ByteBuffer.allocate(4096)
                while (true) {
                    require(channel.read(buffer) > 0 && buffer.hasRemaining()) { "Invalid recorder response framing" }
                    val bytes = buffer.array().copyOf(buffer.position())
                    if (bytes.contains(10.toByte())) {
                        require(bytes.last() == 10.toByte() && bytes.count { it == 10.toByte() } == 1) { "Invalid recorder response framing" }
                        val response = Json.parseToJsonElement(bytes.toString(Charsets.UTF_8)).jsonObject
                        require(response.keys == setOf("protocol", "ok", "callbackId", "receiptSha256") &&
                            response["protocol"]?.jsonPrimitive?.intOrNull == 1 &&
                            response["ok"]?.jsonPrimitive?.booleanOrNull == true &&
                            response["callbackId"] == message["callbackId"]) { "Recorder did not acknowledge callback" }
                        return@submit response.getValue("receiptSha256").jsonPrimitive.content.also {
                            require(it.matches(Regex("[a-f0-9]{64}"))) { "Invalid recorder receipt" }
                        }
                    }
                }
                @Suppress("UNREACHABLE_CODE") error("unreachable")
            }.get(deadline.toMillis(), TimeUnit.MILLISECONDS)
        } finally {
            channel.close() // closes an outstanding blocked read/connect on timeout or cancellation
            executor.shutdownNow()
            require(executor.awaitTermination(1, TimeUnit.SECONDS)) { "Recorder exchange worker did not stop" }
        }
    }
}
