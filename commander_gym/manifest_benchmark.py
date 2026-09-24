"""Model-independent evaluation over exact frozen #53 dataset evidence.

This module deliberately reuses the settled dataset-manifest, raw-evidence, and
benchmark contracts.  It does not define another corpus.  A benchmark is materialized
from one cataloged ``frozen_test`` split, projected through the existing leakage-safe
``BenchmarkInput`` boundary, and evaluated by candidate and teacher policies on the
same immutable cases.

Reference choices and adjudication provenance remain runner-side scoring evidence.
Only the seat-visible decision input is delivered to evaluated policies.  Evaluation
results may be stored as deterministic content-addressed artifacts through the #74
storage substrate; runtime latency/token/cost telemetry is intentionally excluded from
the deterministic artifact because it is not reproducible evaluation identity.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping, Sequence

from .benchmark import BenchmarkCase, BenchmarkJudgment, benchmark_suite_identity
from .benchmark_compare import compare_benchmark_reports
from .benchmark_runner import BenchmarkPilot, run_benchmark
from .dataset_manifest import DatasetManifest, DatasetManifestError, DatasetManifestStore
from .deck_package import ArtifactRef, DeckPackageError
from .evidence import EvidenceError, validate_raw_evidence_envelope
from .records import DecisionRecord, RecordValidationError
from .storage import LocalArtifactStore, StorageError, StorageLayout, StoredBlob

MANIFEST_BENCHMARK_SCHEMA_VERSION = 1
MANIFEST_BENCHMARK_KIND = "commander-gym.manifest-benchmark-evaluation"
BENCHMARK_SUBJECT_ROLES = frozenset({"candidate", "teacher"})


class ManifestBenchmarkError(ValueError):
    """Raised when frozen evidence cannot be evaluated without ambiguity or leakage."""


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ManifestBenchmarkError(f"{label} must be a non-empty string")
    return value


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ManifestBenchmarkError(f"{label} must be an object")
    return value


def _artifact_ref_dict(ref: ArtifactRef) -> dict[str, Any]:
    try:
        ref.validate()
    except DeckPackageError as exc:
        raise ManifestBenchmarkError("benchmark subject artifact reference is invalid") from exc
    digest = _require_string(ref.digest, "benchmark subject artifact digest")
    return {
        "kind": ref.kind,
        "artifact_id": ref.artifact_id,
        "version": ref.version,
        "digest": digest,
        "metadata": dict(ref.metadata),
    }


@dataclass(frozen=True)
class BenchmarkSubjectIdentity:
    """Exact identity of one policy/component evaluated on frozen evidence.

    ``runtime_name`` and ``runtime_version`` bind this durable identity to the
    ``BenchmarkPilot`` object that is actually executed.  ``component`` identifies the
    Pilot component/policy/adapter revision; ``model`` is optional for deterministic or
    non-model components and exact when present.
    """

    role: str
    runtime_name: str
    runtime_version: str
    component: ArtifactRef
    model: ArtifactRef | None = None
    provider: str | None = None

    def validate(self) -> None:
        if self.role not in BENCHMARK_SUBJECT_ROLES:
            raise ManifestBenchmarkError(
                f"benchmark subject role must be one of {sorted(BENCHMARK_SUBJECT_ROLES)!r}"
            )
        _require_string(self.runtime_name, "benchmark subject runtime_name")
        _require_string(self.runtime_version, "benchmark subject runtime_version")
        _artifact_ref_dict(self.component)
        if self.model is not None:
            _artifact_ref_dict(self.model)
        if self.provider is not None:
            _require_string(self.provider, "benchmark subject provider")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        result: dict[str, Any] = {
            "role": self.role,
            "runtime_name": self.runtime_name,
            "runtime_version": self.runtime_version,
            "component": _artifact_ref_dict(self.component),
        }
        if self.model is not None:
            result["model"] = _artifact_ref_dict(self.model)
        if self.provider is not None:
            result["provider"] = self.provider
        return result


@dataclass(frozen=True)
class ManifestBenchmarkWriteResult:
    artifact: StoredBlob
    report: Mapping[str, Any]


def _load_cataloged_manifest(
    layout: StorageLayout,
    manifest_artifact_id: str,
) -> DatasetManifest:
    _require_string(manifest_artifact_id, "manifest_artifact_id")
    store = DatasetManifestStore(layout)
    try:
        manifest = store.read_artifact(manifest_artifact_id)
        cataloged = store.read(dataset_id=manifest.dataset_id, version=manifest.version)
    except DatasetManifestError as exc:
        raise ManifestBenchmarkError(
            "benchmark evaluation requires a valid cataloged dataset manifest"
        ) from exc
    if cataloged.to_dict() != manifest.to_dict():
        raise ManifestBenchmarkError(
            "dataset manifest artifact does not match its immutable catalog entry"
        )
    return manifest


def _load_raw_evidence(
    artifacts: LocalArtifactStore,
    artifact_id: str,
) -> Mapping[str, Any]:
    try:
        payload = artifacts.read_bytes(artifact_id)
    except StorageError as exc:
        raise ManifestBenchmarkError(f"raw evidence artifact not found: {artifact_id}") from exc
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestBenchmarkError("raw evidence artifact is not UTF-8 JSON") from exc
    if not isinstance(value, Mapping):
        raise ManifestBenchmarkError("raw evidence artifact must decode to an object")
    try:
        validate_raw_evidence_envelope(value)
    except EvidenceError as exc:
        raise ManifestBenchmarkError("benchmark source is not valid immutable raw evidence") from exc
    qualification = _require_mapping(value.get("qualification"), "raw evidence qualification")
    if (
        qualification.get("classification") != "completed"
        or qualification.get("diagnostic_only") is not False
    ):
        raise ManifestBenchmarkError(
            "frozen benchmark labels require completed, non-diagnostic raw evidence"
        )
    return value


def _decision_record_from_raw(raw: Mapping[str, Any]) -> DecisionRecord:
    if raw.get("record_kind") != "action":
        raise ManifestBenchmarkError(
            "manifest benchmark v1 supports action decisions only; structured decisions "
            "must use a future explicit benchmark adapter"
        )
    input_value = _require_mapping(raw.get("input"), "raw evidence decision input")
    target = _require_mapping(raw.get("target"), "raw evidence decision target")
    provenance = _require_mapping(raw.get("provenance"), "raw evidence decision provenance")
    legal_actions = input_value.get("legal_actions")
    if not isinstance(legal_actions, list):
        raise ManifestBenchmarkError("raw action evidence legal_actions must be an array")

    payload = {
        "schema_version": provenance.get("record_schema_version"),
        "game_id": provenance.get("game_id"),
        "decision_id": provenance.get("decision_id"),
        "decision_type": input_value.get("decision_type"),
        "seat": input_value.get("seat"),
        "observation_schema": input_value.get("observation_schema"),
        "observation": input_value.get("observation"),
        "legal_actions": legal_actions,
        "chosen_action_id": target.get("chosen_action_id"),
        "pilot": provenance.get("pilot"),
        "deck_id": provenance.get("deck_id"),
        "deck_version": provenance.get("deck_version"),
        "primer_version": provenance.get("primer_version"),
        "binding": provenance.get("binding"),
        "outcome": provenance.get("outcome"),
        "metadata": provenance.get("metadata", {}),
    }
    try:
        return DecisionRecord.from_dict(payload)
    except (RecordValidationError, TypeError, ValueError) as exc:
        raise ManifestBenchmarkError(
            "raw action evidence cannot be reconstructed as a validated DecisionRecord"
        ) from exc


def build_manifest_benchmark_cases(
    layout: StorageLayout,
    manifest_artifact_id: str,
    *,
    split_name: str,
) -> list[BenchmarkCase]:
    """Materialize one exact ``frozen_test`` split as leakage-safe benchmark cases.

    The benchmark label is the immutable recorded target for the selected frozen
    decision.  Dataset/annotation/source lineage is attached only to judgment
    provenance and therefore cannot enter ``BenchmarkInput``.
    """

    if not isinstance(layout, StorageLayout):
        raise ManifestBenchmarkError("layout must be a StorageLayout")
    _require_string(split_name, "split_name")
    manifest = _load_cataloged_manifest(layout, manifest_artifact_id)
    try:
        split = manifest.split(split_name)
    except DatasetManifestError as exc:
        raise ManifestBenchmarkError(f"unknown benchmark split {split_name!r}") from exc
    if split.role != "frozen_test":
        raise ManifestBenchmarkError(
            f"benchmark evaluation requires a frozen_test split; {split_name!r} has "
            f"role {split.role!r}"
        )

    artifacts = LocalArtifactStore(layout)
    cases: list[BenchmarkCase] = []
    seen: set[str] = set()
    for selection in sorted(split.selections, key=lambda item: item.evidence_artifact_id):
        evidence = _load_raw_evidence(artifacts, selection.evidence_artifact_id)
        raw_decisions = evidence.get("decisions")
        if not isinstance(raw_decisions, list):
            raise ManifestBenchmarkError("raw evidence decisions must be an array")

        by_id: dict[str, Mapping[str, Any]] = {}
        for raw in raw_decisions:
            raw_mapping = _require_mapping(raw, "raw evidence decision")
            provenance = _require_mapping(
                raw_mapping.get("provenance"), "raw evidence decision provenance"
            )
            decision_id = _require_string(
                provenance.get("decision_id"), "raw evidence decision_id"
            )
            if decision_id in by_id:
                raise ManifestBenchmarkError(
                    f"raw evidence repeats decision_id {decision_id!r}"
                )
            by_id[decision_id] = raw_mapping

        for decision_id in selection.decision_ids:
            raw = by_id.get(decision_id)
            if raw is None:
                raise ManifestBenchmarkError(
                    f"frozen manifest decision {decision_id!r} is missing from source evidence"
                )
            if decision_id in seen:
                raise ManifestBenchmarkError(
                    f"frozen manifest repeats decision_id {decision_id!r}"
                )
            seen.add(decision_id)
            decision = _decision_record_from_raw(raw)
            judgment = BenchmarkJudgment(
                preferred_action_ids=[decision.chosen_action_id],
                provenance={
                    "source": "frozen_raw_target",
                    "manifest_artifact_id": manifest_artifact_id,
                    "dataset_id": manifest.dataset_id,
                    "dataset_version": manifest.version,
                    "dataset_digest": manifest.dataset_digest,
                    "split_name": split.name,
                    "source_evidence_artifact_id": selection.evidence_artifact_id,
                    "required_annotation_artifact_ids": sorted(
                        manifest.required_annotation_artifact_ids
                    ),
                },
            )
            cases.append(
                BenchmarkCase(
                    case_id=decision_id,
                    category=decision.decision_type,
                    decision=decision,
                    judgment=judgment,
                    held_out=True,
                    tags=["manifest-frozen-test"],
                )
            )

    if not cases:
        raise ManifestBenchmarkError("frozen benchmark split must contain at least one decision")
    if set(seen) != set(split.decision_ids):
        raise ManifestBenchmarkError(
            "materialized benchmark cases do not exactly match frozen manifest membership"
        )
    return cases


def _validate_subject_pilot(subject: BenchmarkSubjectIdentity, pilot: BenchmarkPilot) -> None:
    subject.validate()
    if pilot.name != subject.runtime_name or pilot.version != subject.runtime_version:
        raise ManifestBenchmarkError(
            f"{subject.role} benchmark runtime identity does not match executed pilot"
        )


def _deterministic_report(report: Mapping[str, Any]) -> dict[str, Any]:
    summary = _require_mapping(report.get("summary"), "benchmark summary")
    categories = _require_mapping(report.get("categories"), "benchmark categories")
    rows = report.get("cases")
    if not isinstance(rows, list):
        raise ManifestBenchmarkError("benchmark cases must be an array")
    metric_fields = (
        "cases",
        "legal_rate",
        "invalid_output_rate",
        "preferred_rate",
        "error_rate",
    )
    return {
        "benchmark": dict(_require_mapping(report.get("benchmark"), "benchmark identity")),
        "summary": {field: summary.get(field) for field in metric_fields},
        "categories": {
            category: {
                field: _require_mapping(value, f"benchmark category {category}").get(field)
                for field in metric_fields
            }
            for category, value in sorted(categories.items())
        },
        "cases": [
            {
                "case_id": row.get("case_id"),
                "category": row.get("category"),
                "selected_action_id": row.get("selected_action_id"),
                "legal": row.get("legal"),
                "preferred": row.get("preferred"),
                "rank": row.get("rank"),
                "invalid_output": row.get("invalid_output"),
                "error": row.get("error"),
            }
            for row in rows
            if isinstance(row, Mapping)
        ],
    }


def _deterministic_comparison(comparison: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "benchmark": dict(
            _require_mapping(comparison.get("benchmark"), "benchmark comparison identity")
        ),
        "summary": dict(
            _require_mapping(comparison.get("summary"), "benchmark comparison summary")
        ),
        "categories": {
            key: dict(_require_mapping(value, f"benchmark comparison category {key}"))
            for key, value in sorted(
                _require_mapping(comparison.get("categories"), "benchmark comparison categories").items()
            )
        },
        "cases": [
            dict(row)
            for row in comparison.get("cases", [])
            if isinstance(row, Mapping)
        ],
    }


def build_manifest_benchmark_evaluation(
    layout: StorageLayout,
    manifest_artifact_id: str,
    *,
    split_name: str,
    candidate_pilot: BenchmarkPilot,
    candidate_identity: BenchmarkSubjectIdentity,
    teacher_pilot: BenchmarkPilot,
    teacher_identity: BenchmarkSubjectIdentity,
) -> dict[str, Any]:
    """Evaluate candidate and teacher on identical frozen, seat-safe evidence."""

    if candidate_identity.role != "candidate":
        raise ManifestBenchmarkError("candidate_identity must have role='candidate'")
    if teacher_identity.role != "teacher":
        raise ManifestBenchmarkError("teacher_identity must have role='teacher'")
    _validate_subject_pilot(candidate_identity, candidate_pilot)
    _validate_subject_pilot(teacher_identity, teacher_pilot)

    manifest = _load_cataloged_manifest(layout, manifest_artifact_id)
    split = manifest.split(split_name)
    cases = build_manifest_benchmark_cases(
        layout,
        manifest_artifact_id,
        split_name=split_name,
    )
    suite = benchmark_suite_identity(cases)

    candidate_report = run_benchmark(cases, candidate_pilot)
    teacher_report = run_benchmark(cases, teacher_pilot)
    if candidate_report.get("benchmark") != suite or teacher_report.get("benchmark") != suite:
        raise ManifestBenchmarkError(
            "candidate and teacher did not evaluate the exact same frozen benchmark suite"
        )
    comparison = compare_benchmark_reports(teacher_report, candidate_report)

    source_artifacts = sorted(
        selection.evidence_artifact_id for selection in split.selections
    )
    return {
        "schema_version": MANIFEST_BENCHMARK_SCHEMA_VERSION,
        "kind": MANIFEST_BENCHMARK_KIND,
        "dataset_manifest": {
            "manifest_artifact_id": manifest_artifact_id,
            "dataset_id": manifest.dataset_id,
            "dataset_version": manifest.version,
            "dataset_digest": manifest.dataset_digest,
            "split_name": split.name,
            "split_role": split.role,
            "source_evidence_artifact_ids": source_artifacts,
            "required_annotation_artifact_ids": sorted(
                manifest.required_annotation_artifact_ids
            ),
        },
        "benchmark": suite,
        "teacher": {
            "identity": teacher_identity.to_dict(),
            "result": _deterministic_report(teacher_report),
        },
        "candidate": {
            "identity": candidate_identity.to_dict(),
            "result": _deterministic_report(candidate_report),
        },
        "comparison": _deterministic_comparison(comparison),
    }


def write_manifest_benchmark_evaluation(
    layout: StorageLayout,
    manifest_artifact_id: str,
    *,
    split_name: str,
    candidate_pilot: BenchmarkPilot,
    candidate_identity: BenchmarkSubjectIdentity,
    teacher_pilot: BenchmarkPilot,
    teacher_identity: BenchmarkSubjectIdentity,
) -> ManifestBenchmarkWriteResult:
    """Store one deterministic evaluation artifact through the #74 blob substrate."""

    report = build_manifest_benchmark_evaluation(
        layout,
        manifest_artifact_id,
        split_name=split_name,
        candidate_pilot=candidate_pilot,
        candidate_identity=candidate_identity,
        teacher_pilot=teacher_pilot,
        teacher_identity=teacher_identity,
    )
    try:
        payload = _canonical_json_bytes(report)
    except (TypeError, ValueError) as exc:
        raise ManifestBenchmarkError(
            "benchmark evaluation must be canonically JSON serializable"
        ) from exc
    artifact = LocalArtifactStore(layout).put_bytes(payload)
    return ManifestBenchmarkWriteResult(artifact=artifact, report=report)
