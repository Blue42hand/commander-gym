package org.commandergym.argentum

import com.wingedsheep.ai.llm.MulliganInfo
import com.wingedsheep.engine.core.engineSerializersModule
import com.wingedsheep.gameserver.GameServerApplication
import com.wingedsheep.gameserver.ai.AiControllerContext
import com.wingedsheep.gameserver.ai.AiControllerProvider
import com.wingedsheep.gameserver.ai.AiControllerSpec
import com.wingedsheep.gameserver.lobby.AiDeckSpec
import com.wingedsheep.gameserver.protocol.AiControllerCatalog
import com.wingedsheep.gameserver.protocol.ClientMessage
import com.wingedsheep.gameserver.protocol.GetAiControllerCatalog
import com.wingedsheep.gameserver.protocol.ServerMessage
import com.wingedsheep.gameserver.protocol.SetQuickGameAiController
import com.wingedsheep.sdk.core.DeckFormat
import com.wingedsheep.sdk.model.EntityId
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.jupiter.api.AfterAll
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.BeforeAll
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.TestInstance
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
class GameServerBindingFoundationAcceptanceTest {
    private val token = "commander-gym-binding-foundation-token"
    private val realInstanceRoot = System.getenv("COMMANDER_GYM_QUALIFICATION_INSTANCE_ROOT")
        ?.takeIf { it.isNotBlank() }
    private val expectedBindingId = if (realInstanceRoot == null) "seat-a" else "krenko-foundation-openai"
    private val expectedSpec = AiControllerSpec("commander-gym", expectedBindingId)
    private val expectedDeckLabel = if (realInstanceRoot == null) "Synthetic Zetalpa" else "Krenko — Argentum Native"
    private val expectedPilotId = if (realInstanceRoot == null) "synthetic-binding-pilot" else "foundation-openai"
    private val expectedPilotRevision = if (realInstanceRoot == null) "r1" else "2026-09-24.1"
    private val expectedBindingRevision = if (realInstanceRoot == null) "r1" else "2026-10-02.1"
    private val json = Json {
        ignoreUnknownKeys = true
        encodeDefaults = true
        classDiscriminator = "type"
        serializersModule = engineSerializersModule
    }

    private lateinit var repoRoot: File
    private lateinit var instanceRoot: Path
    private lateinit var evidenceFile: Path
    private lateinit var sidecar: Process
    private lateinit var context: ConfigurableApplicationContext
    private var sidecarPort: Int = 0
    private var serverPort: Int = 0

