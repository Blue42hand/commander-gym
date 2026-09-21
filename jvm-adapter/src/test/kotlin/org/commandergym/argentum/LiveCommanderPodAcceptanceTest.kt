package org.commandergym.argentum

import com.wingedsheep.engine.core.GameAction
import com.wingedsheep.engine.core.engineSerializersModule
import com.wingedsheep.engine.view.ClientGameState
import com.wingedsheep.engine.view.LegalActionInfo
import com.wingedsheep.gameserver.GameServerApplication
import com.wingedsheep.gameserver.lobby.AiDeckSpec
import com.wingedsheep.gameserver.protocol.ClientMessage
import com.wingedsheep.gameserver.protocol.ServerMessage
import com.wingedsheep.sdk.core.Zone
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.jupiter.api.AfterAll
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.BeforeAll
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.TestInstance
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable
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

/**
 * Paid, live acceptance proof for the actual human-play topology:
 *
 * one normal WebSocket human client + three normal Argentum AI seats
 * -> Commander Gym game-server provider -> production sidecar -> gpt-5.6-luna.
 *
 * Normal CI compiles this class but never calls OpenAI. Run it explicitly with
 * COMMANDER_GYM_LIVE_OPENAI_ACCEPTANCE=1 and OPENAI_API_KEY set.
 */
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
@EnabledIfEnvironmentVariable(named = "COMMANDER_GYM_LIVE_OPENAI_ACCEPTANCE", matches = "1")
class LiveCommanderPodAcceptanceTest {
    private val token = "commander-gym-live-pod-token"
    private val model = System.getenv("COMMANDER_GYM_OPENAI_MODEL")
        ?.takeIf { it.isNotBlank() }
        ?: "gpt-5.6-luna"
    private val timeout = Duration.ofSeconds(
        System.getenv("COMMANDER_GYM_LIVE_POD_TIMEOUT_SECONDS")
            ?.toLongOrNull()
            ?.coerceAtLeast(60)
            ?: 900
    )
    private val json = Json {
        ignoreUnknownKeys = true
        encodeDefaults = true
        classDiscriminator = "type"
        serializersModule = engineSerializersModule
    }

    private lateinit var repoRoot: File
    private lateinit var evidenceFile: Path
    private lateinit var sidecar: Process
    private lateinit var context: ConfigurableApplicationContext
    private var sidecarPort: Int = 0
    private var serverPort: Int = 0

    @BeforeAll
    fun startLiveStack() {
        check(!System.getenv("OPENAI_API_KEY").isNullOrBlank()) {
            "OPENAI_API_KEY is required when COMMANDER_GYM_LIVE_OPENAI_ACCEPTANCE=1"
        }
        repoRoot = findRepoRoot()
        verifyOpenAiSdk()
        evidenceFile = Files.createTempFile("commander-gym-live-pod", ".jsonl")
        sidecarPort = freePort()
        serverPort = freePort()

        val python = System.getenv("PYTHON")?.takeIf { it.isNotBlank() } ?: "python3"
        val builder = ProcessBuilder(python, "-m", "commander_gym.game_server_openai_sidecar")
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
        environment["COMMANDER_GYM_SIDECAR_TOKEN"] = token
        environment["COMMANDER_GYM_SIDECAR_HOST"] = "127.0.0.1"
        environment["COMMANDER_GYM_SIDECAR_PORT"] = sidecarPort.toString()
        environment["COMMANDER_GYM_OPENAI_MODEL"] = model
        environment["COMMANDER_GYM_OPENAI_TIMEOUT"] = "120"
        environment["COMMANDER_GYM_OPENAI_MAX_ATTEMPTS"] = "2"
        environment["COMMANDER_GYM_SIDECAR_PROVENANCE"] = evidenceFile.toAbsolutePath().toString()

        sidecar = builder.start()
        waitForPort(sidecarPort, Duration.ofSeconds(20))

        context = SpringApplicationBuilder(GameServerApplication::class.java)
            .run(
                "--server.port=" + serverPort,
                "--spring.main.banner-mode=off",
                "--game.ai.enabled=true",
                "--game.ai.mode=commander-gym",
                "--game.ai.thinking-delay-ms=0",
                "--commander-gym.sidecar.url=http://127.0.0.1:" + sidecarPort,
                "--commander-gym.sidecar.token=" + token,
                "--commander-gym.sidecar.timeout-ms=120000",
                "--logging.level.com.wingedsheep.gameserver=INFO",
                "--logging.level.org.springframework.web.socket=WARN",
            )
        waitForPort(serverPort, Duration.ofSeconds(30))
    }

