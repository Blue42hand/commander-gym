"""OpenAI Responses adapter for the strategic side of the thin pilot contract.

The core artificial-player interface remains provider-neutral.  This module is one
replaceable strategic provider implementation suitable for use behind ``RoutingPilot``.
It deliberately keeps Argentum authoritative for observable state and legality:

* the model receives the exact seat-authorized observation except for ephemeral
  ``actionId`` / ``decisionId`` routing handles;
* enumerable choices are selected by Argentum-owned ``semanticId`` and mapped back to
  the current live ``actionId`` only after the provider returns;
* structured decisions are returned as native Argentum DecisionResponse fields, with
  the current live ``decisionId`` injected locally rather than treated as strategy;
* malformed, unknown, incomplete, refusal/error, or provider-failure results fail
  closed.  No fallback action is invented here.

The adapter uses an OpenAI-SDK-compatible ``client.responses.create`` object by duck
typing so importing Commander Gym does not require the OpenAI package.  Callers choose
the model explicitly; no provider/model choice is baked into the pilot contract.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from typing import Any, Mapping

from .pilot import ArgentumActionChoice, ArgentumDecisionChoice, PilotChoice


class OpenAIResponsesPilotError(RuntimeError):
    """Raised when the provider cannot yield one valid native Argentum choice."""


_DEFAULT_INSTRUCTIONS = """You are the strategic policy for a Magic-playing agent.
Argentum is authoritative for rules, observable state, and the currently legal action
or decision set. Use only the JSON observation provided. Return exactly one JSON object.

For an enumerable legal action, return:
{"channel":"action","semanticId":"<exact semanticId from legalActions>","params":{...}}

For a structured pending decision, return:
{"channel":"decision","response":{...}}
where response contains the native Argentum DecisionResponse fields required by the
pending decision, except decisionId. The caller injects the live routing decisionId.

