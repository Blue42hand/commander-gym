"""Logical target version selection; physical framing and run identity are unchanged."""
from typing import Iterable

from .records import DecisionRecord, RecordValidationError, StructuredDecisionRecord


def target_schema_version(records: Iterable[DecisionRecord | StructuredDecisionRecord],
                          requested: int | None) -> int:
    versions = {record.schema_version for record in records if isinstance(record, DecisionRecord)}
    selected = (2 if 2 in versions else 1) if requested is None else requested
    if type(selected) is not int or selected not in {1, 2}:
        raise RecordValidationError("unsupported canonical target schema version")
    if versions - {selected}:
        raise RecordValidationError("canonical targets cannot infer or discard action parameters across versions")
    return selected


def action_target(record: DecisionRecord) -> dict:
    record.validate()
    target = {"chosen_action_id": record.chosen_action_id}
    if record.schema_version == 2:
        target["chosen_action_params"] = record.chosen_action_params
    return target
