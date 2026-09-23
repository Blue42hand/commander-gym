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

The only supported params fields are native Argentum ActionParams:
- attackers: object mapping attacker entity id to attacked player/permanent entity id;
- blockers: object mapping blocker entity id to an array of attacker entity ids;
- targets: array of target entity ids;
- xValue: integer.
Do not use attackerIds, attackTargetId, or other substitute field names.

For a structured pending decision, return:
{"channel":"decision","response":{...}}
where response contains the native Argentum DecisionResponse fields required by the
pending decision, except decisionId. The caller injects the live routing decisionId.

Never invent a legal action, semanticId, card/entity hidden from the observation, or
routing identifier. Output JSON only.

Play to win and make concrete progress toward a terminal result. Do not pass merely
because passing is legal when a useful legal land play, spell, ability, or combat action
advances the game. Preserve interaction when the observation gives a concrete tactical
reason, not as a default excuse to stall.
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
        pending_copy.pop("id", None)
        if pending_copy.get("requiresStructuredResponse") is True:
            spec = _structured_response_spec(pending_copy)
            if spec is not None:
                response_type, required_fields = spec
                pending_copy["responseSpec"] = {
                    "type": response_type,
                    "requiredFields": required_fields,
                }
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


def _provider_failure_summary(exc: Exception) -> str:
    """Return bounded provider diagnostics without serializing the request or key."""

    parts = [type(exc).__name__]
    status_code = getattr(exc, "status_code", None)
    if type(status_code) is int:
        parts.append(f"status={status_code}")
    body = getattr(exc, "body", None)
    if isinstance(body, Mapping):
        error = body.get("error", body)
        if isinstance(error, Mapping):
            code = error.get("code")
            if isinstance(code, str) and code:
                parts.append(f"code={code}")
            message = error.get("message")
            if isinstance(message, str) and message:
                parts.append(f"message={message[:500]}")
    return ", ".join(parts)


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise OpenAIResponsesPilotError(f"{label} must be a non-empty string")
    return value


