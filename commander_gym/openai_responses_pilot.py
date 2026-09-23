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
from time import perf_counter
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
        if (
            pending_copy.get("requiresStructuredResponse") is True
            and not isinstance(pending_copy.get("responseSpec"), Mapping)
        ):
            # Compatibility hint for older Gym/game-server observations. When Argentum supplies
            # responseSpec natively, preserve it verbatim so engine-owned protocol semantics remain
            # visible and downstream adapters do not rewrite the contract.
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


def _json_schema_for_wire_type(
    field_name: str,
    wire_type: str,
    pending: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    pending = pending or {}
    if wire_type == "boolean":
        return {"type": "boolean"}
    if wire_type == "integer":
        schema: dict[str, Any] = {"type": "integer"}
        if field_name == "number":
            lo, hi = pending.get("minValue"), pending.get("maxValue")
            if type(lo) is int:
                schema["minimum"] = lo
            if type(hi) is int:
                schema["maximum"] = hi
        elif field_name == "optionIndex":
            options = pending.get("options")
            if isinstance(options, list) and options:
                schema["enum"] = list(range(len(options)))
        return schema
    if wire_type == "string":
        schema = {"type": "string"}
        if field_name == "color":
            colors = pending.get("availableColors")
            if isinstance(colors, list) and colors and all(isinstance(item, str) for item in colors):
                schema["enum"] = list(colors)
        return schema
    if wire_type == "array":
        integer_arrays = {"selectedModes", "selectedModeIndices"}
        nested_string_arrays = {"piles"}
        if field_name in integer_arrays:
            item_schema: dict[str, Any] = {"type": "integer"}
            modes = pending.get("modes")
            if isinstance(modes, list) and modes:
                indices = []
                for index, mode in enumerate(modes):
                    if isinstance(mode, Mapping):
                        if mode.get("available", True) is not True:
                            continue
                        candidate = mode.get("index", index)
                    else:
                        candidate = index
                    if type(candidate) is int:
                        indices.append(candidate)
                if indices:
                    item_schema["enum"] = indices
            schema = {"type": "array", "items": item_schema}
            if field_name == "selectedModes":
                lo, hi = pending.get("minModes"), pending.get("maxModes")
                if type(lo) is int:
                    schema["minItems"] = lo
                if type(hi) is int:
                    schema["maxItems"] = hi
            return schema
        if field_name in nested_string_arrays:
            item_schema: dict[str, Any] = {"type": "string"}
            cards = pending.get("cards")
            if isinstance(cards, list) and cards and all(isinstance(item, str) for item in cards):
                item_schema["enum"] = list(cards)
            return {
                "type": "array",
                "items": {"type": "array", "items": item_schema},
            }
        if field_name == "edges":
            edge_ids = [
                edge.get("id")
                for edge in pending.get("edges", [])
                if isinstance(edge, Mapping) and isinstance(edge.get("id"), str)
            ]
            edge_id_schema: dict[str, Any] = {"type": "string"}
            if edge_ids:
                edge_id_schema["enum"] = edge_ids
            return {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "edgeId": edge_id_schema,
                        "amount": {"type": "integer", "minimum": 0},
                    },
                    "required": ["edgeId", "amount"],
                    "additionalProperties": False,
                },
            }

        item_schema: dict[str, Any] = {"type": "string"}
        choices: list[str] = []
        if field_name == "selectedCards":
            raw = pending.get("options")
            if isinstance(raw, list) and all(isinstance(item, str) for item in raw):
                choices = list(raw)
        elif field_name == "orderedObjects":
            raw = pending.get("objects")
            if raw is None:
                raw = pending.get("cards")
            if isinstance(raw, list) and all(isinstance(item, str) for item in raw):
                choices = list(raw)
        elif field_name == "selectedSources":
            raw = pending.get("availableSources")
            if isinstance(raw, list):
                choices = [
                    source["entityId"]
                    for source in raw
                    if isinstance(source, Mapping) and isinstance(source.get("entityId"), str)
                ]
        elif field_name == "waterbendPermanents":
            raw = pending.get("waterbendPermanents")
            if isinstance(raw, list):
                choices = [
                    item["entityId"]
                    for item in raw
                    if isinstance(item, Mapping) and isinstance(item.get("entityId"), str)
                ]
        if choices:
            item_schema["enum"] = choices
        schema = {"type": "array", "items": item_schema}
        if field_name == "selectedCards":
            lo, hi = pending.get("minSelections"), pending.get("maxSelections")
            if pending.get("type") != "SearchLibraryDecision":
                if type(lo) is int:
                    schema["minItems"] = lo
            if type(hi) is int:
                schema["maxItems"] = hi
        return schema
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
                properties[field_name] = _json_schema_for_wire_type(field_name, wire_type, pending)
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
        choose_started = perf_counter()
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
                choice = self._choice_from_response(
                    response,
                    observation,
                    retry_count=attempt,
                )
                elapsed_ms = round((perf_counter() - choose_started) * 1000.0, 3)
                if isinstance(choice, ArgentumActionChoice):
                    return ArgentumActionChoice(
                        action_id=choice.action_id,
                        params=choice.params,
                        metadata={**dict(choice.metadata), "providerWallTimeMs": elapsed_ms},
                    )
                if isinstance(choice, ArgentumDecisionChoice):
                    return ArgentumDecisionChoice(
                        response=choice.response,
                        metadata={**dict(choice.metadata), "providerWallTimeMs": elapsed_ms},
                    )
                raise OpenAIResponsesPilotError("provider returned unsupported pilot choice")
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

    @staticmethod
    def _validate_structured_response_values(
        response: Mapping[str, Any],
        pending: Mapping[str, Any],
    ) -> None:
        response_type = response.get("type")

        if response_type == "CardsSelectedResponse":
            selected = response.get("selectedCards")
            options = pending.get("options")
            if isinstance(selected, list) and isinstance(options, list):
                if len(selected) != len(set(map(str, selected))):
                    raise OpenAIResponsesPilotError("selectedCards must not contain duplicates")
                if any(card not in options for card in selected):
                    raise OpenAIResponsesPilotError("selectedCards contains a card outside current options")
                maximum = pending.get("maxSelections")
                if type(maximum) is int and len(selected) > maximum:
                    raise OpenAIResponsesPilotError("selectedCards exceeds maxSelections")
                minimum = pending.get("minSelections")
                if (
                    pending.get("type") != "SearchLibraryDecision"
                    and type(minimum) is int
                    and len(selected) < minimum
                ):
                    raise OpenAIResponsesPilotError("selectedCards is below minSelections")

        elif response_type == "ColorChosenResponse":
            color = response.get("color")
            colors = pending.get("availableColors")
            if isinstance(colors, list) and color not in colors:
                raise OpenAIResponsesPilotError("selected color is not currently available")

        elif response_type == "NumberChosenResponse":
            number = response.get("number")
            lo, hi = pending.get("minValue"), pending.get("maxValue")
            if type(number) is int:
                if type(lo) is int and number < lo:
                    raise OpenAIResponsesPilotError("selected number is below minValue")
                if type(hi) is int and number > hi:
                    raise OpenAIResponsesPilotError("selected number is above maxValue")

        elif response_type == "ModesChosenResponse":
            selected = response.get("selectedModes")
            modes = pending.get("modes")
            if isinstance(selected, list) and isinstance(modes, list):
                available = set()
                for index, mode in enumerate(modes):
                    if isinstance(mode, Mapping):
                        if mode.get("available", True) is not True:
                            continue
                        candidate = mode.get("index", index)
                    else:
                        candidate = index
                    if type(candidate) is int:
                        available.add(candidate)
                if any(mode not in available for mode in selected):
                    raise OpenAIResponsesPilotError("selectedModes contains an unavailable mode")
                lo, hi = pending.get("minModes"), pending.get("maxModes")
                if type(lo) is int and len(selected) < lo:
                    raise OpenAIResponsesPilotError("selectedModes is below minModes")
                if type(hi) is int and len(selected) > hi:
                    raise OpenAIResponsesPilotError("selectedModes exceeds maxModes")

        elif response_type == "OptionChosenResponse":
            index = response.get("optionIndex")
            options = pending.get("options")
            if type(index) is int and isinstance(options, list) and not (0 <= index < len(options)):
                raise OpenAIResponsesPilotError("optionIndex is outside current options")

        elif response_type == "OrderedResponse":
            ordered = response.get("orderedObjects")
            objects = pending.get("objects")
            if objects is None:
                objects = pending.get("cards")
            if isinstance(ordered, list) and isinstance(objects, list):
                if sorted(map(str, ordered)) != sorted(map(str, objects)):
                    raise OpenAIResponsesPilotError(
                        "orderedObjects must contain exactly the current objects once each"
                    )

        elif response_type == "TargetsResponse":
            selected = response.get("selectedTargets")
            requirements = pending.get("targetRequirements")
            legal_targets = pending.get("legalTargets")
            if (
                isinstance(selected, Mapping)
                and isinstance(requirements, list)
                and isinstance(legal_targets, Mapping)
            ):
                for requirement in requirements:
                    if not isinstance(requirement, Mapping):
                        continue
                    index = requirement.get("index")
                    if type(index) is not int:
                        continue
                    key = str(index)
                    chosen = selected.get(key, selected.get(index))
                    if not isinstance(chosen, list):
                        raise OpenAIResponsesPilotError(
                            f"selectedTargets is missing requirement {index}"
                        )
                    allowed = legal_targets.get(key, legal_targets.get(index))
                    if isinstance(allowed, list) and any(target not in allowed for target in chosen):
                        raise OpenAIResponsesPilotError(
                            f"selectedTargets for requirement {index} contains an illegal target"
                        )
                    minimum = requirement.get("minTargets", 1)
                    maximum = requirement.get("maxTargets", 1)
                    if type(minimum) is int and len(chosen) < minimum:
                        raise OpenAIResponsesPilotError(
                            f"selectedTargets for requirement {index} is below minTargets"
                        )
                    if type(maximum) is int and len(chosen) > maximum:
                        raise OpenAIResponsesPilotError(
                            f"selectedTargets for requirement {index} exceeds maxTargets"
                        )

        elif response_type == "CombatResolutionResponse":
            edges = response.get("edges")
            native_edges = pending.get("edges")
            if isinstance(edges, list) and isinstance(native_edges, list):
                by_id = {
                    edge.get("id"): edge
                    for edge in native_edges
                    if isinstance(edge, Mapping) and isinstance(edge.get("id"), str)
                }
                for chosen in edges:
                    if not isinstance(chosen, Mapping):
                        continue
                    edge_id = chosen.get("edgeId")
                    amount = chosen.get("amount")
                    native = by_id.get(edge_id)
                    if native is None:
                        raise OpenAIResponsesPilotError("combat response contains an unknown edge")
                    maximum = native.get("maximum")
                    if type(amount) is int and (
                        amount < 0 or (type(maximum) is int and amount > maximum)
                    ):
                        raise OpenAIResponsesPilotError(
                            "combat response amount is outside the native edge range"
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
            self._validate_structured_response_values(response, pending)

        submitted = dict(response)
        submitted["decisionId"] = decision_id
        return ArgentumDecisionChoice(response=submitted, metadata=dict(metadata))
