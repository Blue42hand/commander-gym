"""Thin Argentum-native artificial-player contract.

Commander Gym owns strategic choice. Argentum owns observations, legality, execution,
and typed decision semantics. This module deliberately passes Argentum's observation
shape through unchanged and returns only one of Argentum's native response channels:

* an ephemeral legal actionId plus ActionParams; or
* a typed structured DecisionResponse payload.

No transport, relay, HTTP, GitHub, or Commander-specific lifecycle appears here.
Stable replay/training provenance should be consumed from Argentum's wire contract
when available rather than synthesized into a competing Commander Gym ontology.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Union


class PilotContractError(ValueError):
    """Raised when a pilot input or output violates the Argentum player contract."""


@dataclass(frozen=True)
class ArgentumActionChoice:
    """Choose one currently legal Argentum action template."""

    action_id: int
    params: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ArgentumDecisionChoice:
    """Submit one native structured Argentum DecisionResponse payload."""

    response: Mapping[str, Any]
    metadata: Mapping[str, Any] = field(default_factory=dict)


PilotChoice = Union[ArgentumActionChoice, ArgentumDecisionChoice]


class ArtificialPlayer(Protocol):
    """Smallest transport-independent interface for an artificial Magic player."""

    name: str
    version: str

    def choose(self, observation: Mapping[str, Any]) -> PilotChoice:
        ...


def _require_observation(observation: Mapping[str, Any]) -> None:
    if not isinstance(observation, Mapping):
        raise PilotContractError("Argentum observation must be a mapping")
    if observation.get("terminated") is True:
        raise PilotContractError("terminal Argentum observations do not accept pilot choices")
    if observation.get("agentToAct") is None:
        raise PilotContractError("non-terminal Argentum observation must identify agentToAct")

    perspective = observation.get("perspectivePlayerId")
    if perspective is not None and perspective != observation.get("agentToAct"):
        raise PilotContractError(
            "pilot observation perspectivePlayerId must match agentToAct"
        )


def _validate_action_choice(
    choice: ArgentumActionChoice,
    observation: Mapping[str, Any],
) -> ArgentumActionChoice:
    if type(choice.action_id) is not int:
        raise PilotContractError("Argentum action_id must be an integer")
    if not isinstance(choice.params, Mapping):
        raise PilotContractError("Argentum action params must be a mapping")
    if not isinstance(choice.metadata, Mapping):
        raise PilotContractError("pilot metadata must be a mapping")

    legal = observation.get("legalActions")
    if not isinstance(legal, list):
        raise PilotContractError("Argentum legalActions must be an array")

    legal_ids = []
    for action in legal:
        if not isinstance(action, Mapping) or type(action.get("actionId")) is not int:
            raise PilotContractError("each Argentum legal action must carry integer actionId")
        legal_ids.append(action["actionId"])

    if len(legal_ids) != len(set(legal_ids)):
        raise PilotContractError("Argentum legalActions contain duplicate actionId values")
    if choice.action_id not in set(legal_ids):
        raise PilotContractError("pilot selected an actionId not legal in this observation")
    return choice


def _validate_decision_choice(
    choice: ArgentumDecisionChoice,
    pending: Mapping[str, Any],
) -> ArgentumDecisionChoice:
    if not isinstance(choice.response, Mapping) or not choice.response:
        raise PilotContractError("structured decision response must be a non-empty mapping")
    if not isinstance(choice.metadata, Mapping):
        raise PilotContractError("pilot metadata must be a mapping")

    pending_id = pending.get("decisionId")
    if not isinstance(pending_id, str) or not pending_id:
        raise PilotContractError("pending structured decision must carry decisionId")
    response_id = choice.response.get("decisionId")
    if response_id != pending_id:
        raise PilotContractError(
            "structured decision response decisionId does not match current pending decision"
        )
    return choice


def validate_pilot_choice(
    choice: PilotChoice,
    observation: Mapping[str, Any],
) -> PilotChoice:
    """Validate a pilot choice against the exact Argentum observation, fail-closed.

    This function does not execute anything and intentionally knows nothing about
    HTTP, tunnels, GitHub Actions, or other transport. It only selects the correct
    native Argentum response channel and checks live routing handles.
    """

    _require_observation(observation)

    pending = observation.get("pendingDecision")
    if pending is not None and not isinstance(pending, Mapping):
        raise PilotContractError("Argentum pendingDecision must be an object or null")

    requires_structured = bool(
        pending is not None and pending.get("requiresStructuredResponse") is True
    )

    if requires_structured:
        if not isinstance(choice, ArgentumDecisionChoice):
            raise PilotContractError(
                "current Argentum decision requires a structured DecisionResponse"
            )
        return _validate_decision_choice(choice, pending)

    if not isinstance(choice, ArgentumActionChoice):
        raise PilotContractError(
            "current Argentum observation requires selection from legalActions"
        )
    return _validate_action_choice(choice, observation)


def choose_for_observation(
    pilot: ArtificialPlayer,
    observation: Mapping[str, Any],
) -> PilotChoice:
    """Ask a pilot to choose, then validate its answer against Argentum."""

    _require_observation(observation)
    return validate_pilot_choice(pilot.choose(observation), observation)
