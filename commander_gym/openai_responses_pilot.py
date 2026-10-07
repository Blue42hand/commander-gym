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
import re
from time import perf_counter
from typing import Any, Mapping

from .pilot import ArgentumActionChoice, ArgentumDecisionChoice, PilotChoice
from .openai_run_budget import OpenAIRunBudget, OpenAIRunBudgetError
from .observation_projection import compact_seat_observation
from .cache_friendly_input import cache_friendly_observation_input
from .delegated_autopass import _NATIVE_PHASES, _NATIVE_STEPS, _nonmana_ability_keys

MODEL_IO_SCHEMA_VERSION = 1
EXPLICIT_NAMED_WAIT_INSTRUCTIONS = (
    "Before returning PassPriority, check whether every currently affordable "
    "nonmana ActivateAbility can safely be deferred through the rest of this "
    "turn. If so, include priorityDelegation with until turn_end, a concrete "
    "reason, and deferAbilities containing every exact sourceId/abilityId pair "
    "from legalActions. If any ability might need activation before your next "
    "turn, omit delegation. Do not use phase_end or next_own_main to delegate "
    "a menu that still contains a nonmana ability."
)
_RECOVERY_CALLBACK_SECONDS = 110.0  # Argentum's policy HTTP deadline is 120 s.
_RECOVERY_REQUEST_SECONDS = 90.0
_RECOVERY_RETURN_MARGIN_SECONDS = 5.0
_RECOVERY_MIN_RETRY_SECONDS = 10.0

class OpenAIResponsesPilotError(RuntimeError):
    """Raised when the provider cannot yield one valid native Argentum choice.

    ``model_io`` is populated only when at least one provider attempt actually began.
    It intentionally uses the same attempt snapshots as successful choices but leaves
    ``selectedAttempt`` null so callers can preserve failed diagnostics without
    inventing a gameplay choice or successful model output.
    """

    def __init__(
        self,
        message: str,
        *,
        model_io: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.model_io = deepcopy(dict(model_io)) if model_io is not None else None


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


def _response_content_snapshot(response: Any) -> dict[str, Any]:
    """Capture exact model-produced content without serializing opaque SDK objects."""

    direct = _field(response, "output_text")
    if isinstance(direct, str) and direct:
        return {"outputText": direct}

    output = _field(response, "output")
    if not isinstance(output, list):
        return {}

    pieces: list[str] = []
    refusals: list[str] = []
    for item in output:
        content = _field(item, "content")
        if not isinstance(content, list):
            continue
        for part in content:
            part_type = _field(part, "type")
            if part_type == "output_text":
                text = _field(part, "text")
                if isinstance(text, str):
                    pieces.append(text)
            elif part_type == "refusal":
                refusal = _field(part, "refusal")
                if isinstance(refusal, str):
                    refusals.append(refusal)
    snapshot: dict[str, Any] = {}
    if pieces:
        snapshot["outputText"] = "".join(pieces)
    if refusals:
        snapshot["refusal"] = "".join(refusals)
    return snapshot


def _provider_response_snapshot(response: Any) -> dict[str, Any]:
    """Capture stable response fields needed to reconstruct one provider attempt."""

    snapshot = _response_content_snapshot(response)
    response_id = _field(response, "id")
    if isinstance(response_id, str) and response_id:
        snapshot["responseId"] = response_id
    response_model = _field(response, "model")
    if isinstance(response_model, str) and response_model:
        snapshot["responseModel"] = response_model
    status = _field(response, "status")
    if isinstance(status, str) and status:
        snapshot["status"] = status
    usage = _as_mapping(_field(response, "usage"))
    if usage is not None:
        snapshot["usage"] = dict(usage)
    incomplete_details = _as_mapping(_field(response, "incomplete_details"))
    if incomplete_details is not None:
        snapshot["incompleteDetails"] = dict(incomplete_details)
    provider_error = _field(response, "error")
    provider_error_mapping = _as_mapping(provider_error)
    if provider_error_mapping is not None:
        snapshot["providerError"] = dict(provider_error_mapping)
    elif isinstance(provider_error, str) and provider_error:
        snapshot["providerError"] = provider_error
    return snapshot


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
    # Preserve only a short machine-readable code. Free-form provider error
    # messages can echo request data or credentials into the HTTP failure.
    body = getattr(exc, "body", None)
    if isinstance(body, Mapping):
        error = body.get("error", body)
        if isinstance(error, Mapping):
            code = error.get("code")
            if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code):
                parts.append(f"code={code}")
    return ", ".join(parts)


def _retryable_provider_status(exc: Exception) -> bool:
    """Recognize a bounded server failure without trusting exception text."""

    return type(getattr(exc, "status_code", None)) is int and exc.status_code in {
        500, 502, 503, 504, 520,
    }