# Native Argentum PendingDecision -> DecisionResponse wire contract. These are schema hints and
# validation only: Argentum remains authoritative for whether the selected values are legal.
# Gym-style enum kinds are accepted alongside native game-server serializer names so the same
# strategic pilot can serve both transports.
_STRUCTURED_RESPONSE_SPECS: dict[str, tuple[str, dict[str, str]]] = {
    "ChooseTargetsDecision": ("TargetsResponse", {"selectedTargets": "object"}),
    "CHOOSE_TARGETS": ("TargetsResponse", {"selectedTargets": "object"}),
    "SelectCardsDecision": ("CardsSelectedResponse", {"selectedCards": "array"}),
    "SELECT_CARDS": ("CardsSelectedResponse", {"selectedCards": "array"}),
    "YesNoDecision": ("YesNoResponse", {"choice": "boolean"}),
    "YES_NO": ("YesNoResponse", {"choice": "boolean"}),
    "BatchYesNoDecision": (
        "BatchYesNoResponse",
        {"choice": "boolean", "applyToAll": "boolean"},
    ),
    "ChooseModeDecision": ("ModesChosenResponse", {"selectedModes": "array"}),
    "CHOOSE_MODE": ("ModesChosenResponse", {"selectedModes": "array"}),
    "ChooseColorDecision": ("ColorChosenResponse", {"color": "string"}),
    "CHOOSE_COLOR": ("ColorChosenResponse", {"color": "string"}),
    "ChooseNumberDecision": ("NumberChosenResponse", {"number": "integer"}),
    "CHOOSE_NUMBER": ("NumberChosenResponse", {"number": "integer"}),
    "DistributeDecision": ("DistributionResponse", {"distribution": "object"}),
    "DISTRIBUTE": ("DistributionResponse", {"distribution": "object"}),
    "OrderObjectsDecision": ("OrderedResponse", {"orderedObjects": "array"}),
    "ORDER_OBJECTS": ("OrderedResponse", {"orderedObjects": "array"}),
    "SplitPilesDecision": ("PilesSplitResponse", {"piles": "array"}),
    "SPLIT_PILES": ("PilesSplitResponse", {"piles": "array"}),
    "ChooseOptionDecision": ("OptionChosenResponse", {"optionIndex": "integer"}),
    "CHOOSE_OPTION": ("OptionChosenResponse", {"optionIndex": "integer"}),
    "ChooseReplacementDecision": (
        "ReplacementChosenResponse",
        {"fromIndex": "integer", "toIndex": "integer"},
    ),
    "CHOOSE_REPLACEMENT": (
        "ReplacementChosenResponse",
        {"fromIndex": "integer", "toIndex": "integer"},
    ),
    "BudgetModalDecision": ("BudgetModalResponse", {"selectedModeIndices": "array"}),
    "BUDGET_MODAL": ("BudgetModalResponse", {"selectedModeIndices": "array"}),
    "AssignDamageDecision": ("DamageAssignmentResponse", {"assignments": "object"}),
    "ASSIGN_DAMAGE": ("DamageAssignmentResponse", {"assignments": "object"}),
    "SearchLibraryDecision": ("CardsSelectedResponse", {"selectedCards": "array"}),
    "SEARCH_LIBRARY": ("CardsSelectedResponse", {"selectedCards": "array"}),
    "ReorderLibraryDecision": ("OrderedResponse", {"orderedObjects": "array"}),
    "REORDER_LIBRARY": ("OrderedResponse", {"orderedObjects": "array"}),
    "SelectManaSourcesDecision": (
        "ManaSourcesSelectedResponse",
        {
            "selectedSources": "array",
            "autoPay": "boolean",
            "waterbendPermanents": "array",
            "declined": "boolean",
        },
    ),
    "SELECT_MANA_SOURCES": (
        "ManaSourcesSelectedResponse",
        {
            "selectedSources": "array",
            "autoPay": "boolean",
            "waterbendPermanents": "array",
            "declined": "boolean",
        },
    ),
    "CombatResolutionDecision": ("CombatResolutionResponse", {"edges": "array"}),
    "COMBAT_RESOLUTION": ("CombatResolutionResponse", {"edges": "array"}),
}


_ENGINE_FIELD_KIND_TO_WIRE_TYPE = {
    "BOOLEAN": "boolean",
    "INTEGER": "integer",
    "STRING": "string",
    "ENTITY_ID_ARRAY": "array",
    "INTEGER_ARRAY": "array",
    "ENTITY_ID_ARRAY_ARRAY": "array",
    "MAP": "object",
    "DAMAGE_EDGE_AMOUNT_ARRAY": "array",
}


def _structured_response_spec(
    pending: Mapping[str, Any],
) -> tuple[str, dict[str, str]] | None:
    explicit = pending.get("responseSpec")
    if isinstance(explicit, Mapping):
        response_type = explicit.get("responseType")
        fields = explicit.get("requiredFields")
        if isinstance(response_type, str) and response_type and isinstance(fields, Mapping):
            converted: dict[str, str] = {}
            for field_name, field_kind in fields.items():
                if not isinstance(field_name, str) or not isinstance(field_kind, str):
                    return None
                wire_type = _ENGINE_FIELD_KIND_TO_WIRE_TYPE.get(field_kind)
                if wire_type is None:
                    return None
                converted[field_name] = wire_type
            return response_type, converted

    # Compatibility fallback for Gym observations and older Argentum game-server builds that
    # predate PendingDecision.responseSpec(). The normal game-server path should supply the
    # Argentum-owned responseSpec once that generic contract is available.
    for key in ("type", "kind"):
        value = pending.get(key)
        if isinstance(value, str) and value in _STRUCTURED_RESPONSE_SPECS:
            return _STRUCTURED_RESPONSE_SPECS[value]
    return None


def _decision_cancel_allowed(pending: Mapping[str, Any]) -> bool:
    explicit = pending.get("responseSpec")
    if isinstance(explicit, Mapping) and explicit.get("cancelAllowed") is True:
        return True
    return pending.get("canCancel") is True


