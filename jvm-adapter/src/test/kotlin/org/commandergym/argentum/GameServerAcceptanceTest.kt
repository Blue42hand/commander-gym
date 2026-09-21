package org.commandergym.argentum

import com.wingedsheep.engine.core.engineSerializersModule
import com.wingedsheep.engine.view.ClientGameState
import com.wingedsheep.engine.view.LegalActionInfo
import com.wingedsheep.gameserver.GameServerApplication
import com.wingedsheep.gameserver.ai.AiControllerProvider
import com.wingedsheep.gameserver.lobby.AiDeckSpec
import com.wingedsheep.gameserver.protocol.ClientMessage
import com.wingedsheep.gameserver.protocol.ServerMessage
import com.wingedsheep.sdk.core.DeckFormat
import com.wingedsheep.sdk.core.Zone
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.jupiter.api.AfterAll
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertNotNull
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.BeforeAll
import org.junit.jupiter.api.MethodOrderer
import org.junit.jupiter.api.Order
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.TestInstance
import org.junit.jupiter.api.TestMethodOrder
import org.springframework.boot.builder.SpringApplicationBuilder
import org.springframework.context.ConfigurableApplicationContext
import java.io.File
import java.net.ServerSocket
import java.net.Socket
import java.net.URI
import java.net.http.HttpClient
import java.net.http.WebSocket
import java.nio.file.Files
import java.nio.file.Path
import java.time.Duration
import java.util.concurrent.CompletionStage
import java.util.concurrent.CopyOnWriteArrayList

@TestInstance(TestInstance.Lifecycle.PER_CLASS)
@TestMethodOrder(MethodOrderer.OrderAnnotation::class)
class GameServerAcceptanceTest {
    private val token = "commander-gym-acceptance-token"
    private val json = Json {
        ignoreUnknownKeys = true
        encodeDefaults = true
        classDiscriminator = "type"
        serializersModule = engineSerializersModule
    }

    private lateinit var repoRoot: File
    private lateinit var modeFile: Path
    private lateinit var evidenceFile: Path
    private lateinit var sidecar: Process
    private lateinit var context: ConfigurableApplicationContext
    private var sidecarPort: Int = 0
    private var serverPort: Int = 0

    @BeforeAll
    fun startAcceptanceStack() {
        repoRoot = findRepoRoot()
        modeFile = Files.createTempFile("commander-gym-acceptance-mode", ".txt")
        evidenceFile = Files.createTempFile("commander-gym-acceptance-evidence", ".jsonl")
        sidecarPort = freePort()
        serverPort = freePort()

        val python = System.getenv("PYTHON")?.takeIf { it.isNotBlank() } ?: "python3"
        val script = File(repoRoot, "scripts/game_server_acceptance_sidecar.py")
        check(script.isFile) { "Acceptance sidecar script not found: " + script }

        val builder = ProcessBuilder(
            python,
            script.absolutePath,
            "--port",
            sidecarPort.toString(),
            "--token",
            token,
            "--mode-file",
            modeFile.toAbsolutePath().toString(),
            "--evidence",
            evidenceFile.toAbsolutePath().toString(),
        )
            .directory(repoRoot)
            .redirectErrorStream(true)
            .redirectOutput(ProcessBuilder.Redirect.INHERIT)
        val environment = builder.environment()
        val existingPythonPath = environment["PYTHONPATH"]
        environment["PYTHONPATH"] = if (existingPythonPath.isNullOrBlank()) {
            repoRoot.absolutePath
        } else {
            repoRoot.absolutePath + File.pathSeparator + existingPythonPath
        }
        sidecar = builder.start()
        waitForPort(sidecarPort, Duration.ofSeconds(10))

        context = SpringApplicationBuilder(GameServerApplication::class.java)
            .run(
                "--server.port=" + serverPort,
                "--spring.main.banner-mode=off",
                "--game.ai.enabled=true",
                "--game.ai.mode=commander-gym",
                "--game.ai.thinking-delay-ms=0",
                "--commander-gym.sidecar.url=http://127.0.0.1:" + sidecarPort,
                "--commander-gym.sidecar.token=" + token,
                "--commander-gym.sidecar.timeout-ms=2000",
                "--logging.level.com.wingedsheep.gameserver=INFO",
                "--logging.level.org.springframework.web.socket=WARN",
            )

        waitForPort(serverPort, Duration.ofSeconds(20))
    }

