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
    current native menu still has only PassPriority and mana abilities. Its
    separate component identity leaves the qualified no-choice handler intact.
    """

    name: str = "standing-mana-only-pass"
    version: str = "1"

    def choose(self, observation: Mapping[str, Any]) -> PilotChoice | None:
        previous = NativeNoChoiceHandler().choose(observation)
        if previous is not None:
            return previous
        if (
            observation.get("terminated") is not False
            or observation.get("pendingDecision") is not None
            or not isinstance(observation.get("agentToAct"), str)
            or observation.get("agentToAct") != observation.get("perspectivePlayerId")
        ):
            return None
        legal = observation.get("legalActions")
        if not isinstance(legal, list) or len(legal) < 2:
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
            elif kind != "ActivateAbility" or action.get("isManaAbility") is not True:
                return None
        if len(passes) != 1:
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
