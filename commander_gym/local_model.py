"""Provider-neutral bounded local-model adapter for composed Pilots.

The foundation router already places ``local_generalist`` immediately before
``frontier_escalation``.  This module supplies the smallest generic executable adapter
for that role without choosing a model vendor, runtime, or hardware target.

A local model is allowed to handle only an explicit, versioned decision scope.  The
backend receives the exact seat-visible Argentum observation with only volatile routing
handles removed.  It returns semantic action identity or a native structured response;
the adapter maps semantic identity back to the current live Argentum handle locally.
Unsupported families and declared backend unavailability defer explicitly so the
composed router can escalate.  Malformed/illegal model output fails closed rather than
being converted into a fallback gameplay choice.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from .pilot import (
    ArgentumActionChoice,
    ArgentumDecisionChoice,
    PilotChoice,
    PilotContractError,
)
from .pilot_composition import SubsystemDecision

LOCAL_MODEL_ADAPTER_VERSION = "1"


class LocalModelUnavailableError(RuntimeError):
    """Expected operational unavailability that may safely escalate to the next route."""


class LocalModelBackend(Protocol):
    """Replaceable local inference backend.

    ``infer`` receives a seat-safe observation with live ``actionId``/``decisionId``
    removed and returns one provider-neutral selection object:

    * ``{"channel":"action","semanticId":"...","params":{...}}``; or
    * ``{"channel":"decision","response":{...}}``.

    The backend may be an in-process model, localhost service, accelerator runtime, or
    test fixture.  None of those choices are part of the public Pilot contract.
    """

    name: str
    version: str

    def infer(self, observation: Mapping[str, Any]) -> Mapping[str, Any]:
        ...


@dataclass(frozen=True)
class LocalModelScope:
    """Explicit decision families one local-model revision is certified to attempt.

    Ordinary-action scope is conservative: every currently legal action kind must be
    present in ``action_kinds``.  This prevents a model certified for a narrow action
    vocabulary from silently taking a decision that also contains an unfamiliar action
    family.  Structured decisions are matched by Argentum's authoritative ``kind``.
    """

    action_kinds: tuple[str, ...] = ()
    structured_decision_kinds: tuple[str, ...] = ()
    version: str = "1"

    def __post_init__(self) -> None:
        if not isinstance(self.version, str) or not self.version:
            raise PilotContractError("local-model scope version must be a non-empty string")
        if not self.action_kinds and not self.structured_decision_kinds:
            raise PilotContractError("local-model scope must certify at least one decision family")
        for label, values in (
            ("action_kinds", self.action_kinds),
            ("structured_decision_kinds", self.structured_decision_kinds),
        ):
            if not isinstance(values, tuple) or any(
                not isinstance(value, str) or not value for value in values
            ):
                raise PilotContractError(f"local-model {label} must contain non-empty strings")
            if len(values) != len(set(values)):
                raise PilotContractError(f"local-model {label} must not contain duplicates")

    def classify(self, observation: Mapping[str, Any]) -> str | None:
        pending = observation.get("pendingDecision")
        if isinstance(pending, Mapping) and pending.get("requiresStructuredResponse") is True:
            kind = pending.get("kind")
            if isinstance(kind, str) and kind in set(self.structured_decision_kinds):
                return f"structured:{kind}"
            return None

        legal = observation.get("legalActions")
        if not isinstance(legal, list) or not legal:
            return None
        kinds: list[str] = []
        for action in legal:
            if not isinstance(action, Mapping):
                return None
            kind = action.get("kind")
            if not isinstance(kind, str) or not kind:
                return None
            kinds.append(kind)
        observed = set(kinds)
        if not observed.issubset(set(self.action_kinds)):
            return None
        return "actions:" + ",".join(sorted(observed))

    def provenance(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "actionKinds": list(self.action_kinds),
            "structuredDecisionKinds": list(self.structured_decision_kinds),
        }


def seat_safe_local_model_input(observation: Mapping[str, Any]) -> dict[str, Any]:
    """Copy one seat-visible observation while removing only live routing handles."""

    if not isinstance(observation, Mapping):
        raise PilotContractError("local model observation must be a mapping")
    copied = deepcopy(dict(observation))

    legal = copied.get("legalActions")
    if isinstance(legal, list):
        sanitized: list[Any] = []
        for action in legal:
            if isinstance(action, Mapping):
                item = dict(action)
                item.pop("actionId", None)
                sanitized.append(item)
            else:
                sanitized.append(action)
        copied["legalActions"] = sanitized

    pending = copied.get("pendingDecision")
    if isinstance(pending, Mapping):
        item = dict(pending)
        item.pop("decisionId", None)
        copied["pendingDecision"] = item

    return copied


def _selection_to_choice(
    selection: Mapping[str, Any],
    observation: Mapping[str, Any],
    *,
    metadata: Mapping[str, Any],
) -> PilotChoice:
    if not isinstance(selection, Mapping):
        raise PilotContractError("local model output must be a mapping")

    channel = selection.get("channel")
    pending = observation.get("pendingDecision")
    requires_structured = bool(
        isinstance(pending, Mapping) and pending.get("requiresStructuredResponse") is True
    )

    if channel == "action":
        if requires_structured:
            raise PilotContractError(
                "local model returned action channel for a structured Argentum decision"
            )
        semantic_id = selection.get("semanticId")
        if not isinstance(semantic_id, str) or not semantic_id:
            raise PilotContractError("local model action output requires semanticId")
        legal = observation.get("legalActions")
        if not isinstance(legal, list):
            raise PilotContractError("Argentum legalActions must be an array")
        matches = [
            action
            for action in legal
            if isinstance(action, Mapping) and action.get("semanticId") == semantic_id
        ]
        if len(matches) != 1 or type(matches[0].get("actionId")) is not int:
            raise PilotContractError(
                "local model semanticId did not identify exactly one current legal action"
            )
        params = selection.get("params", {})
        if not isinstance(params, Mapping):
            raise PilotContractError("local model action params must be a mapping")
        return ArgentumActionChoice(
            action_id=matches[0]["actionId"],
            params=dict(params),
            metadata=dict(metadata),
        )

    if channel == "decision":
        if not requires_structured or not isinstance(pending, Mapping):
            raise PilotContractError(
                "local model returned decision channel without a structured Argentum decision"
            )
        response = selection.get("response")
        if not isinstance(response, Mapping) or not response:
            raise PilotContractError("local model structured response must be a non-empty mapping")
        if "decisionId" in response:
            raise PilotContractError("local model must not invent live decisionId routing handles")
        decision_id = pending.get("decisionId")
        if not isinstance(decision_id, str) or not decision_id:
            raise PilotContractError("structured Argentum decision must carry decisionId")
        return ArgentumDecisionChoice(
            response={**dict(response), "decisionId": decision_id},
            metadata=dict(metadata),
        )

    raise PilotContractError("local model output channel must be 'action' or 'decision'")


@dataclass(frozen=True)
class LocalModelSubsystem:
    """Conditional ``local_generalist`` component for the composed Pilot router.

    Scope misses and explicit backend unavailability defer to later subsystems. Invalid
    model output raises ``PilotContractError`` and therefore fails closed: the router
    must never reinterpret malformed output as evidence that frontier escalation is
    strategically preferred.
    """

    backend: LocalModelBackend
    scope: LocalModelScope
    name: str = "bounded-local-model"
    version: str = LOCAL_MODEL_ADAPTER_VERSION

    def __post_init__(self) -> None:
        backend_name = getattr(self.backend, "name", None)
        backend_version = getattr(self.backend, "version", None)
        if not isinstance(backend_name, str) or not backend_name:
            raise PilotContractError("local-model backend must expose non-empty name")
        if not isinstance(backend_version, str) or not backend_version:
            raise PilotContractError("local-model backend must expose non-empty version")

    def try_choose(self, observation: Mapping[str, Any]) -> SubsystemDecision:
        family = self.scope.classify(observation)
        base_metadata = {
            "scope": self.scope.provenance(),
            "backend": {
                "name": self.backend.name,
                "version": self.backend.version,
            },
        }
        if family is None:
            return SubsystemDecision(
                reason="outside-certified-local-model-scope",
                metadata={**base_metadata, "matched": False},
            )

        model_input = seat_safe_local_model_input(observation)
        try:
            selection = self.backend.infer(model_input)
        except LocalModelUnavailableError as exc:
            return SubsystemDecision(
                reason="local-model-unavailable",
                metadata={
                    **base_metadata,
                    "matched": True,
                    "family": family,
                    "errorType": type(exc).__name__,
                },
            )

        choice_metadata = {
            "localModel": {
                "adapterVersion": self.version,
                "backend": self.backend.name,
                "backendVersion": self.backend.version,
                "scopeVersion": self.scope.version,
                "family": family,
            }
        }
        choice = _selection_to_choice(
            selection,
            observation,
            metadata=choice_metadata,
        )
        return SubsystemDecision(
            choice=choice,
            metadata={
                **base_metadata,
                "matched": True,
                "family": family,
            },
        )