    @AfterAll
    fun stopAcceptanceStack() {
        if (::context.isInitialized) {
            context.close()
        }
        if (::sidecar.isInitialized) {
            sidecar.destroy()
            if (!sidecar.waitFor(3, java.util.concurrent.TimeUnit.SECONDS)) {
                sidecar.destroyForcibly()
            }
        }
        if (::modeFile.isInitialized) Files.deleteIfExists(modeFile)
        if (::evidenceFile.isInitialized) Files.deleteIfExists(evidenceFile)
    }

    @Test
    @Order(1)
    fun normalGameServerPathUsesCommanderGymProviderAndHumanObservesAction() {
        val providerModes = context.getBeansOfType(AiControllerProvider::class.java)
            .values
            .map { it.mode }
        assertTrue(
            providerModes.any { it.equals("commander-gym", ignoreCase = true) },
            "Commander Gym provider was not registered in vanilla Argentum: " + providerModes,
        )

        setMode("normal")
        val evidenceOffset = evidenceLines().size
        val client = startCommanderGame("Acceptance Normal")
        try {
            val seatId = awaitSeatCreated(evidenceOffset)
            awaitEvidence(evidenceOffset, Duration.ofSeconds(20)) { line ->
                evidenceField(line, "playerId") == seatId &&
                    evidenceField(line, "callback") == "decideMulligan"
            }

            driveHumanUntil(client, Duration.ofSeconds(60)) {
                evidenceLines().drop(evidenceOffset).any { line ->
                    evidenceField(line, "playerId") == seatId &&
                        evidenceField(line, "callback") == "chooseAction" &&
                        evidenceField(line, "mode") == "normal" &&
                        evidenceField(line, "actionType") == "PlayLand"
                }
            }

            val fullStatesBefore = client.fullStateCount()
            client.send(ClientMessage.RequestResync)
            await(Duration.ofSeconds(10), "human resync after AI action") {
                client.fullStateCount() > fullStatesBefore
            }
            val state = client.latestFullState()
            assertNotNull(state, "human client never received a full game state")
            assertTrue(
                state!!.zones.any { it.zoneId.zoneType == Zone.BATTLEFIELD && it.size > 0 },
                "human client did not observe the AI's land on the normal game state",
            )

            val seatEvidence = evidenceLines().drop(evidenceOffset).filter { it.contains(seatId) }
            assertTrue(
                seatEvidence.any { evidenceField(it, "event") == "provenance" && evidenceField(it, "callback") == "chooseAction" },
                "normal action did not produce Commander Gym provenance",
            )
            assertFalse(
                seatEvidence.any { it.contains("\"snapshot\"") },
                "trusted runtime snapshot leaked into sidecar evidence",
            )
            assertTrue(client.errors().isEmpty(), "human client received errors: " + client.errors())
        } finally {
            client.close()
        }
    }

    @Test
    @Order(2)
    fun staleInvalidAndProviderFailuresRemainFailClosed() {
        for (mode in listOf("stale", "invalid", "provider-failure")) {
            setMode(mode)
            val evidenceOffset = evidenceLines().size
            val client = startCommanderGame("Acceptance " + mode)
            try {
                val seatId = awaitSeatCreated(evidenceOffset)
                driveHumanUntil(client, Duration.ofSeconds(30)) {
                    evidenceLines().drop(evidenceOffset).any { line ->
                        evidenceField(line, "playerId") == seatId &&
                            evidenceField(line, "callback") == "chooseAction" &&
                            evidenceField(line, "mode") == mode
                    }
                }

                // Allow the callback exception / malformed response to propagate through the
                // virtual AI session, then prove the game does not advance via a fallback choice.
                Thread.sleep(500)
                val settledStateCount = client.stateMessageCount()
                Thread.sleep(1200)
                assertEquals(
                    settledStateCount,
                    client.stateMessageCount(),
                    "game state advanced after " + mode + " instead of failing closed",
                )

                val modeEvidence = evidenceLines().drop(evidenceOffset).filter { it.contains(seatId) }
                assertFalse(
                    modeEvidence.any {
                        evidenceField(it, "mode") == mode &&
                            evidenceField(it, "actionType") == "PlayLand"
                    },
                    "failure mode " + mode + " silently produced a strategic fallback action",
                )
            } finally {
                client.close()
            }
        }
    }

