from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping, Sequence

from commander_gym.orchestration import ArgentumOrchestrator
from commander_gym.pilot import ArgentumActionChoice
from commander_gym.pilot_session import (
    PilotSeat,
    PilotSessionError,
    run_four_seat_pilot_session,
    write_pilot_session_artifact,
)
from commander_gym.records import DecisionRecord
from commander_gym.run_records import RUN_STATUS_COMPLETED, RUN_STATUS_STOPPED


class SoleActionPilot:
    version = "1"

    def __init__(self, name: str) -> None:
        self.name = name
        self.observations: list[Mapping[str, Any]] = []

    def choose(self, observation: Mapping[str, Any]) -> ArgentumActionChoice:
        self.observations.append(observation)
        return ArgentumActionChoice(action_id=observation["legalActions"][0]["actionId"])


class FailingPilot(SoleActionPilot):
    def choose(self, observation: Mapping[str, Any]) -> ArgentumActionChoice:
        raise RuntimeError("provider failed")


class FourSeatBackend:
    def __init__(self, *, terminal_after: int = 4) -> None:
        self.player_ids = [f"player-{index}" for index in range(4)]
        self.player_names = [f"Seat {index}" for index in range(4)]
        self.terminal_after = terminal_after
        self.choice_index = 0
        self.envs: set[str] = set()
        self.mutations: list[tuple[str, int]] = []
        self.reveal_arguments: list[bool | None] = []

    def health(self) -> Mapping[str, Any]:
        return {"status": "ok"}

    def status(self) -> Mapping[str, Any]:
        return {
            "service": "argentum-gym-server",
            "schemaHash": "argentum-gym-contract@v1.7-semantic-state-provenance",
            "buildRevision": "argentum-build-1",
        }

    def schema_hash(self) -> Mapping[str, Any]:
        return {"schemaHash": "argentum-gym-contract@v1.7-semantic-state-provenance"}

    def list_envs(self) -> Sequence[str]:
        return sorted(self.envs)

    def create_env(self, config: Mapping[str, Any]) -> Mapping[str, Any]:
        self.envs.add("env-1")
        return {"envId": "env-1"}

    def observe_env(
        self, env_id: str, *, reveal_all: bool | None = None
    ) -> Mapping[str, Any]:
        self.reveal_arguments.append(reveal_all)
        return self._observation()

    def step_env(
        self,
        env_id: str,
        action_id: int,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        expected = 100 + self.choice_index
        if action_id != expected:
            raise AssertionError(f"expected current action {expected}, got {action_id}")
        self.mutations.append((self.player_ids[self.choice_index % 4], action_id))
        self.choice_index += 1
        return self._observation()

    def submit_decision(self, env_id: str, response: Mapping[str, Any]) -> Mapping[str, Any]:
        raise AssertionError("test session should use enumerated actions")

    def dispose_envs(self, env_ids: Sequence[str]) -> None:
        for env_id in env_ids:
            self.envs.discard(env_id)

    def _observation(self) -> Mapping[str, Any]:
        terminal = self.choice_index >= self.terminal_after
        acting_index = self.choice_index % 4
        acting_id = None if terminal else self.player_ids[acting_index]
        legal_actions = []
        if not terminal:
            legal_actions = [
                {
                    "actionId": 100 + self.choice_index,
                    "semanticId": (
                        f"argentum-action@v1:seat-{acting_index}-"
                        f"choice-{self.choice_index}"
                    ),
                    "kind": "PassPriority",
                    "description": "Pass priority",
                    "affordable": True,
                    "isDecisionOption": False,
                }
            ]
        return {
            "type": "Game",
            "schemaHash": "argentum-gym-contract@v1.7-semantic-state-provenance",
            "stateDigest": f"state-{self.choice_index}",
            "perspectivePlayerId": acting_id,
            "agentToAct": acting_id,
            "players": [
                {
                    "id": player_id,
                    "name": name,
                    "isPerspective": player_id == acting_id,
                }
                for player_id, name in zip(self.player_ids, self.player_names)
            ],
            "zones": [
                {
                    "ownerId": self.player_ids[(acting_index + 1) % 4],
                    "zoneType": "HAND",
                    "hidden": True,
                    "size": 7,
                    "cards": [],
                }
            ],
            "pendingDecision": None,
            "legalActions": legal_actions,
            "terminated": terminal,
            "winnerId": self.player_ids[0] if terminal else None,
        }


def seats(*, failing_index: int | None = None) -> list[PilotSeat]:
    result = []
    for index in range(4):
        pilot = (
            FailingPilot(f"pilot-{index}")
            if index == failing_index
            else SoleActionPilot(f"pilot-{index}")
        )
        result.append(
            PilotSeat(
                player_name=f"Seat {index}",
                pilot=pilot,
                deck_id=f"deck-{index}",
                deck_version=f"revision-{index}",
                primer_version=f"primer-{index}",
            )
        )
    return result


class FourSeatPilotSessionTests(unittest.TestCase):
    def test_four_independent_seats_reach_terminal_and_record_every_choice(self) -> None:
        backend = FourSeatBackend()
        configured_seats = seats()

        result = run_four_seat_pilot_session(
            ArgentumOrchestrator(backend),
            {"players": [{"name": f"Seat {index}"} for index in range(4)]},
            configured_seats,
            run_id="qualification-1",
            max_choices=8,
            seed=20260920,
        )

        self.assertEqual(result.run.termination.status, RUN_STATUS_COMPLETED)
        self.assertEqual(result.run.metrics["distinct_seats_acted"], 4)
        self.assertTrue(result.run.metrics["all_four_seats_acted"])
        self.assertEqual(result.run.metadata["winner_id"], "player-0")
        self.assertTrue(result.run.metadata["disposed"])
        self.assertEqual(backend.envs, set())
        self.assertEqual([record.seat for record in result.decisions], [0, 1, 2, 3])
        self.assertTrue(all(isinstance(record, DecisionRecord) for record in result.decisions))
        self.assertEqual(len(set(result.run.decision_ids)), 4)

        for index, seat in enumerate(configured_seats):
            self.assertEqual(len(seat.pilot.observations), 1)
            observation = seat.pilot.observations[0]
            self.assertEqual(observation["agentToAct"], f"player-{index}")
            self.assertTrue(observation["zones"][0]["hidden"])
            self.assertEqual(observation["zones"][0]["cards"], [])

        self.assertEqual(backend.reveal_arguments, [None])
        self.assertNotIn("actionId", result.decisions[0].observation["legalActions"][0])

    def test_provider_failure_stops_without_fallback_and_disposes(self) -> None:
        backend = FourSeatBackend(terminal_after=10)

        with self.assertRaisesRegex(RuntimeError, "provider failed"):
            run_four_seat_pilot_session(
                ArgentumOrchestrator(backend),
                {"players": []},
                seats(failing_index=2),
                run_id="qualification-failure",
                max_choices=8,
            )

        self.assertEqual(
            backend.mutations,
            [("player-0", 100), ("player-1", 101)],
        )
        self.assertEqual(backend.envs, set())

    def test_nonterminal_bound_is_an_explicit_stopped_run(self) -> None:
        backend = FourSeatBackend(terminal_after=100)

        result = run_four_seat_pilot_session(
            ArgentumOrchestrator(backend),
            {"players": []},
            seats(),
            run_id="qualification-bounded",
            max_choices=4,
        )

        self.assertEqual(result.run.termination.status, RUN_STATUS_STOPPED)
        self.assertEqual(
            result.run.termination.reason,
            "bounded proof reached max_choices=4",
        )
        self.assertFalse(result.run.metrics["terminal"])
        self.assertTrue(result.run.metrics["all_four_seats_acted"])
        self.assertEqual(backend.envs, set())

    def test_roster_mismatch_fails_before_gameplay_mutation_and_disposes(self) -> None:
        backend = FourSeatBackend()
        configured_seats = seats()
        configured_seats[3] = PilotSeat(
            player_name="Wrong Seat",
            pilot=SoleActionPilot("wrong"),
            deck_id="deck-wrong",
            deck_version="revision-wrong",
        )

        with self.assertRaisesRegex(PilotSessionError, "unexpected Argentum player"):
            run_four_seat_pilot_session(
                ArgentumOrchestrator(backend),
                {"players": []},
                configured_seats,
                run_id="qualification-roster-error",
                max_choices=8,
            )

        self.assertEqual(backend.mutations, [])
        self.assertEqual(backend.envs, set())

    def test_writes_one_validated_atomic_artifact(self) -> None:
        backend = FourSeatBackend()
        result = run_four_seat_pilot_session(
            ArgentumOrchestrator(backend),
            {"players": []},
            seats(),
            run_id="qualification-artifact",
            max_choices=4,
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "proof.json"
            write_pilot_session_artifact(path, result)
            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(payload["artifact_version"], 1)
        self.assertEqual(payload["run"]["run_id"], "qualification-artifact")
        self.assertEqual(len(payload["decisions"]), 4)
        self.assertTrue(payload["run"]["metadata"]["disposed"])


if __name__ == "__main__":
    unittest.main()