    @BeforeAll
    fun startBindingFoundationStack() {
        repoRoot = findRepoRoot()
        instanceRoot = Files.createTempDirectory("commander-gym-binding-foundation")
        evidenceFile = instanceRoot.resolve("binding-foundation-evidence.jsonl")
        sidecarPort = freePort()
        serverPort = freePort()

        val python = System.getenv("PYTHON")?.takeIf { it.isNotBlank() } ?: "python3"
        val script = File(repoRoot, "scripts/game_server_binding_acceptance_sidecar.py")
        check(script.isFile) { "Binding acceptance sidecar script not found: $script" }

        val args = mutableListOf(
            python, script.absolutePath,
            "--port", sidecarPort.toString(),
            "--token", token,
            "--root", instanceRoot.toAbsolutePath().toString(),
            "--evidence", evidenceFile.toAbsolutePath().toString(),
        )
        if (realInstanceRoot != null) {
            args += listOf(
                "--catalog", "$realInstanceRoot/rosters/foundation-active-four-seat.json",
                "--instance-root", realInstanceRoot,
            )
        }
        val builder = ProcessBuilder(args)
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
                "--server.port=$serverPort",
                "--spring.main.banner-mode=off",
                "--game.ai.enabled=true",
                "--game.ai.mode=commander-gym",
                "--game.ai.thinking-delay-ms=0",
                "--commander-gym.sidecar.url=http://127.0.0.1:$sidecarPort",
                "--commander-gym.sidecar.token=$token",
                "--commander-gym.sidecar.timeout-ms=2000",
                "--logging.level.com.wingedsheep.gameserver=INFO",
                "--logging.level.org.springframework.web.socket=WARN",
            )
        waitForPort(serverPort, Duration.ofSeconds(20))
    }

    @AfterAll
    fun stopBindingFoundationStack() {
        if (::context.isInitialized) context.close()
        if (::sidecar.isInitialized) {
            sidecar.destroy()
            if (!sidecar.waitFor(3, java.util.concurrent.TimeUnit.SECONDS)) {
                sidecar.destroyForcibly()
            }
        }
        if (::instanceRoot.isInitialized) {
            instanceRoot.toFile().deleteRecursively()
        }
    }

    @Test
    fun selectedBindingConfiguresExactDeckAndPilotThroughNormalArgentumProviderSeam() {
        val client = HumanClient(URI.create("ws://127.0.0.1:$serverPort/game"), json)
        client.connect()
        try {
            client.send(ClientMessage.Connect("Binding Foundation Host"))
            await(Duration.ofSeconds(10), "human connection") {
                client.messages.any { it is ServerMessage.Connected }
            }

            client.send(GetAiControllerCatalog())
            val preLobbyCatalog = awaitCatalog(client, lobbyId = null)
            val advertised = preLobbyCatalog.options.single { it.spec == expectedSpec }
            assertEquals(if (realInstanceRoot == null) "Synthetic Binding A" else "Krenko — Argentum Native — Foundation OpenAI", advertised.displayName)
            assertEquals(expectedDeckLabel, advertised.deck?.label)
            if (realInstanceRoot != null) {
                assertEquals(4, preLobbyCatalog.options.count { it.spec.profileId?.endsWith("-foundation-openai") == true })
            }

            client.send(
                ClientMessage.CreateQuickGameLobby(
                    vsAi = true,
                    format = DeckFormat.COMMANDER,
                    aiControllerSpec = expectedSpec,
                )
            )
            val lobbyState = awaitQuickGameState(client) { it.aiDeck?.label == expectedDeckLabel }
            val lobbyId = lobbyState.lobbyId

            client.send(GetAiControllerCatalog(lobbyId))
            val selectedCatalog = awaitCatalog(client, lobbyId)
            assertEquals(expectedSpec, selectedCatalog.seats.single().spec)

            val errorsBeforeStale = client.errors().size
            client.send(SetQuickGameAiController(AiControllerSpec("commander-gym", "missing-binding")))
            await(Duration.ofSeconds(10), "stale Binding rejection") {
                client.errors().drop(errorsBeforeStale).any {
                    it.message.contains("Unknown AI controller profile")
                }
            }
            client.send(GetAiControllerCatalog(lobbyId))
            assertEquals(expectedSpec, awaitCatalog(client, lobbyId).seats.single().spec)

            if (realInstanceRoot != null) {
                val talrandSpec = AiControllerSpec("commander-gym", "talrand-foundation-openai")
                client.send(SetQuickGameAiController(talrandSpec))
                awaitQuickGameState(client) { it.aiDeck?.label == "Talrand — Argentum Native" }
                client.send(GetAiControllerCatalog(lobbyId))
                assertEquals(talrandSpec, awaitSelectedCatalog(client, lobbyId, talrandSpec).seats.single().spec)
                client.send(SetQuickGameAiController(expectedSpec))
                awaitQuickGameState(client) { it.aiDeck?.label == expectedDeckLabel }
                client.send(GetAiControllerCatalog(lobbyId))
                assertEquals(expectedSpec, awaitSelectedCatalog(client, lobbyId, expectedSpec).seats.single().spec)
            }

            val errorsBeforeMismatch = client.errors().size
            client.send(
                ClientMessage.SetQuickGameAiDeck(
                    AiDeckSpec.Fixed(
                        deckList = mapOf("Plains" to 98, "Mountain" to 1),
                        label = "Mismatched deck",
                        commander = "Zetalpa, Primal Dawn",
                    )
                )
            )
            await(Duration.ofSeconds(10), "provider-owned deck mismatch rejection") {
                client.errors().drop(errorsBeforeMismatch).any {
                    it.message.contains("owns this seat's deck preset")
                }
            }
            assertEquals(
                expectedDeckLabel,
                client.messages.filterIsInstance<ServerMessage.QuickGameLobbyState>().last().aiDeck?.label,
            )

            // Exercise the settled generic provider seam directly rather than broadening this
            // foundation smoke into a gameplay lifecycle test. The provider still comes from the
            // running normal Argentum Spring application and calls the production sidecar over HTTP.
            val provider = context.getBeansOfType(AiControllerProvider::class.java).values
                .single { it.mode == "commander-gym" }
            val boundDeck = provider.profiles.single { it.id == expectedBindingId }.deckSpec as AiDeckSpec.Fixed
            val controller = provider.create(
                AiControllerContext(
                    playerId = EntityId.of("binding-foundation-ai"),
                    gameSessionId = "binding-foundation-smoke",
                    profileId = expectedBindingId,
                    snapshot = { null },
                )
            )
            controller.setDeckList(boundDeck.deckList, boundDeck.commander)
            assertEquals(true, controller.decideMulligan(MulliganInfo(emptyList(), 0, 0)))
            val recordingSeat = EntityId.of("binding-foundation-ai")
            val masked = com.wingedsheep.engine.view.ClientGameState(
                viewingPlayerId = recordingSeat, cards = emptyMap(), zones = emptyList(), players = emptyList(),
                currentPhase = com.wingedsheep.sdk.core.Phase.COMBAT,
                currentStep = com.wingedsheep.sdk.core.Step.DECLARE_ATTACKERS,
                activePlayerId = recordingSeat, priorityPlayerId = recordingSeat, turnNumber = 1,
                isGameOver = false, winnerId = null, combat = null,
            )
            val offered = listOf(com.wingedsheep.engine.view.LegalActionInfo(
                actionType = "PassPriority", description = "Synthetic pass",
                action = com.wingedsheep.engine.core.PassPriority(recordingSeat)))
            val decisionEvidence = com.wingedsheep.gameserver.recording.aiSeatEvidence(masked, offered, recordingSeat.value)
            val recorded = (controller as com.wingedsheep.ai.RecordedAiPlayerController)
                .chooseRecordedAction(masked, offered, emptyList(), decisionEvidence)
            assertEquals(offered.single().action, (recorded as com.wingedsheep.ai.ActionResponse.SubmitAction).action)

            val resolved = awaitEvidence(Duration.ofSeconds(10)) {
                it["event"] == "binding_seat_resolved" && it["profileId"] == expectedBindingId
            }
            assertEquals(expectedBindingId, resolved["bindingId"])
            assertEquals(expectedBindingRevision, resolved["bindingRevision"])
            assertEquals(expectedPilotId, resolved["pilotId"])
            assertEquals(expectedPilotRevision, resolved["pilotRevision"])
            if (realInstanceRoot != null) {
                val talrandDeck = provider.profiles.single { it.id == "talrand-foundation-openai" }.deckSpec as AiDeckSpec.Fixed
                val talrand = provider.create(
                    AiControllerContext(
                        playerId = EntityId.of("binding-foundation-talrand-ai"),
                        gameSessionId = "binding-foundation-smoke",
                        profileId = "talrand-foundation-openai",
                        snapshot = { null },
                    )
                )
                talrand.setDeckList(talrandDeck.deckList, talrandDeck.commander)
                assertEquals(true, talrand.decideMulligan(MulliganInfo(emptyList(), 0, 0)))
                val talrandResolved = awaitEvidence(Duration.ofSeconds(10)) {
                    it["event"] == "binding_seat_resolved" && it["profileId"] == "talrand-foundation-openai"
                }
                assertEquals("foundation-openai", talrandResolved["pilotId"])
                assertEquals("2026-09-24.1", talrandResolved["pilotRevision"])
                assertEquals("Krenko, Mob Boss", boundDeck.commander)
                assertEquals("Talrand, Sky Summoner", talrandDeck.commander)
                assertEquals(false, boundDeck.deckList == talrandDeck.deckList)
                assertEquals(64, resolved["bindingFingerprint"]?.length)
                assertEquals(64, talrandResolved["bindingFingerprint"]?.length)
                assertEquals(false, resolved["deckFingerprint"] == talrandResolved["deckFingerprint"])
                assertEquals(resolved["pilotFingerprint"], talrandResolved["pilotFingerprint"])
                println("REAL_BINDING_PROFILE_PROOF " + mapOf(
                    "krenko" to resolved,
                    "talrand" to talrandResolved,
                    "krenkoDeckCards" to boundDeck.deckList.values.sum().toString(),
                    "talrandDeckCards" to talrandDeck.deckList.values.sum().toString(),
                ))
            }
        } finally {
            client.close()
        }
    }

    private fun awaitCatalog(client: HumanClient, lobbyId: String?): AiControllerCatalog {
        var result: AiControllerCatalog? = null
        await(Duration.ofSeconds(10), "AI controller catalog") {
            result = client.messages.filterIsInstance<AiControllerCatalog>()
                .lastOrNull { it.lobbyId == lobbyId }
            result != null
        }
        return result!!
    }

    private fun awaitSelectedCatalog(
        client: HumanClient,
        lobbyId: String,
        expected: AiControllerSpec,
    ): AiControllerCatalog {
        var result: AiControllerCatalog? = null
        await(Duration.ofSeconds(10), "selected AI controller catalog") {
            result = client.messages.filterIsInstance<AiControllerCatalog>()
                .lastOrNull { it.lobbyId == lobbyId && it.seats.singleOrNull()?.spec == expected }
            result != null
        }
        return result!!
    }

    private fun awaitQuickGameState(
        client: HumanClient,
        predicate: (ServerMessage.QuickGameLobbyState) -> Boolean,
    ): ServerMessage.QuickGameLobbyState {
        var result: ServerMessage.QuickGameLobbyState? = null
        await(Duration.ofSeconds(10), "quick-game Binding preset") {
            result = client.messages.filterIsInstance<ServerMessage.QuickGameLobbyState>()
                .lastOrNull(predicate)
            result != null
        }
        return result!!
    }

    private fun awaitEvidence(
        timeout: Duration,
        predicate: (Map<String, String>) -> Boolean,
    ): Map<String, String> {
        var result: Map<String, String>? = null
        await(timeout, "Binding seat resolution evidence") {
            result = evidenceLines().asReversed().firstOrNull(predicate)
            result != null
        }
        return result!!
    }

    private fun evidenceLines(): List<Map<String, String>> =
        if (!Files.exists(evidenceFile)) {
            emptyList()
        } else {
            Files.readAllLines(evidenceFile).mapNotNull { line ->
                runCatching {
                    json.parseToJsonElement(line).jsonObject.mapValues { (_, value) ->
                        value.jsonPrimitive.contentOrNull ?: ""
                    }
                }.getOrNull()
            }
        }

    private fun await(timeout: Duration, description: String, predicate: () -> Boolean) {
        val deadline = System.nanoTime() + timeout.toNanos()
        while (System.nanoTime() < deadline) {
            if (predicate()) return
            Thread.sleep(40)
        }
        val diagnostic = "Timed out waiting for $description; evidence=${evidenceLines().takeLast(8)}"
        println("BINDING_ACCEPTANCE_TIMEOUT $diagnostic")
        throw AssertionError(diagnostic)
    }

    private fun waitForPort(port: Int, timeout: Duration) {
        await(timeout, "port $port") {
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
        error("Could not locate commander-gym repo root from ${System.getProperty("user.dir")}")
    }

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
            check(error == null) { "WebSocket transport failed: $error" }
            webSocket.sendText(json.encodeToString(message), true).join()
        }

        fun close() {
            if (::webSocket.isInitialized) {
                runCatching { webSocket.sendClose(WebSocket.NORMAL_CLOSURE, "done").join() }
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