def _failed_model_io(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a failure trace with no fabricated selected/successful model attempt."""

    return {
        "schemaVersion": MODEL_IO_SCHEMA_VERSION,
        "provider": "openai",
        "selectedAttempt": None,
        "attempts": deepcopy(attempts),
    }


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise OpenAIResponsesPilotError(f"{label} must be a non-empty string")
    return value


def _matches_native_field_kind(value: Any, kind: str) -> bool:
    """Check the machine-readable Argentum wire kind before native submission."""

    if kind == "BOOLEAN":
        return type(value) is bool
    if kind == "INTEGER":
        return type(value) is int and -(2**31) <= value < 2**31
    if kind == "STRING":
        return isinstance(value, str)
    if kind == "ENTITY_ID_ARRAY":
        return isinstance(value, list) and all(isinstance(item, str) for item in value)
    if kind == "INTEGER_ARRAY":
        return isinstance(value, list) and all(_matches_native_field_kind(item, "INTEGER") for item in value)
    if kind == "ENTITY_ID_ARRAY_ARRAY":
        return isinstance(value, list) and all(
            isinstance(group, list) and all(isinstance(item, str) for item in group)
            for group in value
        )
    if kind == "MAP":
        return isinstance(value, dict)
    if kind == "DAMAGE_EDGE_AMOUNT_ARRAY":
        return isinstance(value, list)
    return False


_NATIVE_DECISION_FIELD_SCHEMAS: dict[str, dict[str, Any]] = {
    "BOOLEAN": {"type": "boolean"},
    "INTEGER": {"type": "integer", "minimum": -(2**31), "maximum": 2**31 - 1},
    "STRING": {"type": "string"},
    "ENTITY_ID_ARRAY": {"type": "array", "items": {"type": "string"}},
    "INTEGER_ARRAY": {"type": "array", "items": {"type": "integer", "minimum": -(2**31), "maximum": 2**31 - 1}},
    "ENTITY_ID_ARRAY_ARRAY": {
        "type": "array", "items": {"type": "array", "items": {"type": "string"}},
    },
    # Argentum's MAP kind does not describe its values. The response DTOs do.
    "MAP": {"type": "object"},
    "DAMAGE_EDGE_AMOUNT_ARRAY": {
        "type": "array", "items": {
            "type": "object",
            "properties": {"edgeId": {"type": "string"}, "amount": {"type": "integer", "minimum": -(2**31), "maximum": 2**31 - 1}},
            "required": ["edgeId", "amount"], "additionalProperties": False,
        },
    },
}

# Optional fields present in native response DTOs but absent from requiredFields.
_NATIVE_OPTIONAL_RESPONSE_FIELDS = {
    "CombatResolutionResponse": {
        "orderedBlockers": "ENTITY_ID_ARRAY_MAP",
        "orderedAttackers": "ENTITY_ID_ARRAY_MAP",
    },
}


def _native_response_field_schema(response_type: str, field: str, kind: str) -> dict[str, Any] | None:
    if kind == "ENTITY_ID_ARRAY_MAP" and (
        _NATIVE_OPTIONAL_RESPONSE_FIELDS.get(response_type, {}).get(field) == kind
    ):
        # The set of native entity-ID keys is dynamic. Validate values locally;
        # OpenAI's structured-output subset does not support typed map entries.
        return {"type": "object"}
    if kind == "MAP":
        if (response_type, field) in {
            ("DistributionResponse", "distribution"),
            ("DamageAssignmentResponse", "assignments"),
        }:
            return {"type": "object"}
        return None  # TargetsResponse uses the offered requirement indices below.
    if (response_type, field, kind) == ("ColorChosenResponse", "color", "STRING"):
        return {"type": "string", "enum": ["WHITE", "BLUE", "BLACK", "RED", "GREEN"]}
    return _NATIVE_DECISION_FIELD_SCHEMAS.get(kind)


def _matches_native_response_field(value: Any, response_type: str, field: str, kind: str) -> bool:
    if kind == "ENTITY_ID_ARRAY_MAP":
        return isinstance(value, dict) and all(
            isinstance(key, str) and _matches_native_field_kind(ids, "ENTITY_ID_ARRAY")
            for key, ids in value.items()
        )
    if kind == "MAP":
        if (response_type, field) in {
            ("DistributionResponse", "distribution"),
            ("DamageAssignmentResponse", "assignments"),
        }:
            return isinstance(value, dict) and all(
                isinstance(key, str) and _matches_native_field_kind(amount, "INTEGER")
                for key, amount in value.items()
            )
        return response_type == "TargetsResponse" and field == "selectedTargets" and isinstance(value, dict)
    if kind == "DAMAGE_EDGE_AMOUNT_ARRAY":
        return response_type == "CombatResolutionResponse" and field == "edges" and isinstance(value, list) and all(
            isinstance(edge, dict) and set(edge) == {"edgeId", "amount"}
            and isinstance(edge["edgeId"], str) and _matches_native_field_kind(edge["amount"], "INTEGER")
            for edge in value
        )
    if (response_type, field, kind) == ("ColorChosenResponse", "color", "STRING"):
        return isinstance(value, str) and value in {"WHITE", "BLUE", "BLACK", "RED", "GREEN"}
    return _matches_native_field_kind(value, kind)


_ACTION_PARAM_SCHEMAS: dict[str, dict[str, Any]] = {
    # Arbitrary entity-ID keys are checked locally; the provider schema only needs
    # to constrain the named ActionParams fields to Argentum's declared wire kinds.
    "ENTITY_ID_MAP": {"type": "object"},
    "ENTITY_ID_ARRAY_MAP": {"type": "object"},
    "ENTITY_ID_ARRAY": {"type": "array", "items": {"type": "string"}},
    "INTEGER": {"type": "integer"},
}


def _native_action_param_fields(action: Mapping[str, Any]) -> Mapping[str, str] | None:
    spec = action.get("parameterSpec")
    if spec is None:
        return None
    if not isinstance(spec, Mapping) or not isinstance(spec.get("allowedFields"), Mapping):
        raise OpenAIResponsesPilotError("native action parameterSpec is malformed")
    fields = spec["allowedFields"]
    for name, kind in fields.items():
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(kind, str)
            or kind not in _ACTION_PARAM_SCHEMAS
        ):
            raise OpenAIResponsesPilotError("native action parameterSpec has an unsupported field")
    return fields


def _native_block_targets(action: Mapping[str, Any]) -> dict[str, list[str]] | None:
    """Read native pairwise block offers; absence keeps older engine contracts usable."""
    if action.get("kind", action.get("actionType")) != "DeclareBlockers" or "validBlockTargets" not in action:
        return None
    offered = action["validBlockTargets"]
    if not isinstance(offered, Mapping) or not all(
        isinstance(blocker, str) and blocker
        and isinstance(attackers, list) and attackers
        and all(isinstance(attacker, str) and attacker for attacker in attackers)
        for blocker, attackers in offered.items()
    ):
        raise OpenAIResponsesPilotError("native validBlockTargets is malformed")
    return {blocker: list(attackers) for blocker, attackers in offered.items()}


def _native_block_cap(action: Mapping[str, Any]) -> int | None:
    if action.get("kind", action.get("actionType")) != "DeclareBlockers":
        return None
    cap = action.get("maxTotalBlockers")
    if cap is not None and (type(cap) is not int or cap < 0):
        raise OpenAIResponsesPilotError("native maxTotalBlockers is malformed")
    return cap


def _native_attack_targets(action: Mapping[str, Any]) -> tuple[list[str], list[str]] | None:
    """Read the current native attacker and defender candidates as one offer."""
    if action.get("kind", action.get("actionType")) != "DeclareAttackers":
        return None
    if "validAttackers" not in action or "validAttackTargets" not in action:
        raise OpenAIResponsesPilotError("native attacker candidates are malformed")
    # Argentum uses null for an empty candidate list in GameServer legal actions.
    attackers = [] if action["validAttackers"] is None else action["validAttackers"]
    targets = [] if action["validAttackTargets"] is None else action["validAttackTargets"]
    if not all(
        isinstance(ids, list) and all(isinstance(entity_id, str) and entity_id for entity_id in ids)
        for ids in (attackers, targets)
    ):
        raise OpenAIResponsesPilotError("native attacker candidates are malformed")
    return attackers, targets


def _native_action_field_schema(action: Mapping[str, Any], name: str, kind: str) -> dict[str, Any]:
    if name == "delvedCards" and kind == "ENTITY_ID_ARRAY":
        offer = _native_delve_offer(action)
        if offer is not None:
            candidates, cap = offer
            schema = _offered_id_array_schema(candidates)
            if cap is not None:
                schema["maxItems"] = cap
            return schema
    if name == "attackers" and kind == "ENTITY_ID_MAP":
        offered = _native_attack_targets(action)
        if offered is not None:
            attackers, targets = offered
            return {"type": "object", "properties": {
                attacker: {"type": "string", "enum": list(dict.fromkeys(targets))}
                for attacker in attackers if targets
            }, "additionalProperties": False}
    if name == "blockers" and kind == "ENTITY_ID_ARRAY_MAP":
        targets = _native_block_targets(action)
        if targets is not None:
            cap = _native_block_cap(action)
            schema = {"type": "object", "properties": {
                blocker: {"type": "array", "items": {"type": "string", "enum": attackers}}
                for blocker, attackers in targets.items()
            }, "additionalProperties": False}
            if cap is not None:
                schema["description"] = f"Select at most {cap} distinct blocker keys."
            return schema
    return _ACTION_PARAM_SCHEMAS[kind]


def _native_delve_offer(action: Mapping[str, Any]) -> tuple[list[str], int | None] | None:
    if action.get("hasDelve") is not True:
        return None
    cards = action.get("validDelveCards")
    if not isinstance(cards, list) or any(
        not isinstance(card, Mapping) or not isinstance(card.get("entityId"), str)
        or not card["entityId"] for card in cards
    ):
        raise OpenAIResponsesPilotError("native validDelveCards is malformed")
    ids = [card["entityId"] for card in cards]
    if len(ids) != len(set(ids)):
        raise OpenAIResponsesPilotError("native validDelveCards contains duplicates")
    cap = action.get("maxDelveCards")
    if cap is not None and (type(cap) is not int or cap < 0 or cap > len(ids)):
        raise OpenAIResponsesPilotError("native maxDelveCards is malformed")
    return ids, cap


def _native_card_options(pending: Mapping[str, Any]) -> list[str] | None:
    if pending.get("responseSpec", {}).get("responseType") != "CardsSelectedResponse":
        return None
    if "options" not in pending and pending.get("kind") != "SelectCardsDecision":
        return None  # Older fixtures may carry the response shape without native candidates.
    options = pending.get("options")
    if not isinstance(options, list) or not all(
        isinstance(entity_id, str) and entity_id for entity_id in options
    ):
        raise OpenAIResponsesPilotError("native card options are malformed")
    return options


def _offered_id_array_schema(ids: list[str]) -> dict[str, Any]:
    """Keep an empty native offer satisfiable only by [], without enum: []."""
    offered = list(dict.fromkeys(ids))
    schema: dict[str, Any] = {"type": "array", "items": {"type": "string"}}
    if offered:
        schema["items"]["enum"] = offered
    else:
        schema["maxItems"] = 0
    return schema


def _native_action_format(
    observation: Mapping[str, Any], *, allow_priority_delegation: bool = False,
    allow_named_deferrals: bool = False,
    require_nonempty_named_deferrals: bool = False,
    allow_declarative_continuation: bool = False,
) -> dict[str, Any] | None:
    """Constrain each semantic action to its own Argentum-authored parameter fields."""

    legal = observation.get("legalActions")
    pending = observation.get("pendingDecision")
    if isinstance(pending, Mapping) and pending.get("requiresStructuredResponse") is True:
        return None
    if not isinstance(legal, list) or not legal:
        return None
    variants: list[dict[str, Any]] = []
    for action in legal:
        if not isinstance(action, Mapping):
            return None
        fields = _native_action_param_fields(action)
        if fields is None:
            return None
        semantic_id = _require_string(action.get("semanticId"), "legal semanticId")
        variants.append({
            "type": "object",
            "properties": {
                "semanticId": {"type": "string", "const": semantic_id},
                "params": {
                    "type": "object",
                    "properties": {
                        name: _native_action_field_schema(action, name, kind)
                        for name, kind in fields.items()
                    },
                    "additionalProperties": False,
                },
            },
            "required": ["semanticId", "params"],
            "additionalProperties": False,
        })
    root_properties: dict[str, Any] = {
        "channel": {"type": "string", "const": "action"},
        "choice": {"anyOf": variants},
    }
    if allow_priority_delegation:
        root_properties["priorityDelegation"] = {
            "type": "object",
            "properties": {
                "until": {"type": "string", "enum": ["phase_end", "next_own_main", "turn_end"] if allow_named_deferrals else ["phase_end", "next_own_main"]},
                "reason": {"type": "string"},
                "watchOpponents": {"type": "boolean"},
                **({"deferAbilities": {"type": "array", **({"minItems": 1} if require_nonempty_named_deferrals else {}), "items": {
                    "type": "object", "properties": {
                        "sourceId": {"type": "string"}, "abilityId": {"type": "string"},
                    }, "required": ["sourceId", "abilityId"], "additionalProperties": False,
                }}} if allow_named_deferrals else {}),
            },
            "required": ["until", "reason"],
            "additionalProperties": False,
        }
    if allow_named_deferrals:
        root_properties["thenCast"] = {
            "type": "object",
            "properties": {"cardId": {"type": "string"}, "reason": {"type": "string"}},
            "required": ["cardId", "reason"],
            "additionalProperties": False,
        }
    if allow_declarative_continuation:
        root_properties["continuation"] = {
            "type": "object", "properties": {
                "reason": {"type": "string"},
                "steps": {"type": "array", "minItems": 1, "maxItems": 4,
                          "items": {"anyOf": [
                              {"type": "object", "properties": {
                                  "type": {"type": "string", "const": "wait"},
                                  "until": {"type": "string", "enum": ["phase_end", "next_own_main"]},
                                  "maxPasses": {"type": "integer", "minimum": 1, "maximum": 16},
                              }, "required": ["type", "until", "maxPasses"],
                                  "additionalProperties": False},
                              {"type": "object", "properties": {
                                  "type": {"type": "string", "enum": ["playLand", "cast"]},
                                  "cardId": {"type": "string"},
                                  "when": {"type": "object", "properties": {
                                      "phase": {"type": "string", "enum": sorted(_NATIVE_PHASES)},
                                      "step": {"type": "string", "enum": sorted(_NATIVE_STEPS)},
                                      "stackEmpty": {"type": "boolean"},
                                  }, "required": ["stackEmpty"],
                                     "additionalProperties": False},
                                  "params": {"type": "object", "properties": {},
                                             "additionalProperties": False},
                              }, "required": ["type", "cardId", "when", "params"],
                                  "additionalProperties": False},
                          ]}},
            }, "required": ["reason", "steps"], "additionalProperties": False,
        }
    return {
        "type": "json_schema",
        "name": "commander_gym_native_action",
        "strict": False,
        "schema": {
            "type": "object",
            "properties": root_properties,
            "required": ["channel", "choice"],
            "additionalProperties": False,
        },
    }


def _native_auto_pay_available(pending: Mapping[str, Any]) -> bool | None:
    """Use the engine's payment verdict; older and context-sensitive windows remain unknown."""
    value = pending.get("canAutoPayNow")
    if value is not None and type(value) is not bool:
        raise OpenAIResponsesPilotError("native canAutoPayNow must be boolean or null")
    return value


def _native_mana_source_format(observation: Mapping[str, Any]) -> dict[str, Any] | None:
    """Constrain a native mana-source response to this decision's offered IDs."""

    pending = observation.get("pendingDecision")
    if not isinstance(pending, Mapping) or pending.get("requiresStructuredResponse") is not True:
        return None
    spec = pending.get("responseSpec")
    if not isinstance(spec, Mapping) or spec.get("responseType") != "ManaSourcesSelectedResponse":
        return None
    can_auto_pay = _native_auto_pay_available(pending)
    legal = observation.get("legalActions")
    if isinstance(legal, list) and any(
        isinstance(action, Mapping)
        and action.get("kind") == "ActivateAbility"
        and action.get("isManaAbility") is True
        for action in legal
    ):
        # A mixed payment window permits a native mana action before answering
        # the decision; a decision-only schema would hide that legal channel.
        return None
    fields = spec.get("requiredFields")
    if fields != {
        "autoPay": "BOOLEAN", "declined": "BOOLEAN",
        "selectedSources": "ENTITY_ID_ARRAY", "waterbendPermanents": "ENTITY_ID_ARRAY",
    }:
        return None
    available = pending.get("availableSources")
    if not isinstance(available, list) or any(
        not isinstance(source, Mapping) or not isinstance(source.get("entityId"), str)
        for source in available
    ):
        return None
    offered_ids = list(dict.fromkeys(source["entityId"] for source in available))
    return {
        "type": "json_schema", "name": "commander_gym_native_mana_sources", "strict": False,
        "schema": {
            "type": "object",
            "properties": {
                "channel": {"type": "string", "const": "decision"},
                "response": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "const": "ManaSourcesSelectedResponse"},
                        "autoPay": {"type": "boolean", **(
                            {"const": False} if can_auto_pay is False else {}
                        )},
                        "declined": {"type": "boolean"},
                        "selectedSources": _offered_id_array_schema(offered_ids),
                        "waterbendPermanents": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["type", *fields],
                    "additionalProperties": False,
                },
            },
            "required": ["channel", "response"],
            "additionalProperties": False,
        },
    }