Never invent a legal action, semanticId, card/entity hidden from the observation, or
routing identifier. Output JSON only.
"""


def _without_live_routing(observation: Mapping[str, Any]) -> dict[str, Any]:
    """Copy one observation while removing volatile routing handles from model input."""

    copied = deepcopy(dict(observation))

    legal = copied.get("legalActions")
    if isinstance(legal, list):
        sanitized = []
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
        pending_copy = dict(pending)
        pending_copy.pop("decisionId", None)
        copied["pendingDecision"] = pending_copy

    return copied


def _field(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _as_mapping(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    converter = getattr(value, "to_dict", None)
    if callable(converter):
        converted = converter()
        if isinstance(converted, Mapping):
            return converted
    return None


def _response_text(response: Any) -> str:
    """Read SDK ``output_text`` or the equivalent raw Responses output blocks."""

    direct = _field(response, "output_text")
    if isinstance(direct, str) and direct:
        return direct

    output = _field(response, "output")
    if isinstance(output, list):
        pieces: list[str] = []
        for item in output:
            content = _field(item, "content")
            if not isinstance(content, list):
                continue
            for part in content:
                part_type = _field(part, "type")
                if part_type == "refusal":
                    refusal = _field(part, "refusal")
                    raise OpenAIResponsesPilotError(
                        f"OpenAI provider refused pilot decision: {refusal or 'unspecified refusal'}"
                    )
                if part_type == "output_text":
                    text = _field(part, "text")
                    if isinstance(text, str):
                        pieces.append(text)
        if pieces:
            return "".join(pieces)

    raise OpenAIResponsesPilotError("OpenAI response did not contain output text")


def _provider_metadata(response: Any, model: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {"provider": "openai", "model": model}
    response_id = _field(response, "id")
    if isinstance(response_id, str) and response_id:
        metadata["responseId"] = response_id
    response_model = _field(response, "model")
    if isinstance(response_model, str) and response_model:
        metadata["responseModel"] = response_model
    usage = _as_mapping(_field(response, "usage"))
    if usage is not None:
        metadata["usage"] = dict(usage)
    return metadata


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise OpenAIResponsesPilotError(f"{label} must be a non-empty string")
    return value


@dataclass
class OpenAIResponsesPilot:
    """Concrete strategic ``ArtificialPlayer`` backed by OpenAI Responses.

    ``client`` must expose ``client.responses.create(**kwargs)``.  The adapter uses
    Responses JSON mode because native structured Argentum DecisionResponse payloads
    have decision-kind-specific fields and therefore cannot be represented by one
    closed static JSON Schema without duplicating Argentum's decision ontology here.
    Commander Gym parses and validates the returned channel locally, then the existing
    pilot/execution validators and Argentum perform authoritative validation.
    """

    client: Any
    model: str
    instructions: str = _DEFAULT_INSTRUCTIONS
    name: str = "openai-responses"
    version: str = "1"

    def __post_init__(self) -> None:
        _require_string(self.model, "OpenAI model")
        _require_string(self.instructions, "OpenAI pilot instructions")
        responses = getattr(self.client, "responses", None)
        create = getattr(responses, "create", None)
        if not callable(create):
            raise OpenAIResponsesPilotError(
                "client must expose an OpenAI-compatible responses.create method"
            )

    def choose(self, observation: Mapping[str, Any]) -> PilotChoice:
        model_observation = _without_live_routing(observation)
        request = {
            "model": self.model,
            "instructions": self.instructions,
            "input": json.dumps(
                model_observation,
                sort_keys=True,
                separators=(",", ":"),
            ),
            "text": {"format": {"type": "json_object"}},
            "store": False,
        }

        try:
            response = self.client.responses.create(**request)
        except Exception as exc:  # provider/transport errors must never become actions.
            raise OpenAIResponsesPilotError("OpenAI Responses request failed") from exc

        provider_error = _field(response, "error")
        if provider_error:
            raise OpenAIResponsesPilotError(
                f"OpenAI Responses returned an error: {provider_error}"
            )

        status = _field(response, "status")
        if isinstance(status, str) and status not in ("completed",):
            details = _field(response, "incomplete_details")
            raise OpenAIResponsesPilotError(
                f"OpenAI Responses did not complete (status={status}, details={details})"
            )

        raw = _response_text(response)
        try:
            decision = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise OpenAIResponsesPilotError("OpenAI pilot output was not valid JSON") from exc
        if not isinstance(decision, Mapping):
            raise OpenAIResponsesPilotError("OpenAI pilot output must be a JSON object")

        metadata = _provider_metadata(response, self.model)
        channel = decision.get("channel")
        if channel == "action":
            return self._action_choice(decision, observation, metadata)
        if channel == "decision":
            return self._decision_choice(decision, observation, metadata)
        raise OpenAIResponsesPilotError(
            "OpenAI pilot output channel must be 'action' or 'decision'"
        )

    def _action_choice(
        self,
        decision: Mapping[str, Any],
        observation: Mapping[str, Any],
        metadata: Mapping[str, Any],
    ) -> ArgentumActionChoice:
        semantic_id = _require_string(decision.get("semanticId"), "selected semanticId")
        params = decision.get("params", {})
        if not isinstance(params, Mapping):
            raise OpenAIResponsesPilotError("OpenAI action params must be a JSON object")

        legal = observation.get("legalActions")
        if not isinstance(legal, list):
            raise OpenAIResponsesPilotError("Argentum legalActions must be an array")
        matches = [
            action
            for action in legal
            if isinstance(action, Mapping) and action.get("semanticId") == semantic_id
        ]
        if len(matches) != 1:
            raise OpenAIResponsesPilotError(
                "selected semanticId is not exactly one current Argentum legal action"
            )
        action_id = matches[0].get("actionId")
        if type(action_id) is not int:
            raise OpenAIResponsesPilotError(
                "selected Argentum legal action is missing integer actionId"
            )
        return ArgentumActionChoice(
            action_id=action_id,
            params=dict(params),
            metadata=dict(metadata),
        )

    def _decision_choice(
        self,
        decision: Mapping[str, Any],
        observation: Mapping[str, Any],
        metadata: Mapping[str, Any],
    ) -> ArgentumDecisionChoice:
        pending = observation.get("pendingDecision")
        if not isinstance(pending, Mapping) or pending.get("requiresStructuredResponse") is not True:
            raise OpenAIResponsesPilotError(
                "provider returned structured decision when Argentum does not require one"
            )
        decision_id = _require_string(
            pending.get("decisionId"),
            "pending Argentum decisionId",
        )
        response = decision.get("response")
        if not isinstance(response, Mapping) or not response:
            raise OpenAIResponsesPilotError(
                "OpenAI structured decision response must be a non-empty JSON object"
            )
        if "decisionId" in response:
            raise OpenAIResponsesPilotError(
                "model-facing structured response must not invent live decisionId routing"
            )
        _require_string(response.get("type"), "structured response type")

        submitted = dict(response)
        submitted["decisionId"] = decision_id
        return ArgentumDecisionChoice(response=submitted, metadata=dict(metadata))