    @AfterAll
    fun stopLiveStack() {
        if (::context.isInitialized) context.close()
        if (::sidecar.isInitialized) {
            sidecar.destroy()
            if (!sidecar.waitFor(3, java.util.concurrent.TimeUnit.SECONDS)) sidecar.destroyForcibly()
        }
        if (::evidenceFile.isInitialized && System.getenv("COMMANDER_GYM_KEEP_LIVE_EVIDENCE") != "1") {
            Files.deleteIfExists(evidenceFile)
        } else if (::evidenceFile.isInitialized) {
            println("Commander Gym live pod evidence: " + evidenceFile.toAbsolutePath())
        }
    }

    @Test
    fun humanVsThreeLunaCommanderPodUsesNormalMultiplayerLifecycle() {
        val decks = mapOf(
            "human" to loadDeck("krenko.json"),
            "talrand" to loadDeck("talrand.json"),
            "sythis" to loadDeck("sythis.json"),
            "lathril" to loadDeck("lathril.json"),
        )
        decks.values.forEach { deck ->
            assertEquals(100, deck.cards.values.sum(), "${deck.name} must remain an exact 100-card roster deck")
            assertEquals(1, deck.cards[deck.commander], "${deck.name} must contain its commander")
        }

        val client = HumanClient(URI.create("ws://127.0.0.1:" + serverPort + "/game"), json)
        client.connect()
        client.send(ClientMessage.Connect("Commander Gym Live Human"))
        await(Duration.ofSeconds(15), "human connection") { client.connected() != null }
        val humanId = client.connected()!!.playerId

        client.send(
            ClientMessage.CreateTournamentLobby(
                // Argentum currently requires one catalogued set even for PREMADE_DECKS.
                // Boosters are unused here; M21 only satisfies the lobby-create precondition.
                setCodes = listOf("M21"),
                format = "PREMADE_DECKS",
                boosterCount = 0,
                maxPlayers = 4,
                isPublic = false,
                gameMode = "FREE_FOR_ALL",
                attackMode = "MULTIPLE",
                ranked = false,
                rules = "COMMANDER",
            )
        )
        await(Duration.ofSeconds(15), "premade Commander FFA lobby") {
            client.latestLobby()?.let {
                it.settings.format == "PREMADE_DECKS" &&
                    it.settings.gameMode == "FREE_FOR_ALL" &&
                    it.settings.rules == "COMMANDER" &&
                    it.settings.maxPlayers == 4
            } == true
        }

        client.send(
            ClientMessage.UpdateLobbySettings(
                deckFormat = "COMMANDER",
                rules = "COMMANDER",
                gameMode = "FREE_FOR_ALL",
                maxPlayers = 4,
                ranked = false,
            )
        )
        await(Duration.ofSeconds(15), "Commander deck legality") {
            client.latestLobby()?.settings?.deckFormat == "COMMANDER"
        }

        val aiIds = mutableListOf<String>()
        repeat(3) { index ->
            val before = client.latestLobby()!!.players.map { it.playerId }.toSet()
            client.send(ClientMessage.AddAiToLobby)
            await(Duration.ofSeconds(15), "AI seat ${index + 1}") {
                client.latestLobby()!!.players.size == before.size + 1
            }
            val added = client.latestLobby()!!.players.map { it.playerId }.toSet() - before
            assertEquals(1, added.size)
            aiIds += added.single()
        }
        assertEquals(3, aiIds.toSet().size)

        val aiDecks = listOf(decks.getValue("talrand"), decks.getValue("sythis"), decks.getValue("lathril"))
        aiIds.zip(aiDecks).forEach { (playerId, deck) ->
            client.send(
                ClientMessage.SetLobbyAiDeck(
                    playerId = playerId,
                    spec = AiDeckSpec.Fixed(
                        deckList = deck.cards,
                        label = deck.name,
                        commander = deck.commander,
                    ),
                )
            )
            await(Duration.ofSeconds(20), "${deck.name} submission") {
                client.latestLobby()?.players
                    ?.firstOrNull { it.playerId == playerId }
                    ?.deckSubmitted == true
            }
        }

        val humanDeck = decks.getValue("human")
        client.send(ClientMessage.SubmitSealedDeck(humanDeck.cards, commander = humanDeck.commander))
        await(Duration.ofSeconds(20), "human exact roster deck submission") {
            client.latestLobby()?.players?.firstOrNull { it.playerId == humanId }?.deckSubmitted == true
        }
        assertEquals(4, client.latestLobby()!!.players.size)
        assertTrue(client.latestLobby()!!.players.all { it.deckSubmitted })

        client.send(ClientMessage.StartTournamentLobby)
        await(Duration.ofSeconds(60), "four-seat GameStarted") { client.gameStarted() != null }

        val seats = client.gameStarted()!!.players
        assertEquals(4, seats.size)
        assertEquals(1, seats.count { it.isYou })
        assertEquals(3, seats.count { it.isAi })
        assertEquals(aiIds.toSet(), seats.filter { it.isAi }.map { it.playerId }.toSet())

        await(Duration.ofSeconds(60), "human mulligan prompt") { client.latestMulligan() != null }
        client.send(ClientMessage.KeepHand)
        await(Duration.ofMinutes(3), "all four mulligans") {
            client.messages.any { it is ServerMessage.MulliganComplete }
        }
        await(Duration.ofMinutes(3), "three Luna mulligan callbacks") {
            val records = provenanceRecords()
            aiIds.all { id ->
                records.any { it.string("playerId") == id && it.string("callback") == "decideMulligan" }
            }
        }

        val initialStateMessages = client.stateMessageCount()
        val deadline = System.nanoTime() + timeout.toNanos()
        var lastResyncAt = 0L

        while (System.nanoTime() < deadline) {
            val records = provenanceRecords()
            val eachAiActed = aiIds.all { id ->
                records.any { it.string("playerId") == id && it.string("callback") == "chooseAction" }
            }
            val parameterizedAction = records.any { record ->
                if (record.string("callback") != "chooseAction") return@any false
                val choice = record["choice"] as? JsonObject ?: return@any false
                val params = choice["params"] as? JsonObject
                params != null && params.isNotEmpty()
            }
            val structuredChoice = records.any { record ->
                if (record.string("callback") == "chooseBottomCards") return@any true
                if (record.string("callback") != "chooseAction") return@any false
                (record["choice"] as? JsonObject)?.string("channel") == "decision"
            }
            val allAiAreConfiguredModel = aiIds.all { id ->
                records.any { record -> record.string("playerId") == id && record.toString().contains(model) }
            }
            val humanObservedAiPermanent = client.latestFullState()?.cards?.values?.any { card ->
                card.ownerId.value in aiIds && card.zone?.zoneType == Zone.BATTLEFIELD
            } == true

            if (
                eachAiActed &&
                parameterizedAction &&
                structuredChoice &&
                allAiAreConfiguredModel &&
                humanObservedAiPermanent &&
                client.stateMessageCount() > initialStateMessages
            ) {
                assertNoHiddenSnapshotLeak(records)
                printSummary(client, aiIds, records, "accepted")
                client.close()
                return
            }

            if (client.gameOver() != null) {
                assertNoHiddenSnapshotLeak(records)
                printSummary(client, aiIds, records, "terminal-before-all-criteria")
                client.close()
                throw AssertionError("game ended before every live acceptance criterion was observed")
            }

            val now = System.nanoTime()
            if (now - lastResyncAt > Duration.ofSeconds(5).toNanos()) {
                client.send(ClientMessage.RequestResync)
                lastResyncAt = now
            }

            client.latestActionWindow()?.let { window ->
                val safe = chooseHumanAction(window.actions)
                if (safe != null) {
                    val signature = client.stateMessageCount().toString() + ":" +
                        window.interactionEpoch + ":" + safe.toString()
                    if (signature != client.lastSubmittedSignature) {
                        client.send(ClientMessage.SubmitAction(safe, interactionEpoch = window.interactionEpoch))
                        client.lastSubmittedSignature = signature
                    }
                }
            }
            Thread.sleep(75)
        }

        val records = provenanceRecords()
        assertNoHiddenSnapshotLeak(records)
        printSummary(client, aiIds, records, "timeout")
        client.close()
        throw AssertionError(
            "live human + three Luna acceptance criteria were not all observed within ${timeout.seconds}s"
        )
    }

