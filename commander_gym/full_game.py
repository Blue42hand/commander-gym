"""Fail-closed, repeatable four-seat Argentum full-game runner.

Argentum remains authoritative for state, visibility, legality, multiplayer
lifecycle, and terminal results.  This module only binds artificial players to seats,
routes the acting seat, records successful choices, qualifies the run, and persists a
model-independent artifact.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Mapping, Sequence

from .argentum_client import (
    ArgentumClientError,
    ArgentumConnectionError,
    ArgentumDeliveryUnknownError,
    ArgentumGymClient,
    ArgentumRemoteError,
)
from .openai_responses_pilot import OpenAIResponsesPilot, OpenAIResponsesPilotError
from .orchestration import (
    ArgentumOrchestrator,
    OrchestrationError,
    OrchestrationInspection,
)
from .pilot import PilotContractError
from .pilot_execution import execute_pilot_choice
from .pilot_records import PilotRecordError
from .pilot_routing import RoutingPilot
from .qualification_pilot import QualificationAggroPilot
from .pilot_session import (
    DurablePilotDecision,
    PilotSeat,
    PilotSessionError,
    _PinnedObservationEnvironment,
    _bind_roster,
    _engine_provenance,
    _record_trace,
    _required_string,
    _validate_seats,
)
from .run_records import (
    RUN_RECORD_SCHEMA_VERSION,
    RUN_STATUS_COMPLETED,
    RUN_STATUS_FAILED,
    RUN_STATUS_STOPPED,
    EngineProvenance,
    RunParticipant,
    RunRecord,
    RunTermination,
)
from .records import SCHEMA_VERSION as DECISION_RECORD_SCHEMA_VERSION

FULL_GAME_ARTIFACT_VERSION = 2

QUALIFICATION_VALID_COMPLETE = "valid_complete"
QUALIFICATION_TECHNICAL_CENSORED = "technical_censored"
QUALIFICATION_PILOT_FAILURE = "pilot_failure"
QUALIFICATION_ENGINE_FAILURE = "engine_failure"
QUALIFICATION_UNSUPPORTED_DECISION = "unsupported_decision"
QUALIFICATIONS = frozenset(
    {
        QUALIFICATION_VALID_COMPLETE,
        QUALIFICATION_TECHNICAL_CENSORED,
        QUALIFICATION_PILOT_FAILURE,
        QUALIFICATION_ENGINE_FAILURE,
        QUALIFICATION_UNSUPPORTED_DECISION,
    }
)

FAILURE_DOMAIN_ENGINE = "engine"
FAILURE_DOMAIN_PILOT = "pilot"
FAILURE_DOMAIN_MODEL = "model"
FAILURE_DOMAIN_TRANSPORT = "transport"
FAILURE_DOMAIN_ORCHESTRATION = "orchestration"
FAILURE_DOMAINS = frozenset(
    {
        FAILURE_DOMAIN_ENGINE,
        FAILURE_DOMAIN_PILOT,
        FAILURE_DOMAIN_MODEL,
        FAILURE_DOMAIN_TRANSPORT,
        FAILURE_DOMAIN_ORCHESTRATION,
    }
)
_ENGINE_STAGES = frozenset(
    {"inspect", "create", "observe", "seat_observe", "dispose", "postflight"}
)


@dataclass(frozen=True)
class FullGameFailure:
    qualification: str
    failure_domain: str
    stage: str
    error_type: str
    message: str
    decision_index: int | None = None
    seat: int | None = None
    request_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        if self.failure_domain not in FAILURE_DOMAINS:
            raise PilotSessionError(f"unknown failure domain {self.failure_domain!r}")
        return {
            "qualification": self.qualification,
            "failure_domain": self.failure_domain,
            "stage": self.stage,
            "error_type": self.error_type,
            "message": self.message,
            "decision_index": self.decision_index,
            "seat": self.seat,
            "request_id": self.request_id,
        }


@dataclass(frozen=True)
class FullGameResult:
    run: RunRecord
    decisions: tuple[DurablePilotDecision, ...]
    qualification: str
    failures: tuple[FullGameFailure, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        if self.qualification not in QUALIFICATIONS:
            raise PilotSessionError(f"unknown qualification {self.qualification!r}")
        self.run.validate()
        return {
            "artifact_version": FULL_GAME_ARTIFACT_VERSION,
            "qualification": self.qualification,
            "training_eligible": self.qualification == QUALIFICATION_VALID_COMPLETE,
            "run": self.run.to_dict(),
            "decisions": [record.to_dict() for record in self.decisions],
            "failures": [failure.to_dict() for failure in self.failures],
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _commander_gym_revision() -> str:
    configured = os.environ.get("COMMANDER_GYM_REVISION")
    if configured:
        return configured
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parent.parent,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"
    revision = completed.stdout.strip()
    return revision or "unavailable"


def _participants(seats: Sequence[PilotSeat]) -> list[RunParticipant]:
    return [
        RunParticipant(
            seat=index,
            pilot=seat.provenance(),
            deck_id=seat.deck_id,
            deck_version=seat.deck_version,
            primer_version=seat.primer_version,
            binding=seat.binding,
        )
        for index, seat in enumerate(seats)
    ]


def _classify(exc: Exception) -> str:
    if isinstance(exc, PilotRecordError):
        return QUALIFICATION_UNSUPPORTED_DECISION
    if isinstance(exc, (OpenAIResponsesPilotError, PilotContractError)):
        return QUALIFICATION_PILOT_FAILURE
    if isinstance(exc, ArgentumDeliveryUnknownError):
        return QUALIFICATION_TECHNICAL_CENSORED
    if isinstance(exc, (ArgentumRemoteError, OrchestrationError)):
        return QUALIFICATION_ENGINE_FAILURE
    return QUALIFICATION_TECHNICAL_CENSORED


def _failure_domain(exc: Exception, *, stage: str) -> str:
    """Classify operational ownership separately from run qualification.

    Qualification answers whether evidence is usable; failure domain answers which
    layer failed.  Keeping those axes separate makes run artifacts useful for
    transport and provider operations without changing strategic policy.
    """

    if isinstance(exc, OpenAIResponsesPilotError):
        return FAILURE_DOMAIN_MODEL
    if isinstance(exc, (PilotContractError, PilotRecordError)):
        return FAILURE_DOMAIN_PILOT
    if isinstance(exc, (ArgentumDeliveryUnknownError, ArgentumConnectionError)):
        return FAILURE_DOMAIN_TRANSPORT
    if isinstance(exc, ArgentumRemoteError):
        return FAILURE_DOMAIN_ENGINE
    if isinstance(exc, ArgentumClientError):
        return FAILURE_DOMAIN_TRANSPORT
    if isinstance(exc, OrchestrationError):
        return FAILURE_DOMAIN_ENGINE
    if stage == "pilot":
        return FAILURE_DOMAIN_PILOT
    if stage in _ENGINE_STAGES:
        return FAILURE_DOMAIN_ENGINE
    return FAILURE_DOMAIN_ORCHESTRATION


def _failure(
    exc: Exception,
    *,
    stage: str,
    decision_index: int | None,
    seat: int | None,
) -> FullGameFailure:
    qualification = (
        QUALIFICATION_PILOT_FAILURE if stage == "pilot" else _classify(exc)
    )
    return FullGameFailure(
        qualification=qualification,
        failure_domain=_failure_domain(exc, stage=stage),
        stage=stage,
        error_type=type(exc).__name__,
        message=str(exc) or type(exc).__name__,
        decision_index=decision_index,
        seat=seat,
        request_id=(exc.request_id if isinstance(exc, ArgentumRemoteError) else None),
    )


def _annotate_outcome(
    records: Sequence[DurablePilotDecision], outcome: Mapping[str, Any]
) -> tuple[DurablePilotDecision, ...]:
    annotated: list[DurablePilotDecision] = []
    for record in records:
        combined = dict(record.outcome or {})
        combined["game_result"] = dict(outcome)
        annotated.append(replace(record, outcome=combined))
    return tuple(annotated)


def run_full_game(
    orchestrator: ArgentumOrchestrator,
    config: Mapping[str, Any],
    seats: Sequence[PilotSeat],
    *,
    run_id: str,
    max_choices: int = 100_000,
    seed: int | None = None,
    repeated_state_limit: int = 8,
) -> FullGameResult:
    """Drive one four-seat game to an authoritative terminal state or censor it.

    Before every choice the runner requests an observation projected for the current
    ``agentToAct``.  Engines without that multi-seat Gym capability fail closed rather
    than exposing another seat's information or enabling ``revealAll``.
    """

    _required_string(run_id, "run_id")
    if not isinstance(config, Mapping):
        raise PilotSessionError("config must be a mapping")
    if seed is not None and (type(seed) is not int or seed < 0):
        raise PilotSessionError("seed must be a non-negative integer or null")
    _validate_seats(seats, max_choices)
    if type(repeated_state_limit) is not int or repeated_state_limit < 2:
        raise PilotSessionError("repeated_state_limit must be an integer of at least two")

    started_at = _utc_now()
    inspection: OrchestrationInspection | None = None
    env_id: str | None = None
    decisions: list[DurablePilotDecision] = []
    acted_seats: set[int] = set()
    last_observation: Mapping[str, Any] | None = None
    failures: list[FullGameFailure] = []
    qualification = QUALIFICATION_TECHNICAL_CENSORED
    stage = "inspect"
    decision_index: int | None = None
    seat_index: int | None = None
    disposed = False
    repeated_key: tuple[str, str] | None = None
    repeated_count = 0

    try:
        inspection = orchestrator.inspect()
        stage = "create"
        env_config = dict(config)
        if seed is not None:
            configured_seed = env_config.get("seed")
            if configured_seed is not None and configured_seed != seed:
                raise PilotSessionError("manifest seed conflicts with the requested seed")
            env_config["seed"] = seed
        created = orchestrator.create_environment(env_config)
        env_id = _required_string(created.get("envId"), "create envId")
        stage = "observe"
        observation = orchestrator.observe_environment(env_id)
        roster = _bind_roster(observation, seats)

        for decision_index in range(max_choices):
            last_observation = observation
            terminated = observation.get("terminated")
            if type(terminated) is not bool:
                raise PilotSessionError("Argentum observation terminated must be boolean")
            if terminated:
                qualification = QUALIFICATION_VALID_COMPLETE
                break

            agent = observation.get("agentToAct")
            try:
                seat_index, seat = roster[agent]
            except (KeyError, TypeError) as exc:
                raise PilotSessionError(
                    "Argentum agentToAct is not bound to a configured pilot seat"
                ) from exc
            if not isinstance(agent, str) or not agent:
                raise PilotSessionError("Argentum agentToAct must be a non-empty string")

            stage = "seat_observe"
            seat_observation = orchestrator.observe_environment(
                env_id,
                perspective_player_id=agent,
            )
            if seat_observation.get("agentToAct") != agent:
                raise PilotSessionError("seat observation changed agentToAct")
            if seat_observation.get("perspectivePlayerId") != agent:
                raise PilotSessionError(
                    "Argentum did not return an observation for the acting seat"
                )

            stage = "pilot"
            environment = _PinnedObservationEnvironment(
                orchestrator,
                env_id,
                seat_observation,
            )
            trace = execute_pilot_choice(seat.pilot, environment)
            stage = "record"
            record = _record_trace(
                trace,
                env_id=env_id,
                run_id=run_id,
                decision_index=decision_index,
                seat_index=seat_index,
                seat=seat,
            )
            decisions.append(record)
            acted_seats.add(seat_index)

            state_digest = seat_observation.get("stateDigest")
            if not isinstance(state_digest, str) or not state_digest:
                raise PilotSessionError("acting-seat observation is missing stateDigest")
            loop_key = (state_digest, trace.semantic_id)
            if loop_key == repeated_key:
                repeated_count += 1
            else:
                repeated_key = loop_key
                repeated_count = 1
            if repeated_count >= repeated_state_limit:
                raise PilotSessionError(
                    "repeated identical state/action loop reached the configured limit"
                )

            observation = trace.result_observation
            stage = "observe"
        else:
            last_observation = observation
            qualification = (
                QUALIFICATION_VALID_COMPLETE
                if observation.get("terminated") is True
                else QUALIFICATION_TECHNICAL_CENSORED
            )

    except Exception as exc:
        failures.append(
            _failure(
                exc,
                stage=stage,
                decision_index=decision_index,
                seat=seat_index,
            )
        )
        qualification = failures[-1].qualification
    finally:
        if env_id is not None:
            try:
                orchestrator.dispose_environment(env_id)
                disposed = env_id not in orchestrator.list_environments()
                if not disposed:
                    raise PilotSessionError(
                        f"Argentum environment {env_id} still exists after disposal"
                    )
            except Exception as exc:
                failures.append(
                    _failure(
                        exc,
                        stage="dispose",
                        decision_index=decision_index,
                        seat=seat_index,
                    )
                )
                qualification = QUALIFICATION_TECHNICAL_CENSORED

    healthy_after = False
    if inspection is not None:
        try:
            final_inspection = orchestrator.inspect()
            healthy_after = final_inspection.identity == inspection.identity
            if not healthy_after:
                raise PilotSessionError("Argentum identity changed during the game")
        except Exception as exc:
            failures.append(
                _failure(
                    exc,
                    stage="postflight",
                    decision_index=decision_index,
                    seat=seat_index,
                )
            )
            qualification = QUALIFICATION_TECHNICAL_CENSORED

    terminal = bool(last_observation and last_observation.get("terminated") is True)
    winner_id = last_observation.get("winnerId") if terminal and last_observation else None
    outcome = {
        "qualification": qualification,
        "terminal": terminal,
        "winner_id": winner_id,
        "draw": terminal and winner_id is None,
    }
    annotated = _annotate_outcome(decisions, outcome)

    if qualification == QUALIFICATION_VALID_COMPLETE and disposed and healthy_after:
        termination = RunTermination(status=RUN_STATUS_COMPLETED)
    elif failures:
        termination = RunTermination(
            status=RUN_STATUS_FAILED,
            reason=failures[0].message,
            failure_domain=failures[0].failure_domain,
        )
    else:
        termination = RunTermination(
            status=RUN_STATUS_STOPPED,
            reason=f"choice limit reached before terminal state: {max_choices}",
        )

    engine = (
        _engine_provenance(inspection)
        if inspection is not None
        else EngineProvenance(
            implementation="argentum-gym-server",
            version="unavailable",
        )
    )
    run = RunRecord(
        run_id=run_id,
        started_at=started_at,
        finished_at=_utc_now(),
        engine=engine,
        termination=termination,
        participants=_participants(seats),
        game_id=env_id,
        seed=seed,
        decision_ids=[record.decision_id for record in annotated],
        metrics={
            "choices": len(annotated),
            "distinct_seats_acted": len(acted_seats),
            "all_four_seats_acted": acted_seats == {0, 1, 2, 3},
            "terminal": terminal,
        },
        metadata={
            "runner": "commander-gym-full-game-v2",
            "qualification": qualification,
            "winner_id": winner_id,
            "draw": terminal and winner_id is None,
            "final_state_digest": (
                last_observation.get("stateDigest") if last_observation else None
            ),
            "disposed": disposed,
            "service_healthy_after_dispose": healthy_after,
            "max_choices": max_choices,
            "repeated_state_limit": repeated_state_limit,
            "retry_count": sum(
                int(record.metadata.get("retry_count", 0)) for record in annotated
            ),
            "pilot_configurations": [dict(seat.pilot_config) for seat in seats],
            "commander_gym": {
                "revision": _commander_gym_revision(),
                "full_game_artifact_version": FULL_GAME_ARTIFACT_VERSION,
                "run_record_schema_version": RUN_RECORD_SCHEMA_VERSION,
                "decision_record_schema_version": DECISION_RECORD_SCHEMA_VERSION,
            },
        },
    )
    run.validate()
    return FullGameResult(
        run=run,
        decisions=annotated,
        qualification=qualification,
        failures=tuple(failures),
    )


def write_full_game_artifact(path: str | os.PathLike[str], result: FullGameResult) -> None:
    """Atomically persist a successful or censored game artifact."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n"
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


