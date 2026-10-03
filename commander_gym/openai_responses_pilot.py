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
from .openai_run_budget import OpenAIRunBudget, OpenAIRunBudgetError

MODEL_IO_SCHEMA_VERSION = 1

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
        return type(value) is int
    if kind == "STRING":
        return isinstance(value, str)
    if kind == "ENTITY_ID_ARRAY":
        return isinstance(value, list) and all(isinstance(item, str) for item in value)
    if kind == "INTEGER_ARRAY":
        return isinstance(value, list) and all(type(item) is int for item in value)
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


def _native_action_format(
    observation: Mapping[str, Any], *, allow_priority_delegation: bool = False,
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
                        name: _ACTION_PARAM_SCHEMAS[kind] for name, kind in fields.items()
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
                "until": {"type": "string", "enum": ["phase_end", "next_own_main"]},
                "reason": {"type": "string"},
                "watchOpponents": {"type": "boolean"},
            },
            "required": ["until", "reason"],
            "additionalProperties": False,
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


def _native_mana_source_format(observation: Mapping[str, Any]) -> dict[str, Any] | None:
    """Constrain a native mana-source response to this decision's offered IDs."""

    pending = observation.get("pendingDecision")
    if not isinstance(pending, Mapping) or pending.get("requiresStructuredResponse") is not True:
        return None
    spec = pending.get("responseSpec")
    if not isinstance(spec, Mapping) or spec.get("responseType") != "ManaSourcesSelectedResponse":
        return None
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
                        "autoPay": {"type": "boolean"},
                        "declined": {"type": "boolean"},
                        "selectedSources": {
                            "type": "array", "items": {"type": "string", "enum": offered_ids},
                        },
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
    Responses JSON mode for most native structured decisions; a mana-source
    decision uses a request-local schema constrained to Argentum's offered IDs.
    Commander Gym parses and validates the returned channel locally, then the existing
    pilot/execution validators and Argentum perform authoritative validation.
    """

    client: Any
    model: str
    instructions: str = _DEFAULT_INSTRUCTIONS
    strategy: str | None = None
    max_attempts: int = 2
    budget: OpenAIRunBudget | None = None
    allow_priority_delegation: bool = False
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
        mana_source_format = _native_mana_source_format(observation)
        action_format = None if mana_source_format is not None else _native_action_format(
            observation, allow_priority_delegation=self.allow_priority_delegation,
        )
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
            # The Responses JSON-object mode requires the user input itself to name
            # JSON; mentioning it only in instructions is not sufficient.
            "input": base_input,
            "text": {"format": mana_source_format or action_format or {"type": "json_object"}},
            "store": False,
        }
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
        if isinstance(pending, Mapping) and pending.get("requiresStructuredResponse") is True:
            request["instructions"] += (
                "\n\nFor this structured decision, copy the exact responseType from "
                "pendingDecision.responseSpec into response.type and provide every "
                "requiredFields entry with its declared JSON value kind."
            )
            spec = pending.get("responseSpec")
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

        validation_error: OpenAIResponsesPilotError | None = None
        attempts: list[dict[str, Any]] = []
        provider_wall_time_ms = 0.0
        for attempt in range(self.max_attempts):
            if validation_error is not None:
                request["input"] = (
                    base_input
                    + "\nThe previous response was invalid: "
                    + str(validation_error)
                    + "\nReturn a corrected JSON object using only the current observation."
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
                # A pre-dispatch cap rejection is not a provider attempt.
                raise OpenAIResponsesPilotError(str(exc)) from exc
            except Exception as exc:  # Provider SDK owns transport-level retries.
                elapsed_ms = (perf_counter() - request_started) * 1000
                failure_summary = _provider_failure_summary(exc)
                attempts.append(
                    {
                        "attempt": attempt,
                        "request": request_snapshot,
                        "response": {"transportError": failure_summary,
                                     "providerWallTimeMs": round(elapsed_ms, 3)},
                    }
                )
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
                    retry_count=attempt,
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
            if directive is None:
                return choice
            if not self.allow_priority_delegation:
                raise OpenAIResponsesPilotError("priority delegation is not enabled for this Pilot")
            if (
                not isinstance(directive, Mapping)
                    or not {"until", "reason"}.issubset(directive)
                    or set(directive) - {"until", "reason", "watchOpponents"}
                    or directive.get("until") not in {"phase_end", "next_own_main"}
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
            if "priorityDelegation" in decision:
                raise OpenAIResponsesPilotError(
                    "priority delegation cannot accompany a structured decision"
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
        if response_type != expected_type:
            raise OpenAIResponsesPilotError(
                f"structured decision requires native Argentum response type {expected_type}"
            )
        required = spec.get("requiredFields")
        if not isinstance(required, Mapping):
            raise OpenAIResponsesPilotError("native responseSpec requiredFields must be an object")
        if any(not isinstance(field, str) for field in required):
            raise OpenAIResponsesPilotError("native responseSpec field names must be strings")
        allowed_fields = {"type", *required}
        unexpected = set(response) - allowed_fields
        if unexpected:
            raise OpenAIResponsesPilotError(
                f"native response has unsupported fields: {sorted(unexpected)}"
            )
        for field, kind in required.items():
            if kind not in {
                "BOOLEAN", "INTEGER", "STRING", "ENTITY_ID_ARRAY", "INTEGER_ARRAY",
                "ENTITY_ID_ARRAY_ARRAY", "MAP", "DAMAGE_EDGE_AMOUNT_ARRAY",
            }:
                raise OpenAIResponsesPilotError(
                    f"unsupported native response field kind {kind!r}"
                )
            value = response.get(field)
            if not _matches_native_field_kind(value, kind):
                raise OpenAIResponsesPilotError(
                    f"native response field {field} requires {kind}"
                )

        if response_type == "ManaSourcesSelectedResponse":
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

        submitted = dict(response)
        submitted["decisionId"] = decision_id
        return ArgentumDecisionChoice(response=submitted, metadata=dict(metadata))
