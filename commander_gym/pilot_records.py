"""Bridge Argentum-native pilot execution traces into durable research records.

The live pilot/execution contract keeps exact Argentum observations and routing handles
because they are required to execute safely.  Durable training/evaluation records need
a different boundary: semantic action identity must survive equivalent runs, while
per-step ``actionId`` / ``decisionId`` routing nonces must not become model features or
Commander Gym's canonical action ontology.

This module converts successfully executed *enumerated action* traces into the existing
:class:`DecisionRecord` schema.  It copies Argentum-owned ``semanticId`` values directly
for candidate/chosen identities, strips only live routing handles from the recorded
observation/candidate payloads, and keeps those handles plus the exact submitted payload
in metadata for diagnostics.  Complex structured decisions remain fail-closed until the
durable record schema can represent a non-enumerable native response space without
fabricating legal actions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .pilot_execution import PilotExecutionTrace
from .records import (
    ActionRecord,
    DecisionRecord,
    PilotProvenance,
    RecordValidationError,
)


class PilotRecordError(RecordValidationError):
    """Raised when an execution trace cannot be represented durably without invention."""


@dataclass(frozen=True)
class PilotRecordContext:
    """Run-level identity needed to join one execution trace to durable evidence."""

    game_id: str
    decision_id: str
    seat: int
    deck_id: str
    deck_version: str
    primer_version: str | None = None
    pilot_source: str = "commander-gym"


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise PilotRecordError(f"{label} must be a non-empty string")
    return value


def _without_live_routing(observation: Mapping[str, Any]) -> dict[str, Any]:
    """Copy one seat-authorized observation without ephemeral execution handles.

    The rest of the Argentum wire shape is preserved verbatim.  In particular this does
    not reconstruct state, reinterpret entities, or create a Commander Gym observation
    schema.  The raw unsanitized observation remains available on ``PilotExecutionTrace``
    for execution diagnostics.
    """

    recorded = dict(observation)

    legal = observation.get("legalActions")
    if isinstance(legal, list):
        recorded_legal: list[Any] = []
        for item in legal:
            if isinstance(item, Mapping):
                candidate = dict(item)
                candidate.pop("actionId", None)
                recorded_legal.append(candidate)
            else:
                recorded_legal.append(item)
        recorded["legalActions"] = recorded_legal

    pending = observation.get("pendingDecision")
    if isinstance(pending, Mapping):
        recorded_pending = dict(pending)
        recorded_pending.pop("decisionId", None)
        recorded["pendingDecision"] = recorded_pending

    return recorded


def _action_records(observation: Mapping[str, Any]) -> list[ActionRecord]:
    legal = observation.get("legalActions")
    if not isinstance(legal, list) or not legal:
        raise PilotRecordError(
            "durable action record requires a non-empty Argentum legalActions set"
        )

    records: list[ActionRecord] = []
    semantic_ids: set[str] = set()
    for item in legal:
        if not isinstance(item, Mapping):
            raise PilotRecordError("each Argentum legal action must be an object")
        semantic_id = _require_string(
            item.get("semanticId"),
            "each recorded Argentum legal action semanticId",
        )
        if semantic_id in semantic_ids:
            raise PilotRecordError("Argentum legal action semanticId values must be unique")
        semantic_ids.add(semantic_id)

        payload = dict(item)
        payload.pop("actionId", None)
        payload.pop("semanticId", None)
        description = item.get("description")
        label = description if isinstance(description, str) else None
        records.append(ActionRecord(action_id=semantic_id, payload=payload, label=label))

    return records


def _decision_type(trace: PilotExecutionTrace) -> str:
    observation = trace.observation
    pending = observation.get("pendingDecision")
    if isinstance(pending, Mapping):
        kind = pending.get("kind")
        if isinstance(kind, str) and kind:
            return kind

    legal = observation.get("legalActions")
    if isinstance(legal, list):
        for item in legal:
            if (
                isinstance(item, Mapping)
                and item.get("semanticId") == trace.semantic_id
            ):
                kind = item.get("kind")
                if isinstance(kind, str) and kind:
                    return kind
    return "LEGAL_ACTION"


def _optional_model(metadata: Mapping[str, Any]) -> str | None:
    model = metadata.get("model")
    return model if isinstance(model, str) and model else None


def decision_record_from_execution_trace(
    trace: PilotExecutionTrace,
    context: PilotRecordContext,
) -> DecisionRecord:
    """Convert one successful enumerated Argentum action trace into ``DecisionRecord``.

    ``DecisionRecord`` v1 assumes an enumerable legal candidate set.  Argentum complex
    structured decisions intentionally expose no ``legalActions`` and accept a typed
    response payload instead, so representing one as a synthetic single "action" would
    create exactly the parallel ontology #6 forbids.  Those traces therefore fail closed
    here until the durable record schema grows a native structured-response channel.
    """

    if not isinstance(trace, PilotExecutionTrace):
        raise PilotRecordError("trace must be PilotExecutionTrace")
    if trace.channel != "action":
        raise PilotRecordError(
            "DecisionRecord v1 cannot represent non-enumerable structured decision responses"
        )

    observation = trace.observation
    if not isinstance(observation, Mapping):
        raise PilotRecordError("trace observation must be an object")
    result = trace.result_observation
    if not isinstance(result, Mapping):
        raise PilotRecordError("trace result observation must be an object")

    schema = _require_string(observation.get("schemaHash"), "Argentum schemaHash")
    state_digest = _require_string(observation.get("stateDigest"), "Argentum stateDigest")
    result_digest = _require_string(result.get("stateDigest"), "result Argentum stateDigest")
    semantic_id = _require_string(trace.semantic_id, "chosen Argentum semanticId")

    legal_actions = _action_records(observation)
    legal_ids = {action.action_id for action in legal_actions}
    if semantic_id not in legal_ids:
        raise PilotRecordError(
            "chosen Argentum semanticId is not present in the durable legal action set"
        )

    metadata = {
        "channel": trace.channel,
        "native_semantic_id": semantic_id,
        "live_routing_id": trace.live_routing_id,
        "submitted": dict(trace.submitted),
        "pilot_metadata": dict(trace.pilot_metadata),
        "input_state_digest": state_digest,
        "result_state_digest": result_digest,
    }

    record = DecisionRecord(
        game_id=context.game_id,
        decision_id=context.decision_id,
        decision_type=_decision_type(trace),
        seat=context.seat,
        observation_schema=schema,
        observation=_without_live_routing(observation),
        legal_actions=legal_actions,
        chosen_action_id=semantic_id,
        pilot=PilotProvenance(
            source=context.pilot_source,
            implementation=trace.pilot_name,
            version=trace.pilot_version,
            model=_optional_model(trace.pilot_metadata),
        ),
        deck_id=context.deck_id,
        deck_version=context.deck_version,
        primer_version=context.primer_version,
        outcome={"result_observation": _without_live_routing(result)},
        metadata=metadata,
    )
    record.validate()
    return record
