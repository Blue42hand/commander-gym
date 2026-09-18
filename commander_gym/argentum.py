"""Commander Gym canonical identity boundary for Argentum ``:gym`` observations.

Argentum intentionally regenerates integer action IDs after every observation/step.
Commander Gym needs durable semantic identities for research records while retaining the
current Argentum handles needed to execute a choice. This module owns only that research
boundary; rules, observations, legality, and state remain Argentum responsibilities.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Tuple

from .records import ActionRecord, DecisionRecord, PilotProvenance

CANONICAL_ARGENTUM_VERSION = "commander-gym-argentum-v1"


class ArgentumCanonicalError(ValueError):
    """Raised when an Argentum observation cannot be mapped fail-closed."""


class StaleArgentumDecisionError(ArgentumCanonicalError):
    """Raised when a response targets a different schema/state/decision."""


def _stable(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(kind: str, value: Any) -> str:
    material = {
        "version": CANONICAL_ARGENTUM_VERSION,
        "kind": kind,
        "value": value,
    }
    return hashlib.sha256(_stable(material).encode("utf-8")).hexdigest()


def _require_nonempty_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ArgentumCanonicalError(f"{name} must be a non-empty string")
    return value


def _semantic_action_payload(action: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(action, Mapping):
        raise ArgentumCanonicalError("legalActions entries must be objects")
    if type(action.get("actionId")) is not int:
        raise ArgentumCanonicalError("legal action is missing integer actionId")
    semantic = {key: copy.deepcopy(value) for key, value in action.items() if key != "actionId"}
    if not semantic:
        raise ArgentumCanonicalError("legal action has no semantic fields")
    return semantic


def _pending_semantics(pending: Any) -> Dict[str, Any] | None:
    if pending is None:
        return None
    if not isinstance(pending, Mapping):
        raise ArgentumCanonicalError("pendingDecision must be an object or null")
    return {key: copy.deepcopy(value) for key, value in pending.items() if key != "decisionId"}


@dataclass(frozen=True)
class ArgentumCanonicalDecision:
    """One seat-authorized Argentum decision with durable Commander Gym IDs."""

    decision_id: str
    decision_type: str
    observation_schema: str
    state_digest: str
    perspective_player_id: Any
    agent_to_act: Any
    native_pending_decision_id: str | None
    observation: Dict[str, Any]
    legal_actions: Tuple[ActionRecord, ...]
    _argentum_action_ids: Mapping[str, int]

    @classmethod
    def from_observation(cls, observation: Mapping[str, Any]) -> "ArgentumCanonicalDecision":
        if not isinstance(observation, Mapping):
            raise ArgentumCanonicalError("Argentum observation must be an object")
        if observation.get("type") not in (None, "Game"):
            raise ArgentumCanonicalError("Only Argentum game observations are supported")

        schema_hash = _require_nonempty_string(observation.get("schemaHash"), "schemaHash")
        state_digest = _require_nonempty_string(observation.get("stateDigest"), "stateDigest")

        perspective = observation.get("perspectivePlayerId")
        if perspective is None:
            raise ArgentumCanonicalError("perspectivePlayerId is required")
        agent = observation.get("agentToAct")
        if observation.get("terminated") is True:
            raise ArgentumCanonicalError("Terminal observations do not contain a pilot decision")
        if agent is None:
            raise ArgentumCanonicalError("agentToAct is required for a non-terminal decision")

        pending = observation.get("pendingDecision")
        pending_semantics = _pending_semantics(pending)
        native_pending_id = None
        if pending is not None:
            native_pending_id = _require_nonempty_string(
                pending.get("decisionId"), "pendingDecision.decisionId"
            )
            decision_type = _require_nonempty_string(pending.get("kind"), "pendingDecision.kind")
        else:
            decision_type = "LEGAL_ACTION"

        raw_actions = observation.get("legalActions")
        if not isinstance(raw_actions, list):
            raise ArgentumCanonicalError("legalActions must be an array")
        if not raw_actions:
            if pending is not None and pending.get("requiresStructuredResponse") is True:
                raise ArgentumCanonicalError(
                    "Structured Argentum decisions are not supported by the v1 canonical action adapter"
                )
            raise ArgentumCanonicalError("legalActions must not be empty for a pilot decision")

        records = []
        action_ids: Dict[str, int] = {}
        for raw in raw_actions:
            semantic = _semantic_action_payload(raw)
            semantic_id = "argentum-action-v1:" + _digest("action", semantic)
            if semantic_id in action_ids:
                raise ArgentumCanonicalError(
                    "Argentum exposed duplicate semantic legal actions; refusing ambiguous mapping"
                )
            native_id = raw["actionId"]
            action_ids[semantic_id] = native_id
            records.append(
                ActionRecord(
                    action_id=semantic_id,
                    label=semantic.get("description"),
                    payload={
                        "engine": "argentum",
                        "argentum_action_id": native_id,
                        "semantic": semantic,
                    },
                )
            )

        decision_semantics = {
            "schemaHash": schema_hash,
            "stateDigest": state_digest,
            "perspectivePlayerId": perspective,
            "agentToAct": agent,
            "decisionType": decision_type,
            "pendingDecision": pending_semantics,
            "legalActionIds": sorted(action_ids),
        }
        decision_id = "argentum-decision-v1:" + _digest("decision", decision_semantics)

        return cls(
            decision_id=decision_id,
            decision_type=decision_type,
            observation_schema=schema_hash,
            state_digest=state_digest,
            perspective_player_id=perspective,
            agent_to_act=agent,
            native_pending_decision_id=native_pending_id,
            observation=copy.deepcopy(dict(observation)),
            legal_actions=tuple(records),
            _argentum_action_ids=action_ids,
        )

    def resolve_for_current_observation(
        self,
        semantic_action_id: str,
        current_observation: Mapping[str, Any],
    ) -> int:
        """Resolve a durable action ID to the current Argentum ``actionId`` fail-closed."""
        current = type(self).from_observation(current_observation)
        if current.observation_schema != self.observation_schema:
            raise StaleArgentumDecisionError("Argentum schemaHash changed")
        if current.state_digest != self.state_digest:
            raise StaleArgentumDecisionError("Argentum stateDigest changed")
        if current.decision_id != self.decision_id:
            raise StaleArgentumDecisionError("Argentum decision semantics changed")
        try:
            return current._argentum_action_ids[semantic_action_id]
        except KeyError as exc:
            raise ArgentumCanonicalError(
                "Selected Commander Gym action is not legal in the current Argentum decision"
            ) from exc

    def to_training_record(
        self,
        *,
        chosen_action_id: str,
        game_id: str,
        seat: int,
        pilot: PilotProvenance,
        deck_id: str,
        deck_version: str,
        primer_version: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> DecisionRecord:
        if chosen_action_id not in self._argentum_action_ids:
            raise ArgentumCanonicalError(
                "chosen_action_id does not reference this decision's legal action set"
            )
        record_metadata = dict(metadata or {})
        record_metadata.update(
            {
                "engine": "argentum",
                "canonical_adapter": CANONICAL_ARGENTUM_VERSION,
                "argentum_state_digest": self.state_digest,
                "argentum_schema_hash": self.observation_schema,
                "argentum_perspective_player_id": self.perspective_player_id,
                "argentum_agent_to_act": self.agent_to_act,
                "argentum_pending_decision_id": self.native_pending_decision_id,
                "argentum_selected_action_id": self._argentum_action_ids[chosen_action_id],
            }
        )
        record = DecisionRecord(
            game_id=game_id,
            decision_id=self.decision_id,
            decision_type=self.decision_type,
            seat=seat,
            observation_schema=self.observation_schema,
            observation=copy.deepcopy(self.observation),
            legal_actions=list(self.legal_actions),
            chosen_action_id=chosen_action_id,
            pilot=pilot,
            deck_id=deck_id,
            deck_version=deck_version,
            primer_version=primer_version,
            metadata=record_metadata,
        )
        record.validate()
        return record
