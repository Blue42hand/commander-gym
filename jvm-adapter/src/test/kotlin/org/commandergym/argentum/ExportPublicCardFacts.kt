package org.commandergym.argentum

import com.wingedsheep.gameserver.config.GameBeansConfig
import com.wingedsheep.gameserver.config.GameProperties
import kotlinx.serialization.json.*
import java.nio.file.Files
import java.nio.file.Path

/** Offline preparation utility. Input is a JSON array of public card names. */
fun main(args: Array<String>) {
    require(args.size == 2) { "Expected public-card-names.json output.json" }
    val names = Json.parseToJsonElement(Files.readString(Path.of(args[0]))).jsonArray.map { it.jsonPrimitive.content }
    val facts = PublicCardFacts.export(GameBeansConfig(GameProperties()).cardRegistry(), names,
        requireNotNull(System.getProperty("commander.gym.facts.engineRevision")))
    Files.writeString(Path.of(args[1]), facts.toString(), java.nio.file.StandardOpenOption.CREATE_NEW)
}
