"""Small, dependency-free local decision model used to qualify the #73 runtime seam.

This is intentionally not a claim about long-term Magic model quality.  It is a real
locally executable numeric model backend for the provider-neutral ``LocalModelSubsystem``
introduced by #110.  The model scores the currently legal semantic actions with a
versioned linear policy over seat-visible action features and returns exactly one semantic
choice.  Unsupported decision families remain the router's responsibility.

The implementation uses only the Python standard library so the foundation proof does
not commit Commander Gym to a model vendor, serving runtime, accelerator, or hardware
stack.  A later Magic-specialized local generalist can replace this backend without
changing the composed Pilot or Argentum-facing contracts.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping

from .pilot import PilotContractError

LINEAR_ACTION_MODEL_SCHEMA = "commander-gym-linear-action-model-v1"
_ALLOWED_DENSE_FEATURES = frozenset(
    {
        "affordable",
        "validAttackerCount",
        "validAttackTargetCount",
    }
)


def _finite_number(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PilotContractError(f"{field} must be a finite number")
    result = float(value)
    if result != result or result in (float("inf"), float("-inf")):
        raise PilotContractError(f"{field} must be a finite number")
    return result


def _non_empty_string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise PilotContractError(f"{field} must be a non-empty string")
    return value


@dataclass(frozen=True)
class LinearActionModel:
    """Immutable portable linear scorer for semantic Argentum actions.

    Weight names are deliberately explicit and versioned.  Supported features are:

    - ``kind:<ArgentumActionKind>`` one-hot features;
    - ``semantic:<semanticId>`` one-hot features;
    - ``affordable`` (1.0 only when Argentum says the action is affordable);
    - ``validAttackerCount``;
    - ``validAttackTargetCount``.

    The model is not allowed to inspect live ``actionId`` routing handles; #110 removes
    them before this backend is invoked.
    """

    model_id: str
    version: str
    bias: float
    weights: tuple[tuple[str, float], ...]
    schema: str = LINEAR_ACTION_MODEL_SCHEMA

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "LinearActionModel":
        if not isinstance(value, Mapping):
            raise PilotContractError("linear local model must be a mapping")
        schema = value.get("schema", LINEAR_ACTION_MODEL_SCHEMA)
        if schema != LINEAR_ACTION_MODEL_SCHEMA:
            raise PilotContractError(
                f"unsupported linear local model schema {schema!r}; "
                f"expected {LINEAR_ACTION_MODEL_SCHEMA!r}"
            )
        model_id = _non_empty_string(value.get("modelId"), field="modelId")
        version = _non_empty_string(value.get("version"), field="version")
        bias = _finite_number(value.get("bias", 0.0), field="bias")
        raw_weights = value.get("weights")
        if not isinstance(raw_weights, Mapping) or not raw_weights:
            raise PilotContractError("linear local model weights must be a non-empty mapping")

        weights: list[tuple[str, float]] = []
        for raw_name, raw_weight in raw_weights.items():
            name = _non_empty_string(raw_name, field="weights key")
            if not (
                name in _ALLOWED_DENSE_FEATURES
                or name.startswith("kind:")
                or name.startswith("semantic:")
            ):
                raise PilotContractError(f"unsupported linear local model feature {name!r}")
            if name.startswith("kind:") and not name.removeprefix("kind:"):
                raise PilotContractError("kind feature requires an action kind")
            if name.startswith("semantic:") and not name.removeprefix("semantic:"):
                raise PilotContractError("semantic feature requires a semanticId")
            weights.append(
                (name, _finite_number(raw_weight, field=f"weights[{name!r}]"))
            )

        weights.sort(key=lambda item: item[0])
        return cls(
            model_id=model_id,
            version=version,
            bias=bias,
            weights=tuple(weights),
            schema=schema,
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "modelId": self.model_id,
            "version": self.version,
            "bias": self.bias,
            "weights": {name: weight for name, weight in self.weights},
        }

    @property
    def digest(self) -> str:
        payload = json.dumps(
            self.to_mapping(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class LinearActionModelBackend:
    """Concrete in-process local inference backend for ``LocalModelSubsystem``."""

    model: LinearActionModel

    @property
    def name(self) -> str:
        return self.model.model_id

    @property
    def version(self) -> str:
        return self.model.version

    @property
    def digest(self) -> str:
        return self.model.digest

    def _features(self, action: Mapping[str, Any]) -> dict[str, float]:
        kind = _non_empty_string(action.get("kind"), field="legal action kind")
        semantic_id = _non_empty_string(
            action.get("semanticId"), field="legal action semanticId"
        )
        if "actionId" in action:
            raise PilotContractError("local linear model input must not contain actionId")

        valid_attackers = action.get("validAttackers", [])
        if not isinstance(valid_attackers, list):
            raise PilotContractError("validAttackers must be an array when present")
        valid_targets = action.get("validAttackTargets", [])
        if not isinstance(valid_targets, list):
            raise PilotContractError("validAttackTargets must be an array when present")

        return {
            f"kind:{kind}": 1.0,
            f"semantic:{semantic_id}": 1.0,
            "affordable": 1.0 if action.get("affordable") is True else 0.0,
            "validAttackerCount": float(len(valid_attackers)),
            "validAttackTargetCount": float(len(valid_targets)),
        }

    def _score(self, action: Mapping[str, Any]) -> float:
        features = self._features(action)
        return self.model.bias + sum(
            weight * features.get(name, 0.0) for name, weight in self.model.weights
        )

    def infer(self, observation: Mapping[str, Any]) -> Mapping[str, Any]:
        if not isinstance(observation, Mapping):
            raise PilotContractError("local linear model observation must be a mapping")
        legal = observation.get("legalActions")
        if not isinstance(legal, list) or not legal:
            raise PilotContractError("local linear model requires non-empty legalActions")

        scored: list[tuple[float, str]] = []
        for index, action in enumerate(legal):
            if not isinstance(action, Mapping):
                raise PilotContractError(f"legalActions[{index}] must be a mapping")
            semantic_id = _non_empty_string(
                action.get("semanticId"), field=f"legalActions[{index}].semanticId"
            )
            scored.append((self._score(action), semantic_id))

        best_score = max(score for score, _ in scored)
        best = [semantic_id for score, semantic_id in scored if score == best_score]
        if len(best) != 1:
            raise PilotContractError(
                "local linear model produced a non-unique best legal action"
            )
        return {
            "channel": "action",
            "semanticId": best[0],
            "params": {},
        }
