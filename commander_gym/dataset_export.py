"""Deterministic dataset export from durable Commander Gym run evidence.

The exporter consumes already-recorded Commander Gym evidence; it does not inspect or
reconstruct authoritative game state.  Dataset rows explicitly separate model-facing
inputs from targets and provenance so chosen actions, outcomes, diagnostic metadata,
and private package identity are not silently mixed into the input feature surface.

Actual run artifacts may contain private deck identities and seat observations.  This
module writes only to a caller-supplied local path and never publishes or uploads data.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable, Sequence

from .records import DecisionRecord, RecordValidationError, StructuredDecisionRecord
from .run_records import RunRecord
from .training_guard import benchmark_decision_ids

DATASET_EXPORT_SCHEMA_VERSION = 1

EvidenceRecord = DecisionRecord | StructuredDecisionRecord


class DatasetExportError(RecordValidationError):
    """Raised when durable run evidence cannot be exported without ambiguity."""


def _record_kind(record: EvidenceRecord) -> str:
    if isinstance(record, DecisionRecord):
        return "action"
    if isinstance(record, StructuredDecisionRecord):
        return "structured_decision"
    raise DatasetExportError(
        "dataset records must be DecisionRecord or StructuredDecisionRecord values"
    )


def _input_for_record(record: EvidenceRecord) -> dict[str, Any]:
    """Return only information available when the recorded choice was made."""

    common = {
        "decision_type": record.decision_type,
        "seat": record.seat,
        "observation_schema": record.observation_schema,
        "observation": record.observation,
    }
    if isinstance(record, DecisionRecord):
        return {
            **common,
            "legal_actions": [asdict(action) for action in record.legal_actions],
        }
    if isinstance(record, StructuredDecisionRecord):
        return {
            **common,
            "native_decision_semantic_id": record.native_decision_semantic_id,
        }
    raise DatasetExportError(
        "dataset records must be DecisionRecord or StructuredDecisionRecord values"
    )


def _target_for_record(record: EvidenceRecord) -> dict[str, Any]:
    if isinstance(record, DecisionRecord):
        return {"chosen_action_id": record.chosen_action_id}
    if isinstance(record, StructuredDecisionRecord):
        return {"response": record.response}
    raise DatasetExportError(
        "dataset records must be DecisionRecord or StructuredDecisionRecord values"
    )


def _record_provenance(record: EvidenceRecord) -> dict[str, Any]:
    """Keep reproducibility/diagnostic fields outside the model-facing input."""

    return {
        "game_id": record.game_id,
        "decision_id": record.decision_id,
        "record_schema_version": record.schema_version,
        "pilot": asdict(record.pilot),
        "deck_id": record.deck_id,
        "deck_version": record.deck_version,
        "primer_version": record.primer_version,
        "outcome": record.outcome,
        "metadata": record.metadata,
    }


def build_run_dataset_rows(
    run: RunRecord,
    records: Iterable[EvidenceRecord],
    *,
    held_out_decision_ids: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Build self-contained research rows from one validated run.

    The run's ``decision_ids`` list is treated as the authoritative ordered join to
    the supplied durable decision records.  Export fails closed if a record is
    missing, reordered, duplicated, belongs to another game, or overlaps a held-out
    benchmark decision.
    """

    if not isinstance(run, RunRecord):
        raise DatasetExportError("run must be a RunRecord")
    run.validate()

    materialized = list(records)
    record_ids: list[str] = []
    for record in materialized:
        _record_kind(record)
        record.validate()
        record_ids.append(record.decision_id)
        if record.game_id != run.game_id:
            raise DatasetExportError(
                f"decision {record.decision_id!r} belongs to game {record.game_id!r}, "
                f"not run game {run.game_id!r}"
            )

    if len(record_ids) != len(set(record_ids)):
        raise DatasetExportError("dataset decision_ids must be unique")
    if record_ids != run.decision_ids:
        raise DatasetExportError(
            "run.decision_ids must exactly match exported records in order"
        )

    held_out = set(held_out_decision_ids)
    if any(not isinstance(decision_id, str) or not decision_id for decision_id in held_out):
        raise DatasetExportError("held-out decision ids must be non-empty strings")
    leaked = sorted(set(record_ids) & held_out)
    if leaked:
        raise DatasetExportError(
            "held-out benchmark decisions must not be exported into a training dataset: "
            + ", ".join(leaked)
        )

    run_provenance = {
        "run_id": run.run_id,
        "experiment_id": run.experiment_id,
        "benchmark_id": run.benchmark_id,
        "seed": run.seed,
        "engine": asdict(run.engine),
    }
    rows: list[dict[str, Any]] = []
    for record in materialized:
        rows.append(
            {
                "dataset_schema_version": DATASET_EXPORT_SCHEMA_VERSION,
                "record_kind": _record_kind(record),
                "input": _input_for_record(record),
                "target": _target_for_record(record),
                "provenance": {
                    **run_provenance,
                    **_record_provenance(record),
                },
            }
        )
    return rows


def write_run_dataset_jsonl(
    path: str | os.PathLike[str],
    run: RunRecord,
    records: Iterable[EvidenceRecord],
    *,
    benchmark_paths: Sequence[str | os.PathLike[str]] = (),
) -> None:
    """Atomically write one run as deterministic JSONL training/evaluation evidence.

    ``benchmark_paths`` names held-out benchmark JSONL files.  Their decision IDs are
    checked before any destination file is replaced, so benchmark evidence cannot be
    silently re-ingested as training data through this export path.
    """

    held_out = benchmark_decision_ids(Path(item) for item in benchmark_paths)
    rows = build_run_dataset_rows(
        run,
        records,
        held_out_decision_ids=held_out,
    )

    target = Path(path)
    if not target.parent.is_dir():
        raise DatasetExportError(
            f"dataset parent directory does not exist: {target.parent}"
        )

    payload = "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows
    )
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = handle.name
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)