def _native_targets_format(observation: Mapping[str, Any]) -> dict[str, Any] | None:
    """Constrain a target response to Argentum's current requirement indices and IDs."""

    pending = observation.get("pendingDecision")
    if not isinstance(pending, Mapping) or pending.get("requiresStructuredResponse") is not True:
        return None
    spec = pending.get("responseSpec")
    if not isinstance(spec, Mapping) or spec.get("responseType") != "TargetsResponse":
        return None
    if spec.get("requiredFields") != {"selectedTargets": "MAP"}:
        return None
    legal_targets = pending.get("legalTargets")
    if not isinstance(legal_targets, Mapping) or any(
        not isinstance(index, str)
        or not index.isdecimal()
        or not _matches_native_field_kind(ids, "ENTITY_ID_ARRAY")
        for index, ids in legal_targets.items()
    ):
        return None
    return {
        "type": "json_schema", "name": "commander_gym_native_targets", "strict": False,
        "schema": {
            "type": "object",
            "properties": {
                "channel": {"type": "string", "const": "decision"},
                "response": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "const": "TargetsResponse"},
                        "selectedTargets": {
                            "type": "object",
                            "properties": {
                                index: _offered_id_array_schema(ids)
                                for index, ids in legal_targets.items()
                            },
                            "additionalProperties": False,
                        },
                    },
                    "required": ["type", "selectedTargets"],
                    "additionalProperties": False,
                },
            },
            "required": ["channel", "response"],
            "additionalProperties": False,
        },
    }


