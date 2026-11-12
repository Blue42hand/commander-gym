package org.commandergym.argentum

import com.wingedsheep.engine.core.*
import com.wingedsheep.engine.state.GameState
import com.wingedsheep.gameserver.config.GameBeansConfig
import com.wingedsheep.gameserver.config.GameProperties
import kotlinx.serialization.json.*
import java.nio.file.Files
import java.nio.file.Path

/** Offline legality checks on both branches of authenticated pre-callback states. */
object CompetencySavedStateProbe {
    @JvmStatic fun main(args:Array<String>) {
        val json=Json { serializersModule=engineSerializersModule;allowStructuredMapKeys=true }
        val fixture=Json.parseToJsonElement(Files.readString(Path.of(args[0]))).jsonObject
        val services=EngineServices(GameBeansConfig(GameProperties()).cardRegistry())
        val processor=ActionProcessor(services)
        val report=buildJsonObject {
            put("scope","Both recorded and bounded replacement choices executed from the same authenticated native checkpoints; no provider calls or new game")
            putJsonArray("cases") {
                for (element in fixture.getValue("cases").jsonArray) {
                    val entry=element.jsonObject
                    val initial=json.decodeFromJsonElement<GameState>(entry.getValue("state"))
                    add(buildJsonObject {
                        put("callbackSequence",entry.getValue("sequence"))
                        for (branch in listOf("baseline","replacement")) {
                            val choice=entry.getValue(branch).jsonObject
                            val template=json.decodeFromJsonElement<GameAction>(choice.getValue("action"))
                            val params=choice["params"]
                            val action=if(params==null) template else ActionParameterizer.apply(template,json.decodeFromJsonElement<ActionParams>(params),initial)
                            val result=processor.process(initial,action).result
                            check(result.outcome !is Outcome.Rejected) { "$branch rejected for callback ${entry["sequence"]}: ${result.outcome}" }
                            putJsonObject(branch) {
                                put("accepted",true)
                                put("outcome",result.outcome.toString())
                                put("pendingDecision",result.state.pendingDecision?.javaClass?.simpleName)
                                put("actionType",action.javaClass.simpleName)
                            }
                        }
                    })
                }
            }
        }
        Files.writeString(Path.of(args[1]),report.toString());println(report)
    }
}
