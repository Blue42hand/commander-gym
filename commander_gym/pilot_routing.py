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

        if action.get("kind") != "PassPriority" and action.get("isDecisionOption") is not True:
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
