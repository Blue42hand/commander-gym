"""Experimental sparse ML features derived from durable Commander Gym records.

This module is deliberately an offline research representation, not an Argentum
observation format or action ontology. It consumes only the seat-authorized
observation and legal-action records already captured in :class:`DecisionRecord`.
Outcome, pilot, deck, and free-form metadata are labels/provenance and never model
input.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Dict, Iterable, Mapping, Tuple

from .records import DecisionRecord

FEATURE_SCHEMA_VERSION = 1
DEFAULT_FEATURE_SPACE = 2_000_000


def policy_family_for_decision_type(decision_type: str) -> str:
    """Map a recorded decision type to a coarse experimental policy-head family.

    This is a learning-layer grouping only. It does not define execution semantics,
    legality, or Argentum decision identity. Unknown decision types remain usable via
    the ``other`` family rather than requiring a fixed Commander Gym action vocabulary.
    """

    normalized = decision_type.strip().lower().replace("-", "_").replace(" ", "_")
    if "mulligan" in normalized:
        return "mulligan"
    if any(term in normalized for term in ("attack", "block", "combat")):
        return "combat"
    if any(term in normalized for term in ("mana", "payment", "pay_cost", "cost_payment")):
        return "payment"
    if any(term in normalized for term in ("order", "ordering", "trigger_order", "sequence_order")):
        return "ordering"
    if any(term in normalized for term in ("yes_no", "confirm", "optional", "binary")):
        return "binary"
    if any(term in normalized for term in ("target", "select", "selection", "choose", "entity", "player_choice")):
        return "selection"
    if any(term in normalized for term in ("priority", "action", "cast", "activate", "play")):
        return "priority"
    return "other"


def _path_component(value: Any) -> str:
    return json.dumps(str(value), ensure_ascii=False, separators=(",", ":"))


def _feature_tokens(value: Any, path: str) -> Iterable[str]:
    """Yield deterministic hierarchical tokens for JSON-shaped recorded data."""

    if isinstance(value, Mapping):
        yield f"{path}:object"
        for key in sorted(value, key=lambda item: str(item)):
            child = f"{path}.{_path_component(key)}"
            yield from _feature_tokens(value[key], child)
        return

    if isinstance(value, (list, tuple)):
        yield f"{path}:array:{len(value)}"
        for index, item in enumerate(value):
            yield from _feature_tokens(item, f"{path}[{index}]")
        return

    if value is None or isinstance(value, (str, int, float, bool)):
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        yield f"{path}={encoded}"
        return

    raise TypeError(f"unsupported feature value at {path}: {type(value).__name__}")


def _hash_token(token: str, feature_space: int) -> int:
    digest = sha256(token.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % feature_space


def _hash_tokens(tokens: Iterable[str], feature_space: int) -> Tuple[Tuple[int, ...], int, int]:
    if feature_space <= 0:
        raise ValueError("feature_space must be positive")
    unique_tokens = sorted(set(tokens))
    hashed = [_hash_token(token, feature_space) for token in unique_tokens]
    indices = tuple(sorted(set(hashed)))
    collisions = len(unique_tokens) - len(indices)
    return indices, len(unique_tokens), collisions


@dataclass(frozen=True)
class SparseDecisionFeatures:
    """Hashed state/candidate-action features for one recorded decision."""

    decision_id: str
    policy_family: str
    state_indices: Tuple[int, ...]
    action_indices: Dict[str, Tuple[int, ...]]
    state_token_count: int
    state_collision_count: int
    feature_space: int = DEFAULT_FEATURE_SPACE
    schema_version: int = FEATURE_SCHEMA_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "decision_id": self.decision_id,
            "policy_family": self.policy_family,
            "feature_space": self.feature_space,
            "state_indices": list(self.state_indices),
            "action_indices": {
                action_id: list(self.action_indices[action_id])
                for action_id in sorted(self.action_indices)
            },
            "state_token_count": self.state_token_count,
            "state_collision_count": self.state_collision_count,
        }


def encode_decision_record(
    record: DecisionRecord,
    *,
    feature_space: int = DEFAULT_FEATURE_SPACE,
) -> SparseDecisionFeatures:
    """Encode a validated ``DecisionRecord`` without post-decision leakage.

    State features use the recorded decision type, observation schema, authorized
    observation, and stable identities already present for all legal actions.
    Candidate-action features use each action record's identity, optional label, and
    payload. This function does not derive a competing action identity or inspect
    Argentum engine state.

    The chosen action, outcome, pilot identity, deck identity, and free-form metadata
    are deliberately excluded from the input vector.
    """

    record.validate()
    if feature_space <= 0:
        raise ValueError("feature_space must be positive")

    state_tokens = [
        f"decision_type={record.decision_type}",
        f"observation_schema={record.observation_schema}",
    ]
    state_tokens.extend(_feature_tokens(record.observation, "observation"))
    for action in sorted(record.legal_actions, key=lambda item: item.action_id):
        state_tokens.append(f"legal_action={action.action_id}")

    state_indices, token_count, collision_count = _hash_tokens(state_tokens, feature_space)

    action_indices: Dict[str, Tuple[int, ...]] = {}
    for action in record.legal_actions:
        action_tokens = [f"action_id={action.action_id}"]
        if action.label is not None:
            action_tokens.append(f"label={action.label}")
        action_tokens.extend(_feature_tokens(action.payload, "payload"))
        indices, _, _ = _hash_tokens(action_tokens, feature_space)
        action_indices[action.action_id] = indices

    return SparseDecisionFeatures(
        decision_id=record.decision_id,
        policy_family=policy_family_for_decision_type(record.decision_type),
        state_indices=state_indices,
        action_indices=action_indices,
        state_token_count=token_count,
        state_collision_count=collision_count,
        feature_space=feature_space,
    )
