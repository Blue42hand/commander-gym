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
class CertifiedNativeDecisionHandler:
    """Resolve only native structured decisions whose answer is engine-certified or unique.

    This carries the Forge wake-reduction rule forward: deterministic infrastructure choices may
    bypass the strategic model, but any choice with meaningful alternatives still escalates.
    """

    name: str = "certified-native-decision"
    version: str = "1"

    def choose(self, observation: Mapping[str, Any]) -> PilotChoice | None:
        pending = observation.get("pendingDecision")
        if not isinstance(pending, Mapping) or pending.get("requiresStructuredResponse") is not True:
            return None
        decision_id = pending.get("decisionId")
        if not isinstance(decision_id, str) or not decision_id:
            return None
        kind = pending.get("type") or pending.get("kind")

        def decision(response_type: str, **fields: Any) -> ArgentumDecisionChoice:
            return ArgentumDecisionChoice(
                {"type": response_type, "decisionId": decision_id, **fields}
            )

        # Argentum's built-in LLM controller also treats these two as mechanical.
        if kind in ("AssignDamageDecision", "ASSIGN_DAMAGE"):
            defaults = pending.get("defaultAssignments")
            if isinstance(defaults, Mapping):
                return decision("DamageAssignmentResponse", assignments=dict(defaults))

        if kind in ("CombatResolutionDecision", "COMBAT_RESOLUTION"):
            edges = pending.get("edges")
            if isinstance(edges, list) and all(isinstance(edge, Mapping) for edge in edges):
                chosen = []
                for edge in edges:
                    edge_id = edge.get("id")
                    amount = edge.get("amount")
                    if not isinstance(edge_id, str) or type(amount) is not int:
                        return None
                    chosen.append({"edgeId": edge_id, "amount": amount})
                return decision(
                    "CombatResolutionResponse",
                    edges=chosen,
                    orderedBlockers={},
                    orderedAttackers={},
                )

        if kind in ("SelectManaSourcesDecision", "SELECT_MANA_SOURCES"):
            suggestion = pending.get("autoPaySuggestion")
            if isinstance(suggestion, list) and suggestion:
                return decision(
                    "ManaSourcesSelectedResponse",
                    selectedSources=[],
                    autoPay=True,
                    waterbendPermanents=[],
                    declined=False,
                )
            if pending.get("canDecline") is True:
                return decision(
                    "ManaSourcesSelectedResponse",
                    selectedSources=[],
                    autoPay=False,
                    waterbendPermanents=[],
                    declined=True,
                )
            # Mirrors Argentum SelectManaSourcesHandler.bestEffortResponse for mandatory
            # payments when the solver has no direct auto-pay suggestion.
            sources = pending.get("availableSources")
            if isinstance(sources, list) and all(isinstance(source, Mapping) for source in sources):
                selected_sources: list[str] = []
                for source in sources:
                    entity_id = source.get("entityId")
                    if source.get("requiresTappingAnotherPermanent") is True:
                        continue
                    if not isinstance(entity_id, str):
                        return None
                    selected_sources.append(entity_id)
                return decision(
                    "ManaSourcesSelectedResponse",
                    selectedSources=selected_sources,
                    autoPay=False,
                    waterbendPermanents=[],
                    declined=False,
                )

        # Unique-choice cases: there is literally no strategic branch to preserve.
        if kind in ("ChooseNumberDecision", "CHOOSE_NUMBER"):
            lo, hi = pending.get("minValue"), pending.get("maxValue")
            if type(lo) is int and lo == hi:
                return decision("NumberChosenResponse", number=lo)

        if kind in ("ChooseColorDecision", "CHOOSE_COLOR"):
            colors = pending.get("availableColors")
            if isinstance(colors, list) and len(colors) == 1 and isinstance(colors[0], str):
                return decision("ColorChosenResponse", color=colors[0])

        if kind in ("ChooseModeDecision", "CHOOSE_MODE"):
            modes = pending.get("modes")
            if (
                pending.get("minModes") == 1
                and pending.get("maxModes") == 1
                and isinstance(modes, list)
            ):
                available = [
                    mode for mode in modes
                    if isinstance(mode, Mapping) and mode.get("available", True) is True
                ]
                if len(available) == 1 and type(available[0].get("index")) is int:
                    return decision("ModesChosenResponse", selectedModes=[available[0]["index"]])

        if kind in ("ChooseOptionDecision", "CHOOSE_OPTION"):
            options = pending.get("options")
            if isinstance(options, list) and len(options) == 1:
                return decision("OptionChosenResponse", optionIndex=0)

        if kind in ("SelectCardsDecision", "SELECT_CARDS"):
            options = pending.get("options")
            lo, hi = pending.get("minSelections"), pending.get("maxSelections")
            ordered = pending.get("ordered", False)
            if isinstance(options, list) and type(lo) is int and type(hi) is int:
                if lo == hi == 0:
                    return decision("CardsSelectedResponse", selectedCards=[])
                if lo == hi == len(options) and (not ordered or len(options) <= 1):
                    return decision("CardsSelectedResponse", selectedCards=list(options))

        if kind in ("OrderObjectsDecision", "ORDER_OBJECTS", "ReorderLibraryDecision", "REORDER_LIBRARY"):
            objects = pending.get("objects")
            if objects is None:
                objects = pending.get("cards")
            if isinstance(objects, list) and len(objects) <= 1:
                return decision("OrderedResponse", orderedObjects=list(objects))

        if kind in ("DistributeDecision", "DISTRIBUTE"):
            targets = pending.get("targets")
            total = pending.get("totalAmount")
            if (
                pending.get("allowPartial") is not True
                and isinstance(targets, list)
                and len(targets) == 1
                and type(total) is int
            ):
                return decision("DistributionResponse", distribution={str(targets[0]): total})

        return None


@dataclass(frozen=True)
class NoChoiceCombatHandler:
    """Advance attack/block declarations when Argentum exposes no eligible creature."""

    name: str = "no-choice-combat"
    version: str = "1"

    def choose(self, observation: Mapping[str, Any]) -> PilotChoice | None:
        pending = observation.get("pendingDecision")
        if isinstance(pending, Mapping) and pending.get("requiresStructuredResponse") is True:
            return None
        legal = observation.get("legalActions")
        if not isinstance(legal, list):
            return None
        for action in legal:
            if not isinstance(action, Mapping) or type(action.get("actionId")) is not int:
                continue
            kind = action.get("kind")
            if kind == "DeclareAttackers" and not action.get("validAttackers"):
                return ArgentumActionChoice(action_id=action["actionId"])
            if kind == "DeclareBlockers" and not action.get("validBlockers"):
                return ArgentumActionChoice(action_id=action["actionId"])
        return None


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
    handlers: Sequence[CertifiedMechanicalHandler] = (
        ForcedParameterlessChoiceHandler(),
        CertifiedNativeDecisionHandler(),
        NoChoiceCombatHandler(),
    )
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