def _matches_wire_type(value: Any, wire_type: str) -> bool:
    if wire_type == "boolean":
        return type(value) is bool
    if wire_type == "integer":
        return type(value) is int
    if wire_type == "string":
        return isinstance(value, str)
    if wire_type == "array":
        return isinstance(value, list)
    if wire_type == "object":
        return isinstance(value, Mapping)
    return False


def _json_schema_for_wire_type(field_name: str, wire_type: str) -> dict[str, Any]:
    if wire_type == "boolean":
        return {"type": "boolean"}
    if wire_type == "integer":
        return {"type": "integer"}
    if wire_type == "string":
        return {"type": "string"}
    if wire_type == "array":
        integer_arrays = {"selectedModes", "selectedModeIndices"}
        nested_string_arrays = {"piles"}
        if field_name in integer_arrays:
            return {"type": "array", "items": {"type": "integer"}}
        if field_name in nested_string_arrays:
            return {
                "type": "array",
                "items": {"type": "array", "items": {"type": "string"}},
            }
        if field_name == "edges":
            return {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "edgeId": {"type": "string"},
                        "amount": {"type": "integer"},
                    },
                    "required": ["edgeId", "amount"],
                    "additionalProperties": False,
                },
            }
        return {"type": "array", "items": {"type": "string"}}
    if wire_type == "object":
        # Native response maps are keyed by live entity / requirement ids. Keep their values
        # unconstrained here and let Commander Gym + Argentum validate the exact native payload.
        return {"type": "object"}
    return {}


