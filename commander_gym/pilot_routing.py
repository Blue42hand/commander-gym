"""Provider-neutral routing for mechanical versus strategic pilot choices.

This module carries forward one Forge-era efficiency idea at the policy level only:
when Argentum presents a genuinely forced, parameter-free choice that Commander Gym
has explicitly certified as mechanical, do not wake the strategic pilot. Everything
else escalates unchanged to a provider-neutral :class:`ArtificialPlayer`.

The router does not reconstruct rules, mutate observations, or depend on transport.
Argentum remains authoritative for the observation and legal action/decision set, and
the normal pilot validator still checks the returned choice before execution.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping, Protocol, Sequence

from .pilot import (
    ArtificialPlayer,
    ArgentumActionChoice,
    ArgentumDecisionChoice,
    PilotChoice,
    PilotContractError,
)


class CertifiedMechanicalHandler(Protocol):
    """An explicitly certified deterministic policy for a narrow mechanical case."""

    name: str
    version: str

    def choose(self, observation: Mapping[str, Any]) -> PilotChoice | None:
        ...


@dataclass(frozen=True)
class ForcedParameterlessChoiceHandler:
    """Handle only a single forced choice that cannot require strategic parameters.

    The certification is intentionally conservative. It accepts either Argentum's
    ordinary ``PassPriority`` action or one already-folded decision option, and only
    when that is the sole legal action and the observation does not require a
    structured decision response. Parameter-bearing combat/target/X/damage choices
    are never synthesized here.
    """

    name: str = "forced-parameterless-choice"
    version: str = "1"

    def choose(self, observation: Mapping[str, Any]) -> PilotChoice | None:
        pending = observation.get("pendingDecision")
        if isinstance(pending, Mapping) and pending.get("requiresStructuredResponse") is True:
            return None

        legal = observation.get("legalActions")
        if not isinstance(legal, list) or len(legal) != 1:
            return None
        action = legal[0]
        if not isinstance(action, Mapping) or type(action.get("actionId")) is not int:
            return None

        if action.get("kind") == "PassPriority":
            # Argentum currently serializes generic target-bound defaults on every
            # legal action (including PassPriority). The action kind is authoritative:
            # passing priority never consumes ActionParams, so those unrelated default
            # fields must not turn a forced pass into a model wake.
            if action.get("affordable") is False:
                return None
            return ArgentumActionChoice(action_id=action["actionId"])

        if action.get("isDecisionOption") is not True:
            return None
        if action.get("affordable") is False or action.get("hasXCost") is True:
            return None
        if action.get("requiresDamageDistribution") is True:
            return None
        if action.get("minTargets", 0) != 0 or action.get("maxTargets", 0) != 0:
            return None
        for field_name in (
            "targetEntityIds",
            "validAttackers",
            "mandatoryAttackers",
            "validAttackTargets",
            "validBlockers",
        ):
            if action.get(field_name):
                return None
        for field_name in ("blockerMaxBlockCounts", "mandatoryBlockerAssignments"):
            if action.get(field_name):
                return None

        return ArgentumActionChoice(action_id=action["actionId"])


@dataclass(frozen=True)
class NativeNoChoiceHandler:
    """Port Forge's certified no-action and empty-combat fast paths to Argentum.

    Argentum's legal actions are native. A sole PassPriority action is forced; empty
    native attacker/blocker candidate lists similarly certify an empty declaration.
    Even an otherwise simple mana activation remains a player choice: the recorded
    early-game replay contains a mana activation in a pass-plus-mana menu.

    This is a new component identity: the qualified foundation Pilot still resolves
    its original ForcedParameterlessChoiceHandler unchanged.
    """

    name: str = "native-no-choice"
    version: str = "3"

    def choose(self, observation: Mapping[str, Any]) -> PilotChoice | None:
        forced = ForcedParameterlessChoiceHandler().choose(observation)
        if forced is not None:
            return forced
        if observation.get("pendingDecision") is not None:
            return None
        legal = observation.get("legalActions")
        if not isinstance(legal, list) or not legal:
            return None
        if any(not isinstance(action, Mapping) for action in legal):
            return None

        if len(legal) != 1:
            return None
        action = legal[0]
        kind = action.get("kind")
        candidates = {
            "DeclareAttackers": "validAttackers",
            "DeclareBlockers": "validBlockers",
        }.get(kind)
        if candidates is None or action.get(candidates) != []:
            return None
        if type(action.get("actionId")) is not int or action.get("affordable") is False:
            return None
        if action.get("isDecisionOption") is True:
            return None
        if action.get("mandatoryAttackers") or action.get("mandatoryBlockerAssignments"):
            return None
        native = action.get("action")
        field = "attackers" if kind == "DeclareAttackers" else "blockers"
        if not isinstance(native, Mapping) or native.get("type") != kind:
            return None
        if native.get(field) != {}:
            return None
        return ArgentumActionChoice(action_id=action["actionId"])


@dataclass(frozen=True)
class StandingManaOnlyPassHandler:
    """Opt-in pass when Argentum offers no response except optional mana setup.

    This is a player-behavior policy, not a claim that passing is forced. It is
    deliberately stateless: a changed stack does not wake the provider if the
    current native menu still has only PassPriority and mana abilities. Sole
    no-choice actions pass strict wire checks before reuse of the qualified
    handler; its separate component identity and behavior remain intact.
    """

    name: str = "standing-mana-only-pass"
    version: str = "1"
    ignore_unaffordable_abilities: bool = False

    def choose(self, observation: Mapping[str, Any]) -> PilotChoice | None:
        if (
            observation.get("terminated") is not False
            or observation.get("pendingDecision") is not None
            or not isinstance(observation.get("agentToAct"), str)
            or observation.get("agentToAct") != observation.get("perspectivePlayerId")
        ):
            return None
        legal = observation.get("legalActions")
        if not isinstance(legal, list) or not legal:
            return None
        seat = observation["agentToAct"]
        ids: set[int] = set()
        passes: list[Mapping[str, Any]] = []
        for action in legal:
            if (
                not isinstance(action, Mapping)
                or type(action.get("actionId")) is not int
                or not isinstance(action.get("semanticId"), str)
                or not action["semanticId"]
                or (action.get("isDecisionOption") is not None
                    and action.get("isDecisionOption") is not False)
                or not isinstance(action.get("action"), Mapping)
                or action["action"].get("playerId") != seat
                or type(action.get("affordable")) is not bool
            ):
                return None
            action_id = action["actionId"]
            if action_id in ids:
                return None
            ids.add(action_id)
            kind = action.get("kind")
            if action["action"].get("type") != kind:
                return None
            if kind == "PassPriority":
                if action.get("affordable") is not True or action.get("isManaAbility") is not False:
                    return None
                passes.append(action)
            elif len(legal) == 1 and kind in {"DeclareAttackers", "DeclareBlockers"}:
                # Retain the certified empty-combat path after validating its
                # seat and native wire metadata above.
                continue
            elif (
                self.ignore_unaffordable_abilities
                and kind == "ActivateAbility"
                and action.get("isManaAbility") is False
                and action.get("affordable") is False
                and action.get("isAffordable") is False
            ):
                # Forge's actionability path ignored actions certified unavailable.
                # If Argentum later marks this ability affordable, this handler
                # escalates; this component never infers affordability itself.
                continue
            elif kind != "ActivateAbility" or action.get("isManaAbility") is not True:
                return None
        previous = NativeNoChoiceHandler().choose(observation)
        if previous is not None:
            return previous
        if len(legal) < 2:
            return None
        if len(passes) != 1:
            return None
        return ArgentumActionChoice(action_id=passes[0]["actionId"])


@dataclass(frozen=True)
class AllUnaffordablePassHandler:
    """Opt-in pass when every other native offer is explicitly unaffordable.

    Validate the complete native menu before retaining the prior standing pass
    behavior or handling a fully unaffordable menu. Unknown offers wake the pilot.
    """

    name: str = "native-all-unaffordable-pass"
    version: str = "1"
    allow_unaffordable_cycling: bool = False
    allow_unaffordable_exile_land: bool = False

    def choose(self, observation: Mapping[str, Any]) -> PilotChoice | None:
        seat = observation.get("agentToAct")
        state = observation.get("state")
        legal = observation.get("legalActions")
        if (
            observation.get("terminated") is not False
            or observation.get("pendingDecision") is not None
            or not isinstance(seat, str) or not seat
            or observation.get("perspectivePlayerId") != seat
            or not isinstance(state, Mapping) or state.get("priorityPlayerId") != seat
            or not isinstance(legal, list) or not legal
        ):
            return None
        passes: list[Mapping[str, Any]] = []
        action_ids: set[int] = set()
        semantic_ids: set[str] = set()
        native_types = {
            "ActivateAbility": "ActivateAbility",
            "CastSpell": "CastSpell",
            "CastWithKicker": "CastSpell",
            "DeclareAttackers": "DeclareAttackers",
            "DeclareBlockers": "DeclareBlockers",
        }
        if self.allow_unaffordable_cycling:
            native_types["CycleCard"] = "CycleCard"
        if self.allow_unaffordable_exile_land:
            native_types["PlayLand"] = "PlayLand"
        for offer in legal:
            if not isinstance(offer, Mapping):
                return None
            action_id, semantic_id = offer.get("actionId"), offer.get("semanticId")
            action = offer.get("action")
            if (
                type(action_id) is not int or action_id in action_ids
                or not isinstance(semantic_id, str) or not semantic_id
                or semantic_id in semantic_ids
                or not isinstance(action, Mapping) or action.get("playerId") != seat
                or offer.get("isDecisionOption", False) is not False
                or type(offer.get("affordable")) is not bool
                or type(offer.get("isAffordable")) is not bool
            ):
                return None
            action_ids.add(action_id)
            semantic_ids.add(semantic_id)
            kind = offer.get("kind")
            if offer.get("actionType") != kind:
                return None
            if kind == "PassPriority":
                if (
                    action != {"type": "PassPriority", "playerId": seat}
                    or offer.get("affordable") is not True
                    or offer.get("isAffordable") is not True
                    or offer.get("isManaAbility") is not False
                ):
                    return None
                passes.append(offer)
            elif (
                kind not in native_types
                or action.get("type") != native_types[kind]
                or type(offer.get("isManaAbility")) is not bool
            ):
                return None
            if kind == "PlayLand":
                # Permission effects can keep an exiled land in the native menu
                # after its land play becomes illegal. Trust only the engine's
                # explicit false certificates and a confined, plain exile play.
                card_id = action.get("cardId")
                zones = state.get("zones")
                if (
                    offer.get("affordable") is not False
                    or offer.get("isAffordable") is not False
                    or offer.get("isManaAbility") is not False
                    or offer.get("sourceZone") != "EXILE"
                    or offer.get("parameterSpec") != {"allowedFields": {}}
                    or set(action) != {"type", "playerId", "cardId", "asBackFace"}
                    or action.get("asBackFace") is not False
                    or not isinstance(card_id, str) or not card_id
                    or not isinstance(zones, list)
                    or sum(
                        zone.get("cardIds", []).count(card_id)
                        for zone in zones
                        if isinstance(zone, Mapping)
                        and isinstance(zone.get("zoneId"), Mapping)
                        and zone["zoneId"].get("zoneType") == "Exile"
                        and isinstance(zone.get("cardIds"), list)
                    ) != 1
                ):
                    return None
            if kind == "CycleCard":
                # Argentum's CyclingEnumerator certifies affordability with its
                # mana solver; LegalActionEnricher copies the same fact into
                # isAffordable. Only a plain, non-X, unavailable native cycle
                # may join this all-unaffordable menu. Other forms wake Gym.
                card_id = action.get("cardId")
                zones = state.get("zones")
                if not isinstance(zones, list):
                    return None
                if any(
                    not isinstance(zone, Mapping)
                    or not isinstance(zone.get("zoneId"), Mapping)
                    or not isinstance(zone["zoneId"].get("ownerId"), str)
                    or not zone["zoneId"]["ownerId"]
                    or not isinstance(zone["zoneId"].get("zoneType"), str)
                    or not zone["zoneId"]["zoneType"]
                    or not isinstance(zone.get("cardIds"), list)
                    or any(not isinstance(entry, str) or not entry
                           for entry in zone["cardIds"])
                    for zone in zones
                ):
                    return None
                hand = [zone for zone in zones
                        if isinstance(zone, Mapping)
                        and zone.get("zoneId") == {"ownerId": seat, "zoneType": "Hand"}]
                if (
                    offer.get("affordable") is not False
                    or offer.get("isAffordable") is not False
                    or offer.get("isManaAbility") is not False
                    or offer.get("hasXCost") is not False
                    or offer.get("maxAffordableX") is not None
                    or type(offer.get("minX")) is not int
                    or offer["minX"] != 0
                    or offer.get("minimumManaCostString") is not None
                    or offer.get("manaCostPerExtraTarget") is not None
                    or offer.get("additionalCostInfo") is not None
                    or offer.get("sourceZone") is not None
                    or offer.get("requiresTargets") is not False
                    or offer.get("validTargets") is not None
                    or offer.get("requiresManaColorChoice") is not False
                    or offer.get("requiresDamageDistribution") is not False
                    or offer.get("modalEnumeration") is not None
                    or any(offer.get(field) is not False for field in (
                        "hasConvoke", "hasDelve", "hasHarmonize", "hasTapForGeneric",
                        "tapForPower", "xConstrainsTargetCount", "xConstrainsTargetManaValue",
                        "xConstrainsTargetManaValueExactly", "xConstrainsTargetPower",
                    ))
                    or any(offer.get(field) is not None for field in (
                        "tapForPowerCreatures", "tapForPowerRequired", "tapForGenericAmount",
                        "tapForGenericLabel", "minDelveNeeded", "validConvokeCreatures",
                        "validDelveCards", "validHarmonizeCreatures",
                        "validTapForGenericPermanents",
                    ))
                    or offer.get("parameterSpec") != {"allowedFields": {}}
                    or not isinstance(offer.get("manaCostString"), str)
                    or re.fullmatch(r"(?:\{(?:[1-9][0-9]*|[WUBRGC])\})+",
                                    offer["manaCostString"]) is None
                    or set(action) != {"type", "playerId", "cardId", "paymentStrategy", "xValue"}
                    or not isinstance(card_id, str) or not card_id
                    or action.get("paymentStrategy") != {"type": "AutoPay"}
                    or action.get("xValue") is not None
                    or len(hand) != 1
                    or not isinstance(hand[0].get("cardIds"), list)
                    or hand[0]["cardIds"].count(card_id) != 1
                    or len(hand[0]["cardIds"]) != len(set(hand[0]["cardIds"]))
                ):
                    return None
        previous = StandingManaOnlyPassHandler(
            name=self.name, version=self.version, ignore_unaffordable_abilities=True,
        ).choose(observation)
        if previous is not None:
            return previous
        if len(legal) < 2 or len(passes) != 1:
            return None
        if any(
            offer.get("kind") != "PassPriority" and (
                offer.get("affordable") is not False
                or offer.get("isAffordable") is not False
            )
            for offer in legal
        ):
            return None
        return ArgentumActionChoice(action_id=passes[0]["actionId"])


def _annotate(choice: PilotChoice, routing: Mapping[str, Any]) -> PilotChoice:
    """Attach routing evidence without changing Argentum execution semantics."""

    if isinstance(choice, ArgentumActionChoice):
        return ArgentumActionChoice(
            action_id=choice.action_id,
            params=choice.params,
            metadata={**dict(choice.metadata), "routing": dict(routing)},
        )
    if isinstance(choice, ArgentumDecisionChoice):
        return ArgentumDecisionChoice(
            response=choice.response,
            metadata={**dict(choice.metadata), "routing": dict(routing)},
        )
    raise PilotContractError("strategic pilot returned unsupported choice type")


@dataclass(frozen=True)
class RoutingPilot:
    """Use certified deterministic handlers first, then one strategic pilot.

    A strategic/model/provider failure is propagated rather than replaced with an
    improvised fallback. That keeps execution fail-closed and makes failed wakes
    visible to telemetry instead of silently changing policy.
    """

    strategic_pilot: ArtificialPlayer
    handlers: Sequence[CertifiedMechanicalHandler] = (ForcedParameterlessChoiceHandler(),)
    name: str = "certified-routing"
    version: str = "1"

    def choose(self, observation: Mapping[str, Any]) -> PilotChoice:
        for handler in self.handlers:
            choice = handler.choose(observation)
            if choice is not None:
                return _annotate(
                    choice,
                    {
                        "path": "mechanical",
                        "handler": handler.name,
                        "handlerVersion": handler.version,
                        "strategicWakeAvoided": True,
                    },
                )

        choice = self.strategic_pilot.choose(observation)
        return _annotate(
            choice,
            {
                "path": "strategic",
                "delegate": getattr(self.strategic_pilot, "name", None),
                "delegateVersion": getattr(self.strategic_pilot, "version", None),
                "strategicWakeAvoided": False,
            },
        )