    private fun chooseHumanAction(actions: List<LegalActionInfo>): GameAction? =
        actions.firstOrNull { it.actionType == "PlayLand" }?.action
            ?: actions.firstOrNull { it.actionType == "PassPriority" }?.action
            ?: actions.firstOrNull { it.actionType == "DeclareAttackers" }?.action
            ?: actions.firstOrNull { it.actionType == "DeclareBlockers" }?.action

    private fun assertNoHiddenSnapshotLeak(records: List<JsonObject>) {
        assertFalse(records.any { it.toString().contains("\"snapshot\"") })
    }

    private fun printSummary(
        client: HumanClient,
        aiIds: List<String>,
        records: List<JsonObject>,
        result: String,
    ) {
        val callbacks = records.groupingBy { it.string("callback") ?: "unknown" }.eachCount()
        val perSeat = aiIds.associateWith { id -> records.count { it.string("playerId") == id } }
        val parameterized = records.count { record ->
            val params = (record["choice"] as? JsonObject)?.get("params") as? JsonObject
            record.string("callback") == "chooseAction" && params != null && params.isNotEmpty()
        }
        val structured = records.count { record ->
            record.string("callback") == "chooseBottomCards" ||
                ((record["choice"] as? JsonObject)?.string("channel") == "decision")
        }
        val terminal = client.gameOver()
        println(
            "LIVE_COMMANDER_POD_RESULT=" + json.encodeToString(
                mapOf(
                    "result" to result,
                    "model" to model,
                    "aiSeatIds" to aiIds.joinToString(","),
                    "policyCallbacks" to records.size.toString(),
                    "callbacks" to callbacks.toString(),
                    "callbacksPerAiSeat" to perSeat.toString(),
                    "parameterizedActions" to parameterized.toString(),
                    "structuredChoices" to structured.toString(),
                    "humanStateMessages" to client.stateMessageCount().toString(),
                    "terminalWinner" to (terminal?.winnerId?.value ?: ""),
                    "terminalReason" to (terminal?.reason?.name ?: ""),
                )
            )
        )
    }