def _native_decision_format(observation: Mapping[str, Any]) -> dict[str, Any] | None:
    """Build a request-local wire schema from Argentum's live responseSpec."""

    pending = observation.get("pendingDecision")
    if not isinstance(pending, Mapping) or pending.get("requiresStructuredResponse") is not True:
        return None
    spec = pending.get("responseSpec")
    if not isinstance(spec, Mapping):
        return None
    # A payment window can also offer a native mana action before the decision.
    special = _native_mana_source_format(observation) or _native_targets_format(observation)
    if special is not None:
        return _with_native_cancel_schema(special, spec)
    response_type, fields = spec.get("responseType"), spec.get("requiredFields")
    if not isinstance(response_type, str) or not isinstance(fields, Mapping):
        return None
    if response_type == "ManaSourcesSelectedResponse":
        return None  # Preserve the mixed mana-action channel.
    card_options = _native_card_options(pending)
    properties: dict[str, Any] = {"type": {"type": "string", "const": response_type}}
    for field, kind in fields.items():
        if not isinstance(field, str) or not isinstance(kind, str):
            return None
        schema = _native_response_field_schema(response_type, field, kind)
        if schema is None:
            return None
        if field == "selectedCards" and kind == "ENTITY_ID_ARRAY" and card_options is not None:
            schema = _offered_id_array_schema(card_options)
        properties[field] = schema
    for field, kind in _NATIVE_OPTIONAL_RESPONSE_FIELDS.get(response_type, {}).items():
        properties[field] = _native_response_field_schema(response_type, field, kind)
    result = {
        "type": "json_schema", "name": "commander_gym_native_decision", "strict": False,
        "schema": {
            "type": "object",
            "properties": {
                "channel": {"type": "string", "const": "decision"},
                "response": {
                    "type": "object", "properties": properties,
                    "required": ["type", *fields], "additionalProperties": False,
                },
            },
            "required": ["channel", "response"], "additionalProperties": False,
        },
    }
    return _with_native_cancel_schema(result, spec)


def _with_native_cancel_schema(format_: dict[str, Any], spec: Mapping[str, Any]) -> dict[str, Any]:
    if spec.get("cancelAllowed") is not True:
        return format_
    cancel = {
        "type": "object",
        "properties": {"type": {"type": "string", "const": "CancelDecisionResponse"}},
        "required": ["type"], "additionalProperties": False,
    }
    schema = deepcopy(format_["schema"])
    schema["properties"]["response"] = {"anyOf": [
        schema["properties"]["response"], cancel,
    ]}
    return {**format_, "schema": schema}


def _matches_action_param_kind(value: Any, kind: str) -> bool:
    if kind == "INTEGER":
        return type(value) is int
    if kind == "ENTITY_ID_ARRAY":
        return isinstance(value, list) and all(isinstance(item, str) for item in value)
    if kind == "ENTITY_ID_MAP":
        return isinstance(value, dict) and all(
            isinstance(key, str) and isinstance(item, str)
            for key, item in value.items()
        )
    if kind == "ENTITY_ID_ARRAY_MAP":
        return isinstance(value, dict) and all(
            isinstance(key, str)
            and isinstance(items, list)
            and all(isinstance(item, str) for item in items)
            for key, items in value.items()
        )
    return False


def _validate_native_action_params(
    params: Mapping[str, Any], action: Mapping[str, Any]
) -> dict[str, Any]:
    fields = _native_action_param_fields(action)
    if fields is None:
        if action.get("parameterSpec") is not None:
            raise OpenAIResponsesPilotError("native action parameterSpec is malformed")
        return dict(params)
    unexpected = set(params) - set(fields)
    if unexpected:
        raise OpenAIResponsesPilotError(
            f"ActionParams fields are not allowed by native parameterSpec: {sorted(unexpected)}"
        )
    for name, value in params.items():
        kind = fields[name]
        if not _matches_action_param_kind(value, kind):
            raise OpenAIResponsesPilotError(f"ActionParams.{name} requires {kind}")
    targets = _native_block_targets(action)
    if targets is not None and "blockers" in params and any(
        blocker not in targets or any(attacker not in targets[blocker] for attacker in attackers)
        for blocker, attackers in params["blockers"].items()
    ):
        raise OpenAIResponsesPilotError("ActionParams.blockers contains a pair outside native validBlockTargets")
    cap = _native_block_cap(action)
    if cap is not None and "blockers" in params and len(params["blockers"]) > cap:
        raise OpenAIResponsesPilotError("ActionParams.blockers exceeds native maxTotalBlockers")
    attack_offer = _native_attack_targets(action)
    if attack_offer is not None and "attackers" in params:
        attackers, targets = attack_offer
        if any(attacker not in attackers or target not in targets
               for attacker, target in params["attackers"].items()):
            raise OpenAIResponsesPilotError(
                "ActionParams.attackers contains an ID outside native validAttackers or validAttackTargets"
            )
    if "delvedCards" in params:
        offer = _native_delve_offer(action)
        if offer is not None:
            candidates, cap = offer
            selected = params["delvedCards"]
            if len(selected) != len(set(selected)) or any(card not in candidates for card in selected):
                raise OpenAIResponsesPilotError(
                    "ActionParams.delvedCards must be distinct native offered IDs"
                )
            if cap is not None and len(selected) > cap:
                raise OpenAIResponsesPilotError(
                    "ActionParams.delvedCards exceeds native maxDelveCards"
                )
    return dict(params)


