"""Opt-in llmster Chat Completions pilot; native validation stays in Gym/Argentum.

The OpenAI pilot supplies the existing request-local native schemas and choice
validator only. Its client is never called. No local failure routes to a cloud pilot.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import threading
from time import monotonic
from types import SimpleNamespace
from typing import Any, Mapping

from .lmstudio_provider import LMStudioConfig, LMStudioError, LMStudioTransport, require_loaded_model
from .local_callback_context import current_cancel
from .openai_responses_pilot import (
    OpenAIResponsesPilot, OpenAIResponsesPilotError, _DEFAULT_INSTRUCTIONS,
    _native_action_format, _native_decision_format, _without_live_routing,
)
from .pilot import PilotChoice, PilotContractError, _require_observation, validate_pilot_choice


class LMStudioPilotError(OpenAIResponsesPilotError):
    """Uses the existing failure-receipt contract, carrying provider=lmstudio."""


def _local_native_format(observation: Mapping[str, Any]) -> dict[str, Any] | None:
    """Compose existing native schemas without hiding either payment channel.

    The OpenAI builders intentionally use JSON mode for mixed payment windows.
    llmster needs a constrained union instead. Builder-only views isolate the two
    branches; the model and final validator still receive the unchanged live offer.
    """
    pending = observation.get("pendingDecision")
    spec = pending.get("responseSpec") if isinstance(pending, Mapping) else None
    if (isinstance(pending, Mapping) and pending.get("requiresStructuredResponse") is True
        and isinstance(spec, Mapping) and spec.get("responseType") == "ManaSourcesSelectedResponse"):
        legal = observation.get("legalActions")
        if not isinstance(legal, list) or any(not isinstance(action, Mapping) for action in legal):
            return None
        mana_actions = [action for action in legal
                        if action.get("kind") == "ActivateAbility" and action.get("isManaAbility") is True]
        if mana_actions:
            decision_format = _native_decision_format({**observation, "legalActions": []})
            action_format = _native_action_format({**observation, "pendingDecision": None,
                                                  "legalActions": mana_actions})
            if decision_format is None or action_format is None:
                return None  # Never silently drop an unrepresentable native channel.
            return {"type": "json_schema", "name": "commander_gym_native_mixed_payment", "strict": False,
                    "schema": {"type": "object", "anyOf": [action_format["schema"], decision_format["schema"]]}}
    return _native_decision_format(observation) or _native_action_format(observation)


def _schema_check(schema, value=None, *, validate=False):
    """Fail closed on unsupported schema keywords; validate the supported wire subset.

    This is deliberately not a general JSON Schema implementation. Never remove a
    constraint to make a grammar compile. Native semantic validators still run after it.
    """
    allowed = {"type", "properties", "required", "additionalProperties", "items",
               "anyOf", "const", "enum", "minimum", "maximum", "minItems", "maxItems", "description"}
    if not isinstance(schema, dict) or set(schema) - allowed:
        raise LMStudioError("unsupported_json_schema")
    if "anyOf" in schema:
        branches = schema["anyOf"]
        if not isinstance(branches, list) or not branches:
            raise LMStudioError("unsupported_json_schema")
        for branch in branches:
            _schema_check(branch)
        if validate:
            for branch in branches:
                try:
                    _schema_check(branch, value, validate=True)
                    break
                except LMStudioError:
                    pass
            else:
                raise LMStudioError("output_schema_mismatch")
    kind = schema.get("type")
    types = {"object": dict, "array": list, "string": str, "integer": int, "boolean": bool}
    if kind is not None and kind not in types:
        raise LMStudioError("unsupported_json_schema")
    if validate:
        if kind is not None and type(value) is not types[kind]:
            raise LMStudioError("output_schema_mismatch")
        if "const" in schema and (type(value) is not type(schema["const"]) or value != schema["const"]):
            raise LMStudioError("output_schema_mismatch")
        if "enum" in schema and not any(type(value) is type(item) and value == item for item in schema["enum"]):
            raise LMStudioError("output_schema_mismatch")
        if kind == "integer" and not schema.get("minimum", value) <= value <= schema.get("maximum", value):
            raise LMStudioError("output_schema_mismatch")
    if kind == "object":
        properties = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for sub in properties.values():
            _schema_check(sub)
        if isinstance(extra, dict):
            _schema_check(extra)
        if validate:
            if set(schema.get("required", [])) - set(value):
                raise LMStudioError("output_schema_mismatch")
            for key, item in value.items():
                if key in properties:
                    _schema_check(properties[key], item, validate=True)
                elif extra is False:
                    raise LMStudioError("output_schema_mismatch")
                elif isinstance(extra, dict):
                    _schema_check(extra, item, validate=True)
    if kind == "array":
        if "items" not in schema:
            raise LMStudioError("unsupported_json_schema")
        _schema_check(schema["items"])
        if validate:
            if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", len(value)):
                raise LMStudioError("output_schema_mismatch")
            for item in value:
                _schema_check(schema["items"], item, validate=True)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise LMStudioError("duplicate_json_key")
        result[key] = value
    return result


def _parse_final(response, model):
    if response.get("error") or response.get("model") != model:
        raise LMStudioError("provider_error_or_model_mismatch")
    choices = response.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise LMStudioError("expected_one_choice")
    choice = choices[0]
    if choice.get("finish_reason") != "stop":
        raise LMStudioError("incomplete_completion")
    message = choice.get("message")
    if (not isinstance(message, dict) or message.get("role") != "assistant"
        or message.get("tool_calls") or message.get("function_call") or message.get("refusal")):
        raise LMStudioError("unsupported_tool_or_refusal")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise LMStudioError("empty_final_content")
    # Separate reasoning_content is diagnostic only. Never extract JSON from it,
    # markdown fences, <think> blocks, or partial/truncated completions.
    try:
        parsed = json.loads(content, object_pairs_hook=_unique_object,
                            parse_constant=lambda _: (_ for _ in ()).throw(LMStudioError("nonfinite_json")))
    except (ValueError, TypeError) as exc:
        raise LMStudioError("malformed_final_json") from exc
    return content, parsed


class LMStudioPilot:
    name = "lmstudio-chat-completions"
    version = "1"

    def __init__(self, config: LMStudioConfig, *, transport=None,
                 cancel: threading.Event | None = None):
        self.config = config
        self.transport = transport if transport is not None else LMStudioTransport(config)
        self.cancel = cancel

    def choose(self, observation: Mapping[str, Any]) -> PilotChoice:
        _require_observation(observation)
        cancel = self.cancel if self.cancel is not None else current_cancel.get()
        started = monotonic()
        deadline = started + self.config.timeout_seconds
        attempts = []
        schema_format = _local_native_format(observation)
        if schema_format is None:
            raise LMStudioPilotError("llmster requires a supported native JSON schema; no JSON-mode downgrade")
        try:
            _schema_check(schema_format["schema"])
        except LMStudioError as exc:
            raise LMStudioPilotError(str(exc)) from exc
        instructions = _DEFAULT_INSTRUCTIONS
        if schema_format["name"] in {"commander_gym_native_action", "commander_gym_native_mixed_payment"}:
            instructions += "\nUse the schema's nested choice object containing semanticId and params."
        if schema_format["name"] == "commander_gym_native_mixed_payment":
            instructions += (
                "\nFor this payment window, choose either an explicitly offered native mana ability "
                "via channel action and nested choice, or the pending native payment response via "
                "channel decision and response. Use only availableSources for selectedSources. "
                "If canAutoPayNow is false, do not use autoPay. If autoPay is true, selectedSources "
                "must be empty. CancelDecisionResponse is allowed only when the schema permits it."
            )
        base_input = json.dumps(_without_live_routing(observation), sort_keys=True, separators=(",", ":"))
        request = {
            "model": self.config.model,
            "messages": [{"role": "system", "content": instructions}, {"role": "user", "content": base_input}],
            "response_format": {"type": "json_schema", "json_schema": {
                key: deepcopy(value) for key, value in schema_format.items() if key != "type"
            }},
            "max_tokens": self.config.max_output_tokens,
            "stream": False, "reasoning_effort": "none", "temperature": 0,
        }
        validator = OpenAIResponsesPilot(
            client=SimpleNamespace(responses=SimpleNamespace(create=lambda **_: None)), model=self.config.model,
        )

        def fail(code):
            raise LMStudioPilotError(code, model_io={
                "schemaVersion": 1, "provider": "lmstudio", "selectedAttempt": None,
                "attempts": deepcopy(attempts),
            } if attempts else None)

        def remaining():
            if cancel is not None and cancel.is_set():
                raise LMStudioError("cancelled")
            value = deadline - monotonic()
            if value <= 0:
                raise LMStudioError("deadline_exceeded")
            return value

        for index in range(self.config.max_attempts):
            # One token per serialized UTF-8 byte plus an explicit template reserve
            # is conservative for qualified byte-token models. No prompt truncation.
            if len(json.dumps(request, ensure_ascii=False).encode()) > self.config.max_prompt_bytes:
                fail("local_prompt_byte_limit_exceeded")
            try:
                discovery = self.transport.request("/api/v1/models", None, timeout=remaining(), cancel=cancel)
                identity = require_loaded_model(discovery, self.config)
                remaining()
            except LMStudioError as exc:
                fail(str(exc))
            attempt = {"attempt": index, "request": deepcopy(request), "response": {}}
            attempts.append(attempt)
            dispatch = monotonic()
            try:
                response = self.transport.request("/v1/chat/completions", request, timeout=remaining(), cancel=cancel)
            except LMStudioError as exc:
                attempt["response"] = {"transportError": str(exc), "httpStatus": exc.status,
                                       "providerWallTimeMs": round((monotonic() - dispatch) * 1000, 3)}
                if exc.status in {502, 503, 504} and index + 1 < self.config.max_attempts:
                    continue
                fail(str(exc))
            snapshot = attempt["response"]
            snapshot.update({"providerWallTimeMs": round((monotonic() - dispatch) * 1000, 3),
                             "modelIdentity": identity})
            for source, target in (("id", "responseId"), ("model", "responseModel"),
                                   ("usage", "usage"), ("stats", "stats")):
                if source in response:
                    snapshot[target] = deepcopy(response[source])
            choices = response.get("choices")
            if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                snapshot["finishReason"] = choices[0].get("finish_reason")
                message = choices[0].get("message")
                if isinstance(message, dict):
                    for source, target in (("content", "outputText"), ("reasoning_content", "reasoningContent")):
                        if isinstance(message.get(source), str):
                            snapshot[target] = message[source]
            try:
                remaining()  # Discard late or cancelled results before selection.
                content, parsed = _parse_final(response, self.config.model)
                _schema_check(schema_format["schema"], parsed, validate=True)
                choice = validator._choice_from_response(
                    {"status": "completed", "output_text": content}, observation, retry_count=index,
                )
                choice = validate_pilot_choice(choice, observation)
            except (LMStudioError, OpenAIResponsesPilotError, PilotContractError) as exc:
                snapshot["validationError"] = str(exc)
                if str(exc) in {"cancelled", "deadline_exceeded"}:
                    fail(str(exc))
                request["messages"][1]["content"] = base_input + "\nPrevious output was invalid. Return JSON matching the schema and current native offer."
                continue
            metadata = {**dict(choice.metadata), "provider": "lmstudio", "model": self.config.model,
                        "providerWallTimeMs": round(sum(a["response"]["providerWallTimeMs"] for a in attempts), 3),
                        "modelIo": {"schemaVersion": 1, "provider": "lmstudio",
                                    "selectedAttempt": index, "attempts": attempts}}
            if "usage" in snapshot:
                metadata["usage"] = snapshot["usage"]  # Preserve LM Studio names; absent != zero.
            return replace(choice, metadata=metadata)
        fail("llmster_exhausted_validation_attempts")


def load_lmstudio_pilot(path: str | Path) -> LMStudioPilot:
    """Explicit configuration entry point; never consult ambient provider variables."""
    value = json.loads(Path(path).read_text())
    if not isinstance(value, dict) or value.get("provider") != "lmstudio":
        raise ValueError("configuration must explicitly select provider lmstudio")
    return LMStudioPilot(LMStudioConfig(**value))