    private fun provenanceRecords(): List<JsonObject> {
        if (!Files.exists(evidenceFile)) return emptyList()
        return runCatching {
            Files.readAllLines(evidenceFile)
                .filter { it.isNotBlank() }
                .map { json.parseToJsonElement(it).jsonObject }
        }.getOrElse { emptyList() }
    }

    private fun JsonObject.string(name: String): String? =
        this[name]?.jsonPrimitive?.content

    private fun loadDeck(fileName: String): RosterDeck {
        val file = File(repoRoot, "rosters/argentum-native-v1/$fileName")
        check(file.isFile) { "Roster deck not found: $file" }
        val root = json.parseToJsonElement(file.readText()).jsonObject
        return RosterDeck(
            name = root.getValue("name").jsonPrimitive.content,
            commander = root.getValue("commander").jsonPrimitive.content,
            cards = root.getValue("cards").jsonObject.mapValues { (_, count) -> count.jsonPrimitive.int },
        )
    }

    private fun verifyOpenAiSdk() {
        val python = System.getenv("PYTHON")?.takeIf { it.isNotBlank() } ?: "python3"
        val process = ProcessBuilder(python, "-c", "import openai")
            .directory(repoRoot)
            .redirectErrorStream(true)
            .start()
        check(process.waitFor() == 0) {
            "Python OpenAI SDK is required; run: python3 -m pip install -r requirements-openai.txt"
        }
    }

    private fun await(timeout: Duration, description: String, predicate: () -> Boolean) {
        val deadline = System.nanoTime() + timeout.toNanos()
        while (System.nanoTime() < deadline) {
            if (predicate()) return
            Thread.sleep(50)
        }
        throw AssertionError("Timed out waiting for $description")
    }

    private fun waitForPort(port: Int, timeout: Duration) {
        await(timeout, "port $port") {
            runCatching { Socket("127.0.0.1", port).use { }; true }.getOrDefault(false)
        }
    }

    private fun freePort(): Int = ServerSocket(0).use { it.localPort }

    private fun findRepoRoot(): File {
        var current = File(System.getProperty("user.dir")).absoluteFile
        while (true) {
            if (
                File(current, "commander_gym").isDirectory &&
                File(current, "rosters/argentum-native-v1").isDirectory &&
                File(current, "jvm-adapter").isDirectory
            ) return current
            current = current.parentFile ?: break
        }
        error("Could not locate commander-gym repo root from " + System.getProperty("user.dir"))
    }

    private data class RosterDeck(
        val name: String,
        val commander: String,
        val cards: Map<String, Int>,
    )

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
        @Volatile private var transportError: Throwable? = null
        private lateinit var webSocket: WebSocket
        @Volatile var lastSubmittedSignature: String? = null

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

        fun connected(): ServerMessage.Connected? =
            messages.filterIsInstance<ServerMessage.Connected>().lastOrNull()
        fun latestLobby(): ServerMessage.LobbyUpdate? =
            messages.filterIsInstance<ServerMessage.LobbyUpdate>().lastOrNull()
        fun gameStarted(): ServerMessage.GameStarted? =
            messages.filterIsInstance<ServerMessage.GameStarted>().lastOrNull()
        fun latestMulligan(): ServerMessage.MulliganDecision? =
            messages.filterIsInstance<ServerMessage.MulliganDecision>().lastOrNull()
        fun gameOver(): ServerMessage.GameOver? =
            messages.filterIsInstance<ServerMessage.GameOver>().lastOrNull()
        fun stateMessageCount(): Int = messages.count {
            it is ServerMessage.StateUpdate || it is ServerMessage.StateDeltaUpdate
        }
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

        override fun onOpen(webSocket: WebSocket) { webSocket.request(1) }

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
                    runCatching { json.decodeFromString<ServerMessage>(payload) }
                        .onSuccess(messages::add)
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