def _with_model_io(
    choice: PilotChoice, model_io: Mapping[str, Any], provider_wall_time_ms: float,
) -> PilotChoice:
    """Attach exact provider attempts without changing the chosen Argentum payload."""

    if isinstance(choice, ArgentumActionChoice):
        return ArgentumActionChoice(
            action_id=choice.action_id,
            params=choice.params,
            metadata={**dict(choice.metadata), "modelIo": dict(model_io),
                      "providerWallTimeMs": round(provider_wall_time_ms, 3)},
        )
    if isinstance(choice, ArgentumDecisionChoice):
        return ArgentumDecisionChoice(
            response=choice.response,
            metadata={**dict(choice.metadata), "modelIo": dict(model_io),
                      "providerWallTimeMs": round(provider_wall_time_ms, 3)},
        )
    raise OpenAIResponsesPilotError("provider returned unsupported pilot choice type")


@dataclass
class OpenAIResponsesPilot:
    """Concrete strategic ``ArtificialPlayer`` backed by OpenAI Responses.

    ``client`` must expose ``client.responses.create(**kwargs)``.  The adapter uses
    Responses JSON mode for most native structured decisions; mana-source and
    target decisions use request-local schemas constrained to Argentum's offered IDs.
    Commander Gym parses and validates the returned channel locally, then the existing
    pilot/execution validators and Argentum perform authoritative validation.
    """

    client: Any
    model: str
    instructions: str = _DEFAULT_INSTRUCTIONS
    strategy: str | None = None
    max_attempts: int = 2
    budget: OpenAIRunBudget | None = None
    retry_transient_server_errors: bool = False
    allow_priority_delegation: bool = False
    allow_named_deferrals: bool = False
    require_nonempty_named_deferrals: bool = False
    explicit_wait_guidance: bool = False
    compact_model_observation: bool = False
    cache_friendly_history: bool = False
    guarded_then_cast_templates: bool = False
    allow_declarative_continuation: bool = False
    name: str = "openai-responses"
    version: str = "1"

    def __post_init__(self) -> None:
        _require_string(self.model, "OpenAI model")
        _require_string(self.instructions, "OpenAI pilot instructions")
        if type(self.explicit_wait_guidance) is not bool:
            raise OpenAIResponsesPilotError("explicit_wait_guidance must be boolean")
        if self.explicit_wait_guidance and not (
            self.allow_priority_delegation and self.allow_named_deferrals
            and self.require_nonempty_named_deferrals
        ):
            raise OpenAIResponsesPilotError(
                "explicit wait guidance requires the nonempty named-deferral Pilot"
            )
        if self.require_nonempty_named_deferrals and not (
            self.allow_priority_delegation and self.allow_named_deferrals
        ):
            raise OpenAIResponsesPilotError(
                "nonempty named deferrals require the named-deferral Pilot"
            )
        if type(self.compact_model_observation) is not bool:
            raise OpenAIResponsesPilotError("compact_model_observation must be boolean")
        if type(self.cache_friendly_history) is not bool:
            raise OpenAIResponsesPilotError("cache_friendly_history must be boolean")
        if type(self.retry_transient_server_errors) is not bool:
            raise OpenAIResponsesPilotError("retry_transient_server_errors must be boolean")
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
        native_payment_error = observation.get("nativePaymentError")
        if native_payment_error is not None:
            if not isinstance(native_payment_error, str) or not native_payment_error.strip():
                raise OpenAIResponsesPilotError("nativePaymentError must be a non-empty string")
            pending = observation.get("pendingDecision")
            if not isinstance(pending, Mapping) or pending.get("kind", pending.get("type")) != "SelectManaSourcesDecision":
                raise OpenAIResponsesPilotError("native payment correction requires the current mana decision")
        decision_format = _native_decision_format(observation)
        action_format = None if decision_format is not None else _native_action_format(
            observation, allow_priority_delegation=self.allow_priority_delegation,
            allow_named_deferrals=self.allow_named_deferrals,
            require_nonempty_named_deferrals=self.require_nonempty_named_deferrals,
            allow_declarative_continuation=self.allow_declarative_continuation,
        )
        model_observation = _without_live_routing(observation)
        if self.compact_model_observation:
            model_observation = compact_seat_observation(model_observation)
        base_input = "Return one JSON object for this observation:\n" + json.dumps(
            model_observation,
            sort_keys=True,
            separators=(",", ":"),
        )
        if self.cache_friendly_history:
            base_input = cache_friendly_observation_input(model_observation)
        request = {
            "model": self.model,
            "instructions": self.instructions
            + (f"\n\nRun-specific strategy:\n{self.strategy}" if self.strategy else ""),
            # The Responses JSON-object mode requires the user input itself to name
            # JSON; mentioning it only in instructions is not sufficient.
            "input": base_input,
            "text": {"format": decision_format or action_format or {"type": "json_object"}},
            "store": False,
        }
        if self.cache_friendly_history:
            # Explicit-only writes only at the stable, seat-masked boundaries.
            # Native request-local output schemas and all model settings remain.
            request["prompt_cache_options"] = {"mode": "explicit"}
        if native_payment_error is not None:
            request["instructions"] += (
                "\n\nArgentum rejected your previous payment response. "
                "nativePaymentError in the observation is the native reason. "
                "Use only this fresh legal action and pending-decision offer to correct it. "
                "You may activate an offered mana ability before answering the payment decision."
            )
        if self.compact_model_observation:
            request["instructions"] += (
                "\n\nThis request uses argentum-seat-sparse-cards-v1. The observation "
                "is inside observation; cardDefaults lists exact values for omitted "
                "fields on every state.cards entry. Restore those fields mentally "
                "before choosing. No other state fields or legal choices are "
                "omitted. Ephemeral routing handles are omitted as usual. Use "
                "semanticId from observation.legalActions."
            )
        if self.budget is not None:
            request["max_output_tokens"] = self.budget.MAX_OUTPUT_TOKENS
        pending = observation.get("pendingDecision")
        if action_format is not None:
            request["instructions"] += (
                "\n\nFor this request, use the request-local schema's nested choice object "
                "instead of top-level semanticId/params. Return channel action with "
                "choice containing the exact semanticId and only the ActionParams "
                "allowed by that action's parameterSpec."
            )
        if self.allow_priority_delegation:
            if self.allow_named_deferrals:
                request["instructions"] += (
                    "\n\nYou may include priorityDelegation only with PassPriority. "
                    "For Forge-style conditional waiting across phases of the current "
                    "turn, set until to turn_end, give a concrete reason, and set "
                    "deferAbilities to every currently affordable nonmana "
                    "ActivateAbility by its exact action.sourceId and action.abilityId. "
                    "Omit currently unaffordable abilities. Include the complete set "
                    "or omit delegation. You may set watchOpponents true; for turn_end "
                    "it is always true. Only defer abilities you deliberately choose "
                    "not to use through this turn. New legal alternatives, a changed "
                    "stack, visible board, hand, resources, life, or required decision "
                    "wake you. Do not defer tactical activations when timing matters. "
                    "For shorter waits you may instead set until to phase_end or "
                    "next_own_main without deferAbilities; those waits continue only "
                    "while later menus have pass and mana abilities. Never delegate "
                    "with floating mana or an unreviewed event you need to answer."
                    " You may attach thenCast only to a selected PlayLand action: "
                    "name one exact cardId already in your "
                    + ("hand or command zone" if self.guarded_then_cast_templates else "hand")
                    + " and a reason to "
                    "cast it immediately after the land. Gym will execute that "
                    "specific cast only if the land transition is isolated and "
                    "Argentum then offers one affordable CastSpell for that card. "
                    "A changed state or unavailable cast wakes you instead."
                )
                if self.guarded_then_cast_templates:
                    request["instructions"] += (
                        " When choosing PlayLand, attach thenCast if you already "
                        "intend to cast one exact card from your hand or your "
                        "commander from the command zone immediately afterward. "
                        "This is only a conditional plan: Gym checks the fresh native "
                        "offer and visible state before acting. Omit it if the cast "
                        "needs targets, X, modes, alternative payment, or an "
                        "additional cost; you will choose those after the land. "
                        "Never guess a payment or target in thenCast."
                    )
                if self.require_nonempty_named_deferrals:
                    request["instructions"] += (
                        "\n\nFor turn_end only: deferAbilities must contain at least one "
                        "exact affordable nonmana ability from the current legalActions. "
                        "Never send an empty deferAbilities array. If no such ability "
                        "is offered, choose a shorter lease or plain PassPriority. "
                        "If one is offered, include every currently affordable "
                        "nonmana ability in the offer, with "
                        "its exact action.sourceId and action.abilityId. "
                        "For a native additional cost, use only the ActionParams "
                        "field named in that offered action's parameterSpec: "
                        "tappedPermanents, sacrificedPermanents, discardedCards, "
                        "or exiledCards. Select entity IDs from the corresponding "
                        "additionalCostInfo valid candidates. The targets field "
                        "selects spell or ability targets, not cost payments."
                    )
            else:
                request["instructions"] += (
                    "\n\nYou may optionally include priorityDelegation only when choosing "
                    "PassPriority. Set until to phase_end or next_own_main and give a "
                    "concrete reason. Optionally set watchOpponents true; by default "
                    "opponent battlefield changes do not end the reviewed wait. This "
                    "delegates later priority windows with only "
                    "mana abilities until the boundary. New spells, nonmana actions, "
                    "required decisions, or changed own hand/board, life, or mana wake "
                    "you. Omit delegation when floating mana or unreviewed events matter."
                )
        if self.allow_declarative_continuation:
            request["instructions"] += (
                "\n\nYou may attach one declarative continuation to a chosen PlayLand "
                "or PassPriority. Choose at most one of continuation, thenCast, "
                "and priorityDelegation. For PlayLand, choose one exact cast cardId. "
                "For PassPriority, choose a bounded wait, optionally followed "
                "by one exact land cardId and one exact cast cardId. An optional "
                "final bounded wait after the cast applies only if you regain "
                "priority over that sole exact spell before it resolves. Each future "
                "action has type, cardId, when, and empty params; when may name "
                "the expected phase, step, and stackEmpty. The cards must "
                "already be visible in your hand or command zone. Gym checks "
                "each fresh native offer and wakes you on any changed state, "
                "decision, stack object, or unknown event. A wait may pass "
                "only when the legal menu has PassPriority and mana abilities. "
                "Never plan targets, modes, X, payment, or an unreviewed spell. "
                "Omit continuation when an intervening choice matters."
            )
        if isinstance(pending, Mapping) and pending.get("requiresStructuredResponse") is True:
            request["instructions"] += (
                "\n\nFor this structured decision, copy the exact responseType from "
                "pendingDecision.responseSpec into response.type and provide every "
                "requiredFields entry with its declared JSON value kind."
            )
            spec = pending.get("responseSpec")
            if isinstance(spec, Mapping) and spec.get("cancelAllowed") is True:
                request["instructions"] += (
                    " If cancelling is appropriate, use CancelDecisionResponse with no "
                    "model-authored fields beyond type."
                )
            legal_actions = observation.get("legalActions")
            if isinstance(spec, Mapping) and spec.get("responseType") == "ManaSourcesSelectedResponse" and isinstance(legal_actions, list) and any(
                isinstance(action, Mapping)
                and action.get("kind") == "ActivateAbility"
                and action.get("isManaAbility") is True
                for action in legal_actions
            ):
                request["instructions"] += (
                    " You may instead choose an offered native mana ability via "
                    "channel action before answering this payment decision. "
                    "For selectedSources, use only IDs in pendingDecision.availableSources."
                )
            if isinstance(spec, Mapping) and spec.get("responseType") == "ManaSourcesSelectedResponse":
                can_auto_pay = _native_auto_pay_available(pending)
                request["instructions"] += (
                    " AutoPay and selectedSources are exclusive: if autoPay is true, "
                    "selectedSources must be empty."
                )
                if can_auto_pay is False:
                    request["instructions"] += (
                        " Native canAutoPayNow is false, so do not submit autoPay true; "
                        "activate an offered mana ability first or choose a valid manual/decline response."
                    )

        if (self.explicit_wait_guidance and action_format is not None
            and _nonmana_ability_keys(observation)):
            request["instructions"] += "\n\n" + EXPLICIT_NAMED_WAIT_INSTRUCTIONS

        validation_error: OpenAIResponsesPilotError | None = None
        attempts: list[dict[str, Any]] = []
        provider_wall_time_ms = 0.0
        validation_retries = 0
        recovery_deadline = (
            perf_counter() + _RECOVERY_CALLBACK_SECONDS
            if self.retry_transient_server_errors and self.budget is not None else None
        )
        for attempt in range(self.max_attempts):
            if validation_error is not None:
                correction = (
                    "The previous response was invalid: " + str(validation_error)
                    + "\nReturn a corrected JSON object using only the current observation."
                )
                request["input"] = (
                    [*base_input, {"role": "user", "content": correction}]
                    if self.cache_friendly_history else base_input + "\n" + correction
                )
            if recovery_deadline is not None:
                remaining = recovery_deadline - perf_counter()
                if remaining < _RECOVERY_MIN_RETRY_SECONDS + _RECOVERY_RETURN_MARGIN_SECONDS:
                    raise OpenAIResponsesPilotError(
                        "bounded provider recovery callback deadline exhausted",
                        model_io=_failed_model_io(attempts) if attempts else None,
                    )
                # SDK timeout is a request option, not part of the model input.
                # Keep each dispatch inside the JVM's 120-second HTTP callback.
                request["timeout"] = min(
                    _RECOVERY_REQUEST_SECONDS,
                    remaining - _RECOVERY_RETURN_MARGIN_SECONDS,
                )
            request_snapshot = deepcopy(request)
            request_started = perf_counter()
            try:
                response = (
                    self.budget.create(self.client.responses.create, request)
                    if self.budget is not None
                    else self.client.responses.create(**request)
                )
            except OpenAIRunBudgetError as exc:
                # A rejected reservation is not a new provider attempt. Preserve
                # any earlier invalid response when the retry hits the cap.
                # Settlement failures happen after dispatch and get their own
                # attempt with the response already returned by the provider.
                if exc.dispatched:
                    elapsed_ms = (perf_counter() - request_started) * 1000
                    response_snapshot = (
                        _provider_response_snapshot(exc.response)
                        if exc.response is not None else {}
                    )
                    response_snapshot.update({
                        "budgetError": str(exc),
                        "providerWallTimeMs": round(elapsed_ms, 3),
                    })
                    attempts.append({
                        "attempt": attempt, "request": request_snapshot,
                        "response": response_snapshot,
                    })
                raise OpenAIResponsesPilotError(
                    str(exc),
                    model_io=_failed_model_io(attempts) if attempts else None,
                ) from exc
            except Exception as exc:
                elapsed_ms = (perf_counter() - request_started) * 1000
                provider_wall_time_ms += elapsed_ms
                failure_summary = _provider_failure_summary(exc)
                attempts.append(
                    {
                        "attempt": attempt,
                        "request": request_snapshot,
                        "response": {"transportError": failure_summary,
                                     "providerWallTimeMs": round(elapsed_ms, 3)},
                    }
                )
                # The bounded SDK has transport retries disabled. Give a transient
                # server response one more independently reserved attempt, within
                # the existing total max_attempts ceiling. Budget rejection and
                # non-server errors remain fail-closed; ambiguous attempts retain
                # their reservation and their model-I/O receipt.
                if (self.retry_transient_server_errors and self.budget is not None
                    and _retryable_provider_status(exc)
                    and attempt + 1 < self.max_attempts
                    and recovery_deadline is not None
                    and recovery_deadline - perf_counter()
                        >= _RECOVERY_MIN_RETRY_SECONDS + _RECOVERY_RETURN_MARGIN_SECONDS):
                    continue
                raise OpenAIResponsesPilotError(
                    f"OpenAI Responses request failed ({failure_summary})",
                    model_io=_failed_model_io(attempts),
                ) from exc

            elapsed_ms = (perf_counter() - request_started) * 1000
            provider_wall_time_ms += elapsed_ms
            response_snapshot = _provider_response_snapshot(response)
            response_snapshot["providerWallTimeMs"] = round(elapsed_ms, 3)
            try:
                choice = self._choice_from_response(
                    response,
                    observation,
                    retry_count=validation_retries,
                )
            except OpenAIResponsesPilotError as exc:
                response_snapshot["validationError"] = str(exc)
                attempts.append(
                    {
                        "attempt": attempt,
                        "request": request_snapshot,
                        "response": response_snapshot,
                    }
                )
                validation_error = exc
                validation_retries += 1
                continue

            attempts.append(
                {
                    "attempt": attempt,
                    "request": request_snapshot,
                    "response": response_snapshot,
                }
            )
            return _with_model_io(
                choice,
                {
                    "schemaVersion": MODEL_IO_SCHEMA_VERSION,
                    "provider": "openai",
                    "selectedAttempt": attempt,
                    "attempts": attempts,
                },
                provider_wall_time_ms,
            )

        assert validation_error is not None
        raise OpenAIResponsesPilotError(
            f"OpenAI pilot exhausted {self.max_attempts} validation attempts: "
            f"{validation_error}",
            model_io=_failed_model_io(attempts),
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
            action_decision = decision.get("choice") if "choice" in decision else decision
            if not isinstance(action_decision, Mapping):
                raise OpenAIResponsesPilotError("OpenAI action choice must be a JSON object")
            choice = self._action_choice(action_decision, observation, metadata)
            directive = decision.get("priorityDelegation")
            then_cast = decision.get("thenCast")
            continuation = decision.get("continuation")
            if continuation is not None:
                if not self.allow_declarative_continuation or directive is not None or then_cast is not None:
                    raise OpenAIResponsesPilotError("continuation requires the versioned Pilot alone")
                legal = observation.get("legalActions")
                selected = next((item for item in legal if isinstance(item, Mapping)
                                 and item.get("actionId") == choice.action_id), None) if isinstance(legal, list) else None
                if not isinstance(continuation, Mapping) or set(continuation) != {"reason", "steps"}:
                    raise OpenAIResponsesPilotError("continuation requires reason and steps")
                steps = continuation.get("steps")
                if (not isinstance(continuation.get("reason"), str)
                    or not continuation["reason"].strip()
                    or not isinstance(steps, list) or not 1 <= len(steps) <= 4
                    or not isinstance(selected, Mapping) or choice.params != {}):
                    raise OpenAIResponsesPilotError("continuation has invalid bounds or current choice")
                kinds = []
                for step in steps:
                    if not isinstance(step, Mapping):
                        raise OpenAIResponsesPilotError("continuation step must be an object")
                    kind = step.get("type")
                    if not isinstance(kind, str):
                        raise OpenAIResponsesPilotError("continuation step type must be a string")
                    if kind == "wait":
                        if (set(step) != {"type", "until", "maxPasses"}
                            or not isinstance(step.get("until"), str)
                            or step.get("until") not in {"phase_end", "next_own_main"}
                            or type(step.get("maxPasses")) is not int
                            or not 1 <= step["maxPasses"] <= 16):
                            raise OpenAIResponsesPilotError("continuation wait must be bounded")
                    elif kind in {"playLand", "cast"}:
                        when = step.get("when")
                        if (set(step) != {"type", "cardId", "when", "params"}
                            or not isinstance(step.get("cardId"), str) or not step["cardId"]
                            or not isinstance(when, Mapping) or "stackEmpty" not in when
                            or set(when) - {"phase", "step", "stackEmpty"}
                            or any(not isinstance(when[key], str) or not when[key]
                                   for key in ("phase", "step") if key in when)
                            or ("stackEmpty" in when and type(when["stackEmpty"]) is not bool)
                            or ("phase" in when and when["phase"] not in _NATIVE_PHASES)
                            or ("step" in when and when["step"] not in _NATIVE_STEPS)
                            or step.get("params") != {}):
                            raise OpenAIResponsesPilotError("continuation action must be exact and guarded")
                    else:
                        raise OpenAIResponsesPilotError("unsupported continuation step")
                    kinds.append(kind)
                selected_kind = selected.get("kind")
                if not isinstance(selected_kind, str):
                    raise OpenAIResponsesPilotError("continuation requires a native action kind")
                if (selected_kind == "PlayLand"
                    and kinds not in (["cast"], ["cast", "wait"])
                    or selected_kind == "PassPriority"
                    and kinds not in (["wait"], ["wait", "playLand"],
                                      ["wait", "playLand", "cast"],
                                      ["wait", "playLand", "cast", "wait"])
                    or selected_kind not in {"PlayLand", "PassPriority"}):
                    raise OpenAIResponsesPilotError("unsupported continuation order")
                return ArgentumActionChoice(
                    action_id=choice.action_id, params=choice.params,
                    metadata={**dict(choice.metadata), "continuation": dict(continuation)},
                )
            if then_cast is not None:
                if not self.allow_named_deferrals or directive is not None:
                    raise OpenAIResponsesPilotError("thenCast requires the versioned action-sequence Pilot")
                legal = observation.get("legalActions")
                selected = next((item for item in legal if isinstance(item, Mapping)
                                 and item.get("actionId") == choice.action_id), None) if isinstance(legal, list) else None
                if (
                    not isinstance(then_cast, Mapping)
                    or set(then_cast) != {"cardId", "reason"}
                    or not isinstance(then_cast.get("cardId"), str) or not then_cast["cardId"]
                    or not isinstance(then_cast.get("reason"), str) or not then_cast["reason"].strip()
                    or not isinstance(selected, Mapping) or selected.get("kind") != "PlayLand"
                ):
                    raise OpenAIResponsesPilotError("thenCast requires an exact PlayLand and card intent")
                return ArgentumActionChoice(
                    action_id=choice.action_id, params=choice.params,
                    metadata={**dict(choice.metadata), "thenCast": dict(then_cast)},
                )
            if directive is None:
                return choice
            if not self.allow_priority_delegation:
                raise OpenAIResponsesPilotError("priority delegation is not enabled for this Pilot")
            named = self.allow_named_deferrals and isinstance(directive, Mapping) and directive.get("until") == "turn_end"
            deferred = directive.get("deferAbilities") if named else None
            if (
                not isinstance(directive, Mapping)
                    or not {"until", "reason"}.issubset(directive)
                    or set(directive) - ({"until", "reason", "deferAbilities", "watchOpponents"} if named else {"until", "reason", "watchOpponents"})
                    or directive.get("until") not in ({"turn_end"} if named else {"phase_end", "next_own_main"})
                    or (named and directive.get("watchOpponents", True) is not True)
                    or (named and (not isinstance(deferred, list) or not deferred or any(
                        not isinstance(item, Mapping) or set(item) != {"sourceId", "abilityId"}
                        or not isinstance(item.get("sourceId"), str)
                        or not isinstance(item.get("abilityId"), str)
                        for item in deferred
                    )))
                    or not isinstance(directive.get("reason"), str)
                    or not directive["reason"].strip()
                    or type(directive.get("watchOpponents", False)) is not bool
                or not any(
                    isinstance(action, Mapping)
                    and action.get("actionId") == choice.action_id
                    and action.get("kind") == "PassPriority"
                    for action in observation.get("legalActions", [])
                )
            ):
                raise OpenAIResponsesPilotError(
                    "priority delegation requires an exact current PassPriority and bounded reason"
                )
            return ArgentumActionChoice(
                action_id=choice.action_id,
                params=choice.params,
                metadata={**dict(choice.metadata), "priorityDelegation": dict(directive)},
            )
        if channel == "decision":
            if "priorityDelegation" in decision or "thenCast" in decision or "continuation" in decision:
                raise OpenAIResponsesPilotError(
                    "priority delegation or action sequence cannot accompany a structured decision"
                )
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
                f"selected semanticId {semantic_id!r} is not exactly one current "
                "Argentum legal action"
            )
        action_id = matches[0].get("actionId")
        if type(action_id) is not int:
            raise OpenAIResponsesPilotError(
                "selected Argentum legal action is missing integer actionId"
            )
        return ArgentumActionChoice(
            action_id=action_id,
            params=_validate_native_action_params(params, matches[0]),
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
        spec = pending.get("responseSpec")
        if not isinstance(spec, Mapping):
            raise OpenAIResponsesPilotError(
                "Argentum structured decision is missing its native responseSpec"
            )
        expected_type = _require_string(spec.get("responseType"), "native responseType")
        if response_type == "CancelDecisionResponse" and spec.get("cancelAllowed") is True:
            if set(response) != {"type"}:
                raise OpenAIResponsesPilotError(
                    "native cancel response only permits type"
                )
            return ArgentumDecisionChoice(
                response={"type": response_type, "decisionId": decision_id},
                metadata=dict(metadata),
            )
        if response_type != expected_type:
            raise OpenAIResponsesPilotError(
                f"structured decision requires native Argentum response type {expected_type}"
            )
        required = spec.get("requiredFields")
        if not isinstance(required, Mapping):
            raise OpenAIResponsesPilotError("native responseSpec requiredFields must be an object")
        if any(not isinstance(field, str) for field in required):
            raise OpenAIResponsesPilotError("native responseSpec field names must be strings")
        optional = _NATIVE_OPTIONAL_RESPONSE_FIELDS.get(response_type, {})
        allowed_fields = {"type", *required, *optional}
        unexpected = set(response) - allowed_fields
        if unexpected:
            raise OpenAIResponsesPilotError(
                f"native response has unsupported fields: {sorted(unexpected)}"
            )
        for field, kind in required.items():
            if kind not in _NATIVE_DECISION_FIELD_SCHEMAS:
                raise OpenAIResponsesPilotError(
                    f"unsupported native response field kind {kind!r}"
                )
            value = response.get(field)
            if not _matches_native_response_field(value, response_type, field, kind):
                raise OpenAIResponsesPilotError(
                    f"native response field {field} requires {kind}"
                )
        for field, kind in optional.items():
            if field in response and not _matches_native_response_field(
                response[field], response_type, field, kind,
            ):
                raise OpenAIResponsesPilotError(
                    f"native response field {field} requires {kind}"
                )

        if response_type == "ManaSourcesSelectedResponse":
            if response.get("autoPay") is True and _native_auto_pay_available(pending) is False:
                raise OpenAIResponsesPilotError("native canAutoPayNow is false; autoPay is unavailable")
            if response.get("autoPay") is True and response.get("selectedSources"):
                raise OpenAIResponsesPilotError("autoPay cannot be combined with selectedSources")
            available = pending.get("availableSources")
            selected_sources = response.get("selectedSources")
            if not isinstance(available, list) or any(
                not isinstance(source, Mapping)
                or not isinstance(source.get("entityId"), str)
                for source in available
            ) or not _matches_native_field_kind(selected_sources, "ENTITY_ID_ARRAY"):
                raise OpenAIResponsesPilotError(
                    "native mana-source decision requires exact availableSources and selectedSources"
                )
            offered_ids = {source["entityId"] for source in available}
            if any(source_id not in offered_ids for source_id in selected_sources):
                raise OpenAIResponsesPilotError(
                    "native response selectedSources must be offered in availableSources"
                )
        if response_type == "TargetsResponse":
            legal_targets = pending.get("legalTargets")
            selected_targets = response.get("selectedTargets")
            if not isinstance(legal_targets, Mapping) or not isinstance(selected_targets, Mapping):
                raise OpenAIResponsesPilotError(
                    "native target decision requires exact legalTargets and selectedTargets"
                )
            for index, ids in selected_targets.items():
                offered = legal_targets.get(index)
                if (
                    not isinstance(index, str) or not index.isdecimal()
                    or not _matches_native_field_kind(offered, "ENTITY_ID_ARRAY")
                    or not _matches_native_field_kind(ids, "ENTITY_ID_ARRAY")
                    or any(target_id not in offered for target_id in ids)
                ):
                    raise OpenAIResponsesPilotError(
                        "native response selectedTargets requires arrays of offered target IDs"
                    )
        card_options = _native_card_options(pending)
        if card_options is not None and "selectedCards" in response and any(
            card_id not in card_options for card_id in response["selectedCards"]
        ):
            raise OpenAIResponsesPilotError(
                "native response selectedCards must be offered in options"
            )

        submitted = dict(response)
        submitted["decisionId"] = decision_id
        return ArgentumDecisionChoice(response=submitted, metadata=dict(metadata))
