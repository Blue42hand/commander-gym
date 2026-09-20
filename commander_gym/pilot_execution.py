"""Execute one artificial-player choice against an Argentum environment.

The pilot contract itself remains transport-independent.  This module adds the thin
execution seam around that contract: observe the current seat-safe Argentum state,
validate one provider-neutral pilot choice, submit it through an environment adapter,
and retain Argentum-owned semantic provenance for the choice that was made.

Environment adapters deliberately describe capabilities rather than HTTP.  Argentum
Gym is one adapter; the eventual game-server seat can implement the same three methods
without changing pilot strategy or decision routing.
"""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Any, Mapping, Protocol

from .pilot import (
    ArtificialPlayer,
    ArgentumActionChoice,
    ArgentumDecisionChoice,
    PilotContractError,
    choose_for_observation,
)


class PilotEnvironment(Protocol):
    """Minimal environment capabilities needed to execute one pilot choice."""

    def observe(self) -> Mapping[str, Any]:
        ...

    def submit_action(
        self,
        action_id: int,
        params: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        ...

    def submit_decision(self, response: Mapping[str, Any]) -> Mapping[str, Any]:
        ...


@dataclass(frozen=True)
class PilotExecutionTrace:
    """Transport-neutral evidence for one successfully submitted pilot choice.

    ``semantic_id`` is copied verbatim from Argentum's observation.  Commander Gym
    does not synthesize a parallel action identity.  ``live_routing_id`` is retained
    only to diagnose the exact execution attempt; it is not durable semantic identity.
    """

    pilot_name: str
    pilot_version: str
    channel: str
    semantic_id: str
    live_routing_id: int | str
    observation: Mapping[str, Any]
    submitted: Mapping[str, Any]
    result_observation: Mapping[str, Any]
    pilot_metadata: Mapping[str, Any]
    pilot_elapsed_ms: float = 0.0
    submission_elapsed_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "pilot": {"name": self.pilot_name, "version": self.pilot_version},
            "channel": self.channel,
            "semanticId": self.semantic_id,
            "liveRoutingId": self.live_routing_id,
            "observation": dict(self.observation),
            "submitted": dict(self.submitted),
            "resultObservation": dict(self.result_observation),
            "pilotMetadata": dict(self.pilot_metadata),
            "timing": {
                "pilotElapsedMs": self.pilot_elapsed_ms,
                "submissionElapsedMs": self.submission_elapsed_ms,
            },
        }


def _required_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise PilotContractError(f"{label} must be a non-empty string")
    return value


def _action_semantic_id(
    choice: ArgentumActionChoice,
    observation: Mapping[str, Any],
) -> str:
    legal = observation.get("legalActions")
    if not isinstance(legal, list):
        raise PilotContractError("Argentum legalActions must be an array")
    for action in legal:
        if isinstance(action, Mapping) and action.get("actionId") == choice.action_id:
            return _required_string(
                action.get("semanticId"),
                "selected Argentum legal action semanticId",
            )
    # choose_for_observation already rejects this. Keep this branch fail-closed if the
    # validator changes independently later.
    raise PilotContractError("selected actionId disappeared from current legalActions")


def _decision_semantic_id(observation: Mapping[str, Any]) -> tuple[str, str]:
    pending = observation.get("pendingDecision")
    if not isinstance(pending, Mapping):
        raise PilotContractError("structured response requires pendingDecision")
    return (
        _required_string(pending.get("semanticId"), "pending Argentum decision semanticId"),
        _required_string(pending.get("decisionId"), "pending Argentum decisionId"),
    )


def execute_pilot_choice(
    pilot: ArtificialPlayer,
    environment: PilotEnvironment,
) -> PilotExecutionTrace:
    """Observe, choose, validate, submit exactly once, and return native provenance.

    There are deliberately no retries around mutation.  Transport adapters must surface
    unknown-delivery failures so callers can reconcile authoritative Argentum state
    before deciding what to do next.
    """

    observation = environment.observe()
    if not isinstance(observation, Mapping):
        raise PilotContractError("Argentum environment observation must be a mapping")

    pilot_started = monotonic()
    choice = choose_for_observation(pilot, observation)
    pilot_elapsed_ms = (monotonic() - pilot_started) * 1000.0
    pilot_name = _required_string(getattr(pilot, "name", None), "pilot name")
    pilot_version = _required_string(getattr(pilot, "version", None), "pilot version")

    if isinstance(choice, ArgentumActionChoice):
        semantic_id = _action_semantic_id(choice, observation)
        submitted = {"actionId": choice.action_id, "params": dict(choice.params)}
        submission_started = monotonic()
        result = environment.submit_action(choice.action_id, choice.params)
        submission_elapsed_ms = (monotonic() - submission_started) * 1000.0
        channel = "action"
        live_routing_id: int | str = choice.action_id
        metadata = choice.metadata
    elif isinstance(choice, ArgentumDecisionChoice):
        semantic_id, live_routing_id = _decision_semantic_id(observation)
        submitted = dict(choice.response)
        submission_started = monotonic()
        result = environment.submit_decision(choice.response)
        submission_elapsed_ms = (monotonic() - submission_started) * 1000.0
        channel = "decision"
        metadata = choice.metadata
    else:  # pragma: no cover - choose_for_observation currently guarantees the union.
        raise PilotContractError("unsupported pilot choice type")

    if not isinstance(result, Mapping):
        raise PilotContractError("Argentum submission result must be an observation mapping")

    return PilotExecutionTrace(
        pilot_name=pilot_name,
        pilot_version=pilot_version,
        channel=channel,
        semantic_id=semantic_id,
        live_routing_id=live_routing_id,
        observation=observation,
        submitted=submitted,
        result_observation=result,
        pilot_metadata=metadata,
        pilot_elapsed_ms=pilot_elapsed_ms,
        submission_elapsed_ms=submission_elapsed_ms,
    )


class ArgentumGymEnvironment:
    """Adapt one ``ArgentumGymClient`` env to the transport-neutral execution seam."""

    def __init__(self, client: Any, env_id: str):
        self.client = client
        self.env_id = _required_string(env_id, "Argentum env_id")

    def observe(self) -> Mapping[str, Any]:
        return self.client.observe_env(self.env_id)

    def submit_action(
        self,
        action_id: int,
        params: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        return self.client.step_env(self.env_id, action_id, params=params)

    def submit_decision(self, response: Mapping[str, Any]) -> Mapping[str, Any]:
        return self.client.submit_decision(self.env_id, response)
