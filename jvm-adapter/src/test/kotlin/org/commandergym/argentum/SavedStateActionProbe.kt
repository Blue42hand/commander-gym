package org.commandergym.argentum

import com.wingedsheep.engine.core.*
import com.wingedsheep.engine.state.GameState
import com.wingedsheep.gameserver.config.GameBeansConfig
import com.wingedsheep.gameserver.config.GameProperties
import kotlinx.serialization.json.*
import java.nio.file.Files
import java.nio.file.Path

/** Explicit private offline recovery proof; full snapshots never enter pilot prompts. */
object SavedStateActionProbe {
    @JvmStatic
    fun main(args: Array<String>) {
        val json = Json { serializersModule = engineSerializersModule; allowStructuredMapKeys = true }
        val fixture = Json.parseToJsonElement(Files.readString(Path.of(args[0]))).jsonObject
        val state = json.decodeFromJsonElement<GameState>(fixture.getValue("state"))
        val processor = ActionProcessor(EngineServices(GameBeansConfig(GameProperties()).cardRegistry()))
        fun action(key: String) = json.decodeFromJsonElement<GameAction>(fixture.getValue(key))
        val baseline = processor.process(state, action("baselineAction")).result
        check(baseline.outcome is Outcome.Rejected)
        check(baseline.outcome.toString().contains("Cannot pay attack tax"))
        val replacement = processor.process(state, action("replacementAction")).result
        check(replacement.outcome is Outcome.Done) { "Recovery rejected: ${replacement.outcome}" }
        check(replacement.state.pendingDecision == null)
        val followup = processor.process(replacement.state, action("followupAction")).result
        check(followup.outcome is Outcome.Done) { "Followup rejected: ${followup.outcome}" }
        check(followup.state.pendingDecision == null)
        val report = buildJsonObject {
            put("scope", "Same saved native checkpoint; rejected baseline, policy decline and empty attack declaration executed offline")
            put("baselineOutcome", baseline.outcome.toString())
            put("replacementAccepted", true)
            put("followupAccepted", true)
            put("noPendingPayment", true)
        }
        Files.writeString(Path.of(args[1]), report.toString())
        println(report)
    }
}
