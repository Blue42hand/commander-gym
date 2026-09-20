"""Bounded experiment orchestration over the existing Argentum full-game runner."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Callable, Mapping, Sequence

from .full_game import FullGameResult, run_full_game
from .orchestration import ArgentumOrchestrator
from .pilot_session import PilotSeat

EXPERIMENT_PLAN_SCHEMA_VERSION = 1
EXPERIMENT_ARTIFACT_VERSION = 1
MAX_EXPERIMENT_RUNS = 1024


class ExperimentError(ValueError):
    pass


@dataclass(frozen=True)
class ExperimentRunSpec:
    run_id: str
    seed: int | None = None

    def validate(self) -> None:
        if not isinstance(self.run_id, str) or not self.run_id:
            raise ExperimentError("run_id must be a non-empty string")
        if self.seed is not None and (type(self.seed) is not int or self.seed < 0):
            raise ExperimentError("seed must be a non-negative integer or null")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {"run_id": self.run_id, "seed": self.seed}


@dataclass(frozen=True)
class ExperimentPlan:
    experiment_id: str
    runs: tuple[ExperimentRunSpec, ...]
    schema_version: int = EXPERIMENT_PLAN_SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != EXPERIMENT_PLAN_SCHEMA_VERSION:
            raise ExperimentError("unsupported experiment plan schema_version")
        if not isinstance(self.experiment_id, str) or not self.experiment_id:
            raise ExperimentError("experiment_id must be a non-empty string")
        if not isinstance(self.runs, tuple) or not self.runs:
            raise ExperimentError("runs must be a non-empty tuple")
        if len(self.runs) > MAX_EXPERIMENT_RUNS:
            raise ExperimentError("experiment exceeds maximum run count")
        run_ids = []
        for spec in self.runs:
            if not isinstance(spec, ExperimentRunSpec):
                raise ExperimentError("runs must contain ExperimentRunSpec values")
            spec.validate()
            run_ids.append(spec.run_id)
        if len(run_ids) != len(set(run_ids)):
            raise ExperimentError("run_ids must be unique")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "experiment_id": self.experiment_id,
            "runs": [spec.to_dict() for spec in self.runs],
        }

    def fingerprint(self) -> str:
        payload = json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class ExperimentResult:
    plan: ExperimentPlan
    plan_fingerprint: str
    runs: tuple[FullGameResult, ...]

    def to_dict(self) -> dict[str, Any]:
        if self.plan_fingerprint != self.plan.fingerprint():
            raise ExperimentError("plan fingerprint mismatch")
        if len(self.runs) != len(self.plan.runs):
            raise ExperimentError("result count does not match plan")
        return {
            "artifact_version": EXPERIMENT_ARTIFACT_VERSION,
            "plan": self.plan.to_dict(),
            "plan_fingerprint": self.plan_fingerprint,
            "runs": [result.to_dict() for result in self.runs],
        }


ExperimentRunner = Callable[..., FullGameResult]


def run_experiment(
    orchestrator: ArgentumOrchestrator,
    config: Mapping[str, Any],
    seats: Sequence[PilotSeat],
    plan: ExperimentPlan,
    *,
    max_choices: int = 100_000,
    repeated_state_limit: int = 8,
    runner: ExperimentRunner = run_full_game,
) -> ExperimentResult:
    """Execute the declared runs in order, without adding retry or pilot policy."""

    plan.validate()
    fingerprint = plan.fingerprint()
    results = []
    for index, spec in enumerate(plan.runs):
        result = runner(
            orchestrator,
            config,
            seats,
            run_id=spec.run_id,
            max_choices=max_choices,
            seed=spec.seed,
            repeated_state_limit=repeated_state_limit,
        )
        if not isinstance(result, FullGameResult):
            raise ExperimentError("runner must return FullGameResult")
        result.run.validate()
        if result.run.run_id != spec.run_id:
            raise ExperimentError("runner returned the wrong run_id")
        if result.run.seed != spec.seed:
            raise ExperimentError("runner returned the wrong seed")
        if result.run.experiment_id not in (None, plan.experiment_id):
            raise ExperimentError("run belongs to a different experiment")
        metadata = dict(result.run.metadata)
        metadata["experiment"] = {
            "schema_version": EXPERIMENT_PLAN_SCHEMA_VERSION,
            "plan_fingerprint": fingerprint,
            "run_index": index,
            "run_count": len(plan.runs),
        }
        run = replace(
            result.run,
            experiment_id=plan.experiment_id,
            metadata=metadata,
        )
        run.validate()
        results.append(replace(result, run=run))
    aggregate = ExperimentResult(plan, fingerprint, tuple(results))
    aggregate.to_dict()
    return aggregate


def write_experiment_artifact(path: str | os.PathLike[str], result: ExperimentResult) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=target.parent, delete=False
        ) as handle:
            temporary = handle.name
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)