def _load_object(path: str | os.PathLike[str], label: str) -> Mapping[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise PilotSessionError(f"{label} must be a JSON object")
    return value


def _openai_seats(manifest: Mapping[str, Any]) -> list[PilotSeat]:
    raw_seats = manifest.get("seats")
    if not isinstance(raw_seats, list) or len(raw_seats) != 4:
        raise PilotSessionError("manifest seats must contain exactly four entries")
    openai_client: Any | None = None
    seats: list[PilotSeat] = []
    for index, raw in enumerate(raw_seats):
        if not isinstance(raw, Mapping):
            raise PilotSessionError(f"manifest seat {index} must be an object")
        pilot_config = raw.get("pilot")
        if not isinstance(pilot_config, Mapping):
            raise PilotSessionError(f"manifest seat {index} pilot must be an object")
        backend = pilot_config.get("backend")
        if backend == "openai_responses":
            if openai_client is None:
                api_key = os.environ.get("OPENAI_API_KEY")
                if not api_key:
                    raise PilotSessionError(
                        "OPENAI_API_KEY is required for openai_responses seats"
                    )
                try:
                    from openai import OpenAI
                except ImportError as exc:
                    raise PilotSessionError(
                        "the openai package is required for openai_responses"
                    ) from exc
                openai_client = OpenAI(api_key=api_key)
            model = _required_string(
                pilot_config.get("model"), f"seat {index} pilot model"
            )
            strategy = pilot_config.get("strategy")
            if strategy is not None:
                strategy = _required_string(strategy, f"seat {index} pilot strategy")
            strategic = OpenAIResponsesPilot(
                client=openai_client,
                model=model,
                strategy=strategy,
            )
            pilot_model: str | None = model
            durable_config = {
                "backend": "openai_responses",
                "model": model,
                "strategy": strategy,
                "pilot_version": strategic.version,
                "routing": "certified-routing",
                "routing_version": "1",
            }
        elif backend == "qualification_aggro":
            strategic = QualificationAggroPilot()
            pilot_model = None
            durable_config = {
                "backend": "qualification_aggro",
                "pilot_version": strategic.version,
                "scope": "public-synthetic-lifecycle-qualification-only",
                "routing": "certified-routing",
                "routing_version": "1",
            }
        else:
            raise PilotSessionError(
                f"manifest seat {index} uses unsupported pilot backend"
            )
        seats.append(
            PilotSeat(
                player_name=_required_string(raw.get("player_name"), f"seat {index} player_name"),
                pilot=RoutingPilot(strategic),
                deck_id=_required_string(raw.get("deck_id"), f"seat {index} deck_id"),
                deck_version=_required_string(
                    raw.get("deck_version"), f"seat {index} deck_version"
                ),
                primer_version=raw.get("primer_version"),
                pilot_model=pilot_model,
                pilot_config=durable_config,
            )
        )
    return seats


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", required=True, help="full-game manifest JSON")
    p.add_argument("--output-dir", required=True)
    p.add_argument("--base-url", default=os.environ.get("COMMANDER_GYM_ARGENTUM_URL"))
    p.add_argument("--token-env", default="COMMANDER_GYM_ARGENTUM_TOKEN")
    p.add_argument("--run-id", required=True, help="run id, or prefix when game-count > 1")
    p.add_argument("--game-count", type=int, default=1)
    p.add_argument("--max-choices", type=int, default=100_000)
    p.add_argument("--seed", type=int)
    p.add_argument("--expected-schema-hash")
    p.add_argument("--expected-build-revision")
    p.add_argument("--timeout", type=float, default=30.0)
    return p


def main() -> int:
    args = parser().parse_args()
    if not args.base_url:
        raise SystemExit("--base-url or COMMANDER_GYM_ARGENTUM_URL is required")
    if args.game_count < 1:
        raise SystemExit("--game-count must be positive")

    manifest = _load_object(args.manifest, "manifest")
    config = manifest.get("argentum_config")
    if not isinstance(config, Mapping):
        raise SystemExit("manifest argentum_config must be an object")
    seats = _openai_seats(manifest)
    client = ArgentumGymClient(
        args.base_url,
        bearer_token=os.environ.get(args.token_env),
        timeout=args.timeout,
    )
    orchestrator = ArgentumOrchestrator(
        client,
        expected_schema_hash=args.expected_schema_hash,
        expected_build_revision=args.expected_build_revision,
    )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    exit_code = 0
    summaries: list[dict[str, Any]] = []
    for game_index in range(args.game_count):
        run_id = args.run_id if args.game_count == 1 else f"{args.run_id}-{game_index + 1:04d}"
        result = run_full_game(
            orchestrator,
            config,
            seats,
            run_id=run_id,
            max_choices=args.max_choices,
            seed=(args.seed + game_index) if args.seed is not None else None,
        )
        artifact_path = output_dir / f"{run_id}.json"
        write_full_game_artifact(artifact_path, result)
        summaries.append(
            {
                "run_id": run_id,
                "qualification": result.qualification,
                "artifact": str(artifact_path),
            }
        )
        if result.qualification != QUALIFICATION_VALID_COMPLETE:
            exit_code = 1

    print(json.dumps({"runs": summaries}, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