    private fun startCommanderGame(name: String): HumanClient {
        val client = HumanClient(URI.create("ws://127.0.0.1:" + serverPort + "/game"), json)
        client.connect()
        client.send(ClientMessage.Connect(name))
        await(Duration.ofSeconds(10), name + " connect") {
            client.messages.any { it is ServerMessage.Connected }
        }

        val library = mapOf("Plains" to 99)
        val commander = "Zetalpa, Primal Dawn"
        client.send(ClientMessage.CreateQuickGameLobby(vsAi = true, format = DeckFormat.COMMANDER))
        await(Duration.ofSeconds(10), name + " quick-game lobby") {
            client.messages.any { it is ServerMessage.QuickGameLobbyState }
        }
        client.send(
            ClientMessage.SetQuickGameAiDeck(
                AiDeckSpec.Fixed(
                    deckList = library,
                    label = "Acceptance Zetalpa",
                    commander = commander,
                )
            )
        )
        client.send(
            ClientMessage.SubmitQuickGameLobbyDeck(
                deckList = library,
                commander = commander,
            )
        )
        client.send(ClientMessage.SetQuickGameLobbyReady(true))

        await(Duration.ofSeconds(30), name + " game creation") {
            client.messages.any { it is ServerMessage.GameCreated }
        }
        await(Duration.ofSeconds(30), name + " human mulligan") {
            client.messages.any { it is ServerMessage.MulliganDecision }
        }
        client.send(ClientMessage.KeepHand)
        await(Duration.ofSeconds(30), name + " mulligan completion") {
            client.messages.any { it is ServerMessage.MulliganComplete }
        }
        await(Duration.ofSeconds(30), name + " initial game state") {
            client.stateMessageCount() > 0
        }
        assertTrue(client.errors().isEmpty(), name + " errors before play: " + client.errors())
        return client
    }

    private fun driveHumanUntil(
        client: HumanClient,
        timeout: Duration,
        complete: () -> Boolean,
    ) {
        val deadline = System.nanoTime() + timeout.toNanos()
        var lastPassSignature: String? = null
        while (System.nanoTime() < deadline) {
            if (complete()) return
            val window = client.latestActionWindow()
            if (window != null) {
                val pass = window.actions.firstOrNull { it.actionType == "PassPriority" }
                if (pass != null) {
                    val signature = client.stateMessageCount().toString() + ":" +
                        window.interactionEpoch + ":" + pass.action.toString()
                    if (signature != lastPassSignature) {
                        client.send(
                            ClientMessage.SubmitAction(
                                pass.action,
                                interactionEpoch = window.interactionEpoch,
                            )
                        )
                        lastPassSignature = signature
                    }
                }
            }
            Thread.sleep(40)
        }
        throw AssertionError(
            "Timed out driving human seat; errors=" + client.errors() +
                "; recent evidence=" + evidenceLines().takeLast(12)
        )
    }

    private fun setMode(mode: String) {
        Files.writeString(modeFile, mode + "\n")
    }

    private fun awaitSeatCreated(offset: Int): String {
        var seat: String? = null
        await(Duration.ofSeconds(20), "Commander Gym seat creation") {
            seat = evidenceLines().drop(offset)
                .firstNotNullOfOrNull { line ->
                    if (evidenceField(line, "event") == "seat_created") {
                        evidenceField(line, "playerId")
                    } else {
                        null
                    }
                }
            seat != null
        }
        return seat!!
    }

    private fun awaitEvidence(offset: Int, timeout: Duration, predicate: (String) -> Boolean) {
        await(timeout, "sidecar evidence") {
            evidenceLines().drop(offset).any(predicate)
        }
    }

