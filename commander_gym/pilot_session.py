"""Run four independent Commander Gym pilots through one Argentum environment.

This module is the bounded integration layer used by the Argentum migration proof.  It
does not choose actions, reconstruct game state, or provide a fallback policy.  Every
mutation comes from the pilot bound to the current Argentum ``agentToAct`` and is
recorded through the existing native-semantic provenance bridge.

Actual game artifacts can contain private deck and seat observations.  Callers decide
where to persist them; public Commander Gym only defines and validates the format.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .orchestration import ArgentumOrchestrator, OrchestrationInspection
from .pilot import ArtificialPlayer, PilotContractError
from .pilot_execution import PilotEnvironment, PilotExecutionTrace, execute_pilot_choice
from .pilot_records import (
    PilotRecordContext,
    decision_record_from_execution_trace,
    structured_decision_record_from_execution_trace,
)
from .records import DecisionRecord, PilotProvenance, StructuredDecisionRecord
from .run_records import (
    RUN_STATUS_COMPLETED,
    RUN_STATUS_STOPPED,
    EngineProvenance,
    RunParticipant,
    RunRecord,
    RunTermination,
)

PILOT_SESSION_ARTIFACT_VERSION = 1
DurablePilotDecision = DecisionRecord | StructuredDecisionRecord


class PilotSessionError(RuntimeError):
    """Raised when a four-seat proof cannot continue without guessing."""


@dataclass(frozen=True)
class PilotSeat:
    """One independently controlled seat and its durable package provenance."""

    player_name: str
    pilot: ArtificialPlayer
    deck_id: str
    deck_version: str
    primer_version: str | None = None
    pilot_source: str = "commander-gym"
    pilot_model: str | None = None

    def provenance(self) -> PilotProvenance:
        return PilotProvenance(
            source=self.pilot_source,
            implementation=_pilot_string(self.pilot, "name"),
            version=_pilot_string(self.pilot, "version"),
            model=self.pilot_model,
        )


@dataclass(frozen=True)
class PilotSessionResult:
    """Durable output from a bounded or terminal four-seat pilot session."""

    run: RunRecord
    decisions: tuple[DurablePilotDecision, ...]

    def to_dict(self) -> dict[str, Any]:
        self.run.validate()
        return {
            "artifact_version": PILOT_SESSION_ARTIFACT_VERSION,
            "run": self.run.to_dict(),
            "decisions": [record.to_dict() for record in self.decisions],
        }


class _PinnedObservationEnvironment(PilotEnvironment):
    """Give the executor exactly the observation used to select the acting pilot."""

    def __init__(
        self,
        orchestrator: ArgentumOrchestrator,
        env_id: str,
        observation: Mapping[str, Any],
    ) -> None:
        self._orchestrator = orchestrator
        self._env_id = env_id
        self._observation = observation

    def observe(self) -> Mapping[str, Any]:
        return self._observation

    def submit_action(
        self, action_id: int, params: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        return self._orchestrator.step_environment(
            self._env_id, action_id, params=params
        )

    def submit_decision(self, response: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._orchestrator.submit_decision(self._env_id, response)


def _pilot_string(pilot: ArtificialPlayer, field: str) -> str:
    value = getattr(pilot, field, None)
    if not isinstance(value, str) or not value:
        raise PilotSessionError(f"pilot {field} must be a non-empty string")
    return value


def _required_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise PilotSessionError(f"{label} must be a non-empty string")
    return value


def _validate_seats(seats: Sequence[PilotSeat], max_choices: int) -> None:
    if len(seats) != 4:
        raise PilotSessionError("Argentum migration proof requires exactly four pilot seats")
    if type(max_choices) is not int or max_choices < 4:
        raise PilotSessionError("max_choices must be an integer of at least four")

    names: list[str] = []
    for index, seat in enumerate(seats):
        if not isinstance(seat, PilotSeat):
            raise PilotSessionError("seats must contain PilotSeat values")
        names.append(_required_string(seat.player_name, f"seat {index} player_name"))
        _required_string(seat.deck_id, f"seat {index} deck_id")
        _required_string(seat.deck_version, f"seat {index} deck_version")
        seat.provenance()
    if len(names) != len(set(names)):
        raise PilotSessionError("pilot seat player names must be unique")


def _bind_roster(
    observation: Mapping[str, Any], seats: Sequence[PilotSeat]
) -> dict[Any, tuple[int, PilotSeat]]:
    players = observation.get("players")
    if not isinstance(players, list) or len(players) != 4:
        raise PilotSessionError("Argentum observation must contain exactly four players")

    expected_by_name = {seat.player_name: (index, seat) for index, seat in enumerate(seats)}
    bound: dict[Any, tuple[int, PilotSeat]] = {}
    observed_names: list[str] = []
    for player in players:
        if not isinstance(player, Mapping):
            raise PilotSessionError("Argentum players entries must be objects")
        player_id = player.get("id")
        if player_id is None:
            raise PilotSessionError("Argentum player is missing id")
        try:
            duplicate = player_id in bound
        except TypeError as exc:
            raise PilotSessionError("Argentum player id must be hashable") from exc
        if duplicate:
            raise PilotSessionError("Argentum player ids must be unique")
        name = _required_string(player.get("name"), "Argentum player name")
        observed_names.append(name)
        try:
            bound[player_id] = expected_by_name[name]
        except KeyError as exc:
            raise PilotSessionError(f"unexpected Argentum player {name!r}") from exc

    if set(observed_names) != set(expected_by_name):
        raise PilotSessionError("Argentum roster does not match the four configured pilot seats")
    return bound


def _record_trace(
    trace: PilotExecutionTrace,
    *,
    env_id: str,
    run_id: str,
    decision_index: int,
    seat_index: int,
    seat: PilotSeat,
) -> DurablePilotDecision:
    context = PilotRecordContext(
        game_id=env_id,
        decision_id=f"{run_id}:decision:{decision_index}",
        seat=seat_index,
        deck_id=seat.deck_id,
        deck_version=seat.deck_version,
        primer_version=seat.primer_version,
        pilot_source=seat.pilot_source,
    )
    if trace.channel == "action":
        return decision_record_from_execution_trace(trace, context)
    if trace.channel == "decision":
        return structured_decision_record_from_execution_trace(trace, context)
    raise PilotSessionError(f"unsupported pilot execution channel {trace.channel!r}")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def run_four_seat_pilot_session(
    orchestrator: ArgentumOrchestrator,
    config: Mapping[str, Any],
    seats: Sequence[PilotSeat],
    *,
    run_id: str,
    max_choices: int,
    seed: int | str | None = None,
    require_all_seats: bool = True,
) -> PilotSessionResult:
    """Drive four pilots until terminal state or ``max_choices``.

    Pilot/provider failures propagate.  No heuristic, pass, concession, retry, or
    alternate pilot is substituted.  The environment is disposed in all cases, and a
    successful return additionally proves disposal and stable server identity.
    """

    _required_string(run_id, "run_id")
    if not isinstance(config, Mapping):
        raise PilotSessionError("config must be a mapping")
    _validate_seats(seats, max_choices)

    started_at = _utc_now()
    inspection = orchestrator.inspect()
    env_id: str | None = None
    decisions: list[DurablePilotDecision] = []
    acted_seats: set[int] = set()
    last_observation: Mapping[str, Any] | None = None
    disposed = False

    try:
        created = orchestrator.create_environment(config)
        env_id = _required_string(created.get("envId"), "create envId")
        observation = orchestrator.observe_environment(env_id)
        roster = _bind_roster(observation, seats)

        for decision_index in range(max_choices):
            if observation.get("terminated") is True:
                break
            agent = observation.get("agentToAct")
            try:
                seat_index, seat = roster[agent]
            except (KeyError, TypeError) as exc:
                raise PilotSessionError(
                    "Argentum agentToAct is not bound to a configured pilot seat"
                ) from exc

            environment = _PinnedObservationEnvironment(orchestrator, env_id, observation)
            trace = execute_pilot_choice(seat.pilot, environment)
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
            observation = trace.result_observation

        last_observation = observation
        if require_all_seats and acted_seats != {0, 1, 2, 3}:
            raise PilotSessionError(
                "bounded session ended before all four independent pilots acted"
            )
    finally:
        if env_id is not None:
            orchestrator.dispose_environment(env_id)
            disposed = env_id not in orchestrator.list_environments()

    if env_id is None or last_observation is None:
        raise PilotSessionError("pilot session did not create and observe an environment")
    if not disposed:
        raise PilotSessionError(f"Argentum environment {env_id} still exists after disposal")

    final_inspection = orchestrator.inspect()
    if final_inspection.identity != inspection.identity:
        raise PilotSessionError("Argentum identity changed during the pilot session")

    terminal = last_observation.get("terminated") is True
    termination = (
        RunTermination(status=RUN_STATUS_COMPLETED)
        if terminal
        else RunTermination(
            status=RUN_STATUS_STOPPED,
            reason=f"bounded proof reached max_choices={max_choices}",
        )
    )
    participants = [
        RunParticipant(
            seat=index,
            pilot=seat.provenance(),
            deck_id=seat.deck_id,
            deck_version=seat.deck_version,
            primer_version=seat.primer_version,
        )
        for index, seat in enumerate(seats)
    ]
    run = RunRecord(
        run_id=run_id,
        started_at=started_at,
        finished_at=_utc_now(),
        engine=_engine_provenance(inspection),
        termination=termination,
        participants=participants,
        game_id=env_id,
        seed=seed,
        decision_ids=[record.decision_id for record in decisions],
        metrics={
            "choices": len(decisions),
            "distinct_seats_acted": len(acted_seats),
            "all_four_seats_acted": acted_seats == {0, 1, 2, 3},
            "terminal": terminal,
        },
        metadata={
            "proof": "commander-gym-four-seat-pilot-session-v1",
            "winner_id": last_observation.get("winnerId") if terminal else None,
            "final_state_digest": last_observation.get("stateDigest"),
            "disposed": disposed,
        },
    )
    run.validate()
    return PilotSessionResult(run=run, decisions=tuple(decisions))


def _engine_provenance(inspection: OrchestrationInspection) -> EngineProvenance:
    identity = inspection.identity
    return EngineProvenance(
        implementation=identity.service,
        version=identity.build_revision,
        schema=identity.schema_hash,
        revision=identity.build_revision,
    )


def write_pilot_session_artifact(
    path: str | os.PathLike[str], result: PilotSessionResult
) -> None:
    """Atomically persist one validated pilot-session artifact as JSON."""

    target = Path(path)
    if not target.parent.is_dir():
        raise PilotSessionError(f"artifact parent directory does not exist: {target.parent}")
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