def _response_format_for_observation(observation: Mapping[str, Any]) -> dict[str, Any]:
    """Build a request-local Structured Outputs schema from the current Argentum choices."""

    pending = observation.get("pendingDecision")
    if isinstance(pending, Mapping) and pending.get("requiresStructuredResponse") is True:
        spec = _structured_response_spec(pending)
        if spec is not None:
            response_type, required_fields = spec
            properties: dict[str, Any] = {
                "type": {"type": "string", "const": response_type},
            }
            for field_name, wire_type in required_fields.items():
                properties[field_name] = _json_schema_for_wire_type(field_name, wire_type)
            primary_response_schema: dict[str, Any] = {
                "type": "object",
                "properties": properties,
                "required": ["type", *required_fields.keys()],
                "additionalProperties": False,
            }
            response_schema: dict[str, Any] = primary_response_schema
            can_cancel = _decision_cancel_allowed(pending)
            if can_cancel:
                response_schema = {
                    "anyOf": [
                        primary_response_schema,
                        {
                            "type": "object",
                            "properties": {
                                "type": {"type": "string", "const": "CancelDecisionResponse"},
                            },
                            "required": ["type"],
                            "additionalProperties": False,
                        },
                    ]
                }
            # Arbitrary-key native maps and response unions stay non-strict; Commander Gym and
            # Argentum still validate them fail-closed after generation.
            # without enumerating live entity ids as property names. Use schema guidance but keep
            # local/native validation authoritative for those response classes.
            strict = (
                not can_cancel
                and all(wire_type != "object" for wire_type in required_fields.values())
            )
            return {
                "type": "json_schema",
                "name": "commander_gym_decision",
                "strict": strict,
                "schema": {
                    "type": "object",
                    "properties": {
                        "channel": {"type": "string", "const": "decision"},
                        "response": response_schema,
                    },
                    "required": ["channel", "response"],
                    "additionalProperties": False,
                },
            }

    legal = observation.get("legalActions")
    semantic_ids = [
        action.get("semanticId")
        for action in legal
        if isinstance(action, Mapping)
        and isinstance(action.get("semanticId"), str)
        and action.get("semanticId")
    ] if isinstance(legal, list) else []
    semantic_schema: dict[str, Any] = {"type": "string"}
    if semantic_ids:
        semantic_schema["enum"] = semantic_ids

    return {
        "type": "json_schema",
        "name": "commander_gym_action",
        "strict": False,
        "schema": {
            "type": "object",
            "properties": {
                "channel": {"type": "string", "const": "action"},
                "semanticId": semantic_schema,
                "params": {
                    "type": "object",
                    "properties": {
                        "attackers": {"type": "object"},
                        "blockers": {"type": "object"},
                        "targets": {"type": "array", "items": {"type": "string"}},
                        "xValue": {"type": "integer"},
                    },
                    "additionalProperties": False,
                },
            },
            "required": ["channel", "semanticId", "params"],
            "additionalProperties": False,
        },
    }


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
    strategy: str | None = None
    max_attempts: int = 2
    name: str = "openai-responses"
    version: str = "1"

    def __post_init__(self) -> None:
        _require_string(self.model, "OpenAI model")
        _require_string(self.instructions, "OpenAI pilot instructions")
        if self.strategy is not None:
            _require_string(self.strategy, "OpenAI pilot strategy")
        if type(self.max_attempts) is not int or self.max_attempts < 1:
            raise OpenAIResponsesPilotError("max_attempts must be a positive integer")
        responses = getattr(self.client, "responses", None)
        create = getattr(responses, "create", None)
        if not callable(create):
            raise OpenAIResponsesPilotError(
                "client must expose an OpenAI-compatible responses.create method"
            )

    def choose(self, observation: Mapping[str, Any]) -> PilotChoice:
        model_observation = _without_live_routing(observation)
        base_input = "Return one JSON object for this observation:\n" + json.dumps(
            model_observation,
            sort_keys=True,
            separators=(",", ":"),
        )
        request = {
            "model": self.model,
            "instructions": self.instructions
            + (f"\n\nRun-specific strategy:\n{self.strategy}" if self.strategy else ""),
            "input": base_input,
            "text": {"format": _response_format_for_observation(observation)},
            "store": False,
        }

        validation_error: OpenAIResponsesPilotError | None = None
        for attempt in range(self.max_attempts):
            if validation_error is not None:
                request["input"] = (
                    base_input
                    + "\nThe previous response was invalid: "
                    + str(validation_error)
                    + "\nReturn a corrected JSON object using only the current observation."
                )
            try:
                response = self.client.responses.create(**request)
            except Exception as exc:  # Provider SDK owns transport-level retries.
                raise OpenAIResponsesPilotError(
                    f"OpenAI Responses request failed ({_provider_failure_summary(exc)})"
                ) from exc

            try:
                return self._choice_from_response(
                    response,
                    observation,
                    retry_count=attempt,
                )
            except OpenAIResponsesPilotError as exc:
                validation_error = exc

        assert validation_error is not None
        raise OpenAIResponsesPilotError(
            f"OpenAI pilot exhausted {self.max_attempts} validation attempts: "
            f"{validation_error}"
        ) from validation_error

    def _choice_from_response(
        self,
        response: Any,
        observation: Mapping[str, Any],
        *,
        retry_count: int,
    ) -> PilotChoice:
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
        metadata["retryCount"] = retry_count
        channel = decision.get("channel")
        if channel == "action":
            return self._action_choice(decision, observation, metadata)
        if channel == "decision":
            return self._decision_choice(decision, observation, metadata)
        raise OpenAIResponsesPilotError(
            "OpenAI pilot output channel must be 'action' or 'decision'"
        )

    @staticmethod
    def _validate_action_params(
        params: Mapping[str, Any],
        selected: Mapping[str, Any],
    ) -> dict[str, Any]:
        allowed = {"attackers", "blockers", "targets", "xValue"}
        unexpected = set(params) - allowed
        if unexpected:
            raise OpenAIResponsesPilotError(
                "OpenAI action params contain unsupported field(s): "
                + ", ".join(sorted(unexpected))
            )

        normalized = dict(params)
        attackers = normalized.get("attackers")
        if attackers is not None and (
            not isinstance(attackers, Mapping)
            or any(not isinstance(k, str) or not isinstance(v, str) for k, v in attackers.items())
        ):
            raise OpenAIResponsesPilotError("ActionParams.attackers must map entity ids to entity ids")

        blockers = normalized.get("blockers")
        if blockers is not None:
            if not isinstance(blockers, Mapping):
                raise OpenAIResponsesPilotError(
                    "ActionParams.blockers must map blocker ids to attacker-id arrays"
                )
            for key, value in blockers.items():
                if (
                    not isinstance(key, str)
                    or not isinstance(value, list)
                    or any(not isinstance(item, str) for item in value)
                ):
                    raise OpenAIResponsesPilotError(
                        "ActionParams.blockers must map blocker ids to attacker-id arrays"
                    )

        targets = normalized.get("targets")
        if targets is not None and (
            not isinstance(targets, list)
            or any(not isinstance(item, str) for item in targets)
        ):
            raise OpenAIResponsesPilotError("ActionParams.targets must be an entity-id array")

        x_value = normalized.get("xValue")
        if x_value is not None and type(x_value) is not int:
            raise OpenAIResponsesPilotError("ActionParams.xValue must be an integer")

        kind = selected.get("kind") or selected.get("actionType")
        populated = {
            key
            for key, value in normalized.items()
            if value not in (None, {}, [])
        }
        permitted_by_kind = {
            "DeclareAttackers": {"attackers"},
            "DeclareBlockers": {"blockers"},
            "CastSpell": {"targets", "xValue"},
            "ActivateAbility": {"targets", "xValue"},
        }
        permitted = permitted_by_kind.get(kind, set())
        unusable = populated - permitted
        if unusable:
            raise OpenAIResponsesPilotError(
                f"ActionParams {sorted(unusable)} are not applicable to {kind}"
            )

        if "attackers" in populated:
            valid_attackers = selected.get("validAttackers")
            valid_targets = selected.get("validAttackTargets")
            if isinstance(valid_attackers, list) and any(k not in valid_attackers for k in attackers):
                raise OpenAIResponsesPilotError("ActionParams.attackers contains an ineligible attacker")
            if isinstance(valid_targets, list) and any(v not in valid_targets for v in attackers.values()):
                raise OpenAIResponsesPilotError("ActionParams.attackers contains an invalid attack target")

        if "blockers" in populated:
            valid_blockers = selected.get("validBlockers")
            if isinstance(valid_blockers, list) and any(k not in valid_blockers for k in blockers):
                raise OpenAIResponsesPilotError("ActionParams.blockers contains an ineligible blocker")

        if "xValue" in populated:
            max_x = selected.get("maxAffordableX")
            if x_value < 0 or (type(max_x) is int and x_value > max_x):
                raise OpenAIResponsesPilotError("ActionParams.xValue is outside the affordable range")

        return normalized

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
                f"selected semanticId {semantic_id!r} is not exactly one current "
                "Argentum legal action"
            )
        selected = matches[0]
        action_id = selected.get("actionId")
        if type(action_id) is not int:
            raise OpenAIResponsesPilotError(
                "selected Argentum legal action is missing integer actionId"
            )
        normalized_params = self._validate_action_params(params, selected)
        return ArgentumActionChoice(
            action_id=action_id,
            params=normalized_params,
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
        response_type = _require_string(response.get("type"), "structured response type")
        spec = _structured_response_spec(pending)
        if spec is not None:
            expected_type, required_fields = spec
            cancel_allowed = _decision_cancel_allowed(pending)
            if response_type == "CancelDecisionResponse" and cancel_allowed:
                submitted = dict(response)
                submitted["decisionId"] = decision_id
                return ArgentumDecisionChoice(response=submitted, metadata=dict(metadata))
            if response_type != expected_type:
                raise OpenAIResponsesPilotError(
                    f"structured response for {pending.get('kind') or pending.get('type')} "
                    f"must use type {expected_type}, got {response_type}"
                )
            for field_name, wire_type in required_fields.items():
                if field_name not in response:
                    raise OpenAIResponsesPilotError(
                        f"{expected_type} is missing required field {field_name}"
                    )
                if not _matches_wire_type(response[field_name], wire_type):
                    raise OpenAIResponsesPilotError(
                        f"{expected_type}.{field_name} must be {wire_type}"
                    )

        submitted = dict(response)
        submitted["decisionId"] = decision_id
        return ArgentumDecisionChoice(response=submitted, metadata=dict(metadata))