    private fun evidenceLines(): List<String> =
        if (!Files.exists(evidenceFile)) emptyList() else Files.readAllLines(evidenceFile)

    private fun evidenceField(line: String, name: String): String? =
        runCatching {
            json.parseToJsonElement(line).jsonObject[name]?.jsonPrimitive?.contentOrNull
        }.getOrNull()

    private fun await(timeout: Duration, description: String, predicate: () -> Boolean) {
        val deadline = System.nanoTime() + timeout.toNanos()
        while (System.nanoTime() < deadline) {
            if (predicate()) return
            Thread.sleep(40)
        }
        throw AssertionError("Timed out waiting for " + description)
    }

    private fun waitForPort(port: Int, timeout: Duration) {
        await(timeout, "port " + port) {
            runCatching {
                Socket("127.0.0.1", port).use { }
                true
            }.getOrDefault(false)
        }
    }

    private fun freePort(): Int = ServerSocket(0).use { it.localPort }

    private fun findRepoRoot(): File {
        var current = File(System.getProperty("user.dir")).absoluteFile
        while (true) {
            if (
                File(current, "commander_gym").isDirectory &&
                File(current, "scripts").isDirectory &&
                File(current, "jvm-adapter").isDirectory
            ) {
                return current
            }
            current = current.parentFile ?: break
        }
        error("Could not locate commander-gym repo root from " + System.getProperty("user.dir"))
    }

    private data class ActionWindow(
        val actions: List<LegalActionInfo>,
        val interactionEpoch: String?,
    )

    private class HumanClient(
        private val uri: URI,
        private val json: Json,
    ) : WebSocket.Listener {
        val messages = CopyOnWriteArrayList<ServerMessage>()
        private val partialText = StringBuilder()
        @Volatile
        private var transportError: Throwable? = null
        private lateinit var webSocket: WebSocket

        fun connect() {
            webSocket = HttpClient.newHttpClient()
                .newWebSocketBuilder()
                .connectTimeout(Duration.ofSeconds(10))
                .buildAsync(uri, this)
                .join()
        }

        fun send(message: ClientMessage) {
            val error = transportError
            check(error == null) { "WebSocket transport failed: " + error }
            webSocket.sendText(json.encodeToString(message), true).join()
        }

        fun close() {
            if (::webSocket.isInitialized) {
                runCatching { webSocket.sendClose(WebSocket.NORMAL_CLOSURE, "done").join() }
            }
        }

        fun stateMessageCount(): Int = messages.count {
            it is ServerMessage.StateUpdate || it is ServerMessage.StateDeltaUpdate
        }

        fun fullStateCount(): Int = messages.count { it is ServerMessage.StateUpdate }

        fun latestFullState(): ClientGameState? =
            messages.filterIsInstance<ServerMessage.StateUpdate>().lastOrNull()?.state

        fun latestActionWindow(): ActionWindow? {
            val message = messages.asReversed().firstOrNull {
                it is ServerMessage.StateUpdate || it is ServerMessage.StateDeltaUpdate
            } ?: return null
            return when (message) {
                is ServerMessage.StateUpdate -> ActionWindow(message.legalActions, message.interactionEpoch)
                is ServerMessage.StateDeltaUpdate -> ActionWindow(message.legalActions, message.interactionEpoch)
                else -> null
            }
        }

        fun errors(): List<ServerMessage.Error> = messages.filterIsInstance<ServerMessage.Error>()

        override fun onOpen(webSocket: WebSocket) {
            webSocket.request(1)
        }

        override fun onText(
            webSocket: WebSocket,
            data: CharSequence,
            last: Boolean,
        ): CompletionStage<*>? {
            synchronized(partialText) {
                partialText.append(data)
                if (last) {
                    val payload = partialText.toString()
                    partialText.setLength(0)
                    runCatching {
                        json.decodeFromString<ServerMessage>(payload)
                    }.onSuccess(messages::add)
                }
            }
            webSocket.request(1)
            return null
        }

        override fun onError(webSocket: WebSocket, error: Throwable) {
            transportError = error
        }
    }
}
