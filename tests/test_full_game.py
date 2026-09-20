from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping, Sequence
from unittest.mock import patch

from commander_gym.argentum_client import ArgentumConnectionError, ArgentumRemoteError
from commander_gym.full_game import (
    FAILURE_DOMAIN_ENGINE,
    FAILURE_DOMAIN_MODEL,
    FAILURE_DOMAIN_PILOT,
    FAILURE_DOMAIN_TRANSPORT,
    FULL_GAME_ARTIFACT_VERSION,
    QUALIFICATION_ENGINE_FAILURE,
    QUALIFICATION_PILOT_FAILURE,
    QUALIFICATION_TECHNICAL_CENSORED,
    QUALIFICATION_VALID_COMPLETE,
    _openai_seats,
    run_full_game,
    write_full_game_artifact,
)
from commander_gym.openai_responses_pilot import OpenAIResponsesPilotError
from commander_gym.orchestration import ArgentumOrchestrator
from commander_gym.pilot import ArgentumActionChoice
from commander_gym.pilot_session import PilotSeat
from commander_gym.run_records import RUN_STATUS_COMPLETED, RUN_STATUS_FAILED, RUN_STATUS_STOPPED


class FirstPilot:
    version = "1"

    def __init__(self, name: str, *, fail: bool = False) -> None:
        self.name = name
        self.fail = fail

    def choose(self, observation: Mapping[str, Any]) -> ArgentumActionChoice:
        if self.fail:
            raise RuntimeError("provider offline")
        return ArgentumActionChoice(observation["legalActions"][0]["actionId"])


class SeatAwareBackend:
    def __init__(self, terminal_after: int = 4, *, honor_perspective: bool = True) -> None:
        self.ids = [f"p-{index}" for index in range(4)]
        self.names = [f"Seat {index}" for index in range(4)]
        self.terminal_after = terminal_after
        self.honor_perspective = honor_perspective
        self.index = 0
        self.envs: set[str] = set()

    def health(self) -> Mapping[str, Any]:
        return {"status": "ok"}

    def status(self) -> Mapping[str, Any]:
        return {
            "service": "argentum-gym-server",
            "schemaHash": "schema-seat-perspective",
            "buildRevision": "build-1",
        }

    def schema_hash(self) -> Mapping[str, Any]:
        return {"schemaHash": "schema-seat-perspective"}

    def list_envs(self) -> Sequence[str]:
        return sorted(self.envs)

    def create_env(self, config: Mapping[str, Any]) -> Mapping[str, Any]:
        self.envs.add("env-1")
        return {"envId": "env-1"}

    def observe_env(
        self,
        env_id: str,
        *,
        reveal_all: bool | None = None,
        perspective_player_id: str | None = None,
    ) -> Mapping[str, Any]:
        perspective = perspective_player_id if self.honor_perspective else self.ids[0]
        return self.observation(perspective)

    def step_env(
        self,
        env_id: str,
        action_id: int,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        if action_id != 100 + self.index:
            raise AssertionError("stale action")
        self.index += 1
        return self.observation(self.ids[0])

    def submit_decision(self, env_id: str, response: Mapping[str, Any]) -> Mapping[str, Any]:
        raise AssertionError("not used")

    def dispose_envs(self, env_ids: Sequence[str]) -> None:
        self.envs.difference_update(env_ids)

    def observation(self, perspective: str | None) -> Mapping[str, Any]:
        terminal = self.index >= self.terminal_after
        agent = None if terminal else self.ids[self.index % 4]
        return {
            "type": "Game",
            "schemaHash": "schema-seat-perspective",
            "stateDigest": f"state-{self.index}-{perspective}",
            "perspectivePlayerId": perspective,
            "agentToAct": agent,
            "players": [
                {"id": player_id, "name": name}
                for player_id, name in zip(self.ids, self.names)
            ],
            "pendingDecision": None,
            "legalActions": [] if terminal else [
                {
                    "actionId": 100 + self.index,
                    "semanticId": f"action-{self.index}",
                    "kind": "PassPriority",
                    "description": "Pass priority",
                    "affordable": True,
                }
            ],
            "terminated": terminal,
            "winnerId": self.ids[0] if terminal else None,
        }


def seats(*, fail_index: int | None = None) -> list[PilotSeat]:
    return [
        PilotSeat(
            player_name=f"Seat {index}",
            pilot=FirstPilot(f"pilot-{index}", fail=index == fail_index),
            deck_id=f"deck-{index}",
            deck_version=f"deck-version-{index}",
        )
        for index in range(4)
    ]


class FullGameTests(unittest.TestCase):
    def test_public_qualification_manifest_does_not_require_provider_credentials(self) -> None:
        manifest = json.loads(
            (Path(__file__).parent.parent / "fixtures" / "full_game_krenko_mountains.json")
            .read_text(encoding="utf-8")
        )

        with patch.dict(os.environ, {}, clear=True):
            configured = _openai_seats(manifest)

        self.assertEqual(len(configured), 4)
        self.assertTrue(
            all(
                seat.pilot_config["backend"] == "qualification_aggro"
                for seat in configured
            )
        )

    def test_terminal_game_is_training_eligible_and_annotates_every_decision(self) -> None:
        backend = SeatAwareBackend()
        result = run_full_game(
            ArgentumOrchestrator(backend),
            {"players": []},
            seats(),
            run_id="game-1",
            max_choices=8,
        )

        self.assertEqual(result.qualification, QUALIFICATION_VALID_COMPLETE)
        self.assertEqual(result.run.termination.status, RUN_STATUS_COMPLETED)
        self.assertEqual(result.run.metadata["winner_id"], "p-0")
        self.assertEqual(len(result.decisions), 4)
        self.assertEqual(backend.envs, set())
        for record in result.decisions:
            self.assertEqual(record.outcome["game_result"]["qualification"], "valid_complete")
            self.assertIn("timing", record.metadata)
            self.assertEqual(record.metadata["retry_count"], 0)

    def test_pilot_failure_preserves_partial_trajectory_and_is_not_training_eligible(self) -> None:
        backend = SeatAwareBackend(terminal_after=8)
        result = run_full_game(
            ArgentumOrchestrator(backend),
            {"players": []},
            seats(fail_index=2),
            run_id="game-pilot-failure",
            max_choices=8,
        )

        self.assertEqual(result.qualification, QUALIFICATION_PILOT_FAILURE)
        self.assertEqual(result.run.termination.status, RUN_STATUS_FAILED)
        self.assertEqual(len(result.decisions), 2)
        self.assertEqual(result.failures[0].qualification, QUALIFICATION_PILOT_FAILURE)
        self.assertEqual(result.failures[0].failure_domain, FAILURE_DOMAIN_PILOT)
        self.assertEqual(result.run.termination.failure_domain, FAILURE_DOMAIN_PILOT)
        self.assertEqual(result.failures[0].stage, "pilot")
        self.assertEqual(backend.envs, set())

    def test_model_failure_domain_is_separate_from_pilot_qualification(self) -> None:
        class ModelFailurePilot(FirstPilot):
            def choose(self, observation: Mapping[str, Any]):
                raise OpenAIResponsesPilotError("model service unavailable")

        configured = seats()
        configured[0] = PilotSeat(
            player_name="Seat 0",
            pilot=ModelFailurePilot("model-failure"),
            deck_id="deck-0",
            deck_version="deck-version-0",
        )
        result = run_full_game(
            ArgentumOrchestrator(SeatAwareBackend()),
            {"players": []},
            configured,
            run_id="game-model-failure",
            max_choices=8,
        )

        self.assertEqual(result.qualification, QUALIFICATION_PILOT_FAILURE)
        self.assertEqual(result.failures[0].failure_domain, FAILURE_DOMAIN_MODEL)
        self.assertEqual(result.run.termination.failure_domain, FAILURE_DOMAIN_MODEL)
        self.assertEqual(result.to_dict()["artifact_version"], FULL_GAME_ARTIFACT_VERSION)

    def test_transport_failure_domain_is_not_engine_failure(self) -> None:
        class TransportFailureBackend(SeatAwareBackend):
            def health(self) -> Mapping[str, Any]:
                raise ArgentumConnectionError("could not reach gateway")

        result = run_full_game(
            ArgentumOrchestrator(TransportFailureBackend()),
            {"players": []},
            seats(),
            run_id="game-transport-failure",
            max_choices=8,
        )

        self.assertEqual(result.qualification, QUALIFICATION_TECHNICAL_CENSORED)
        self.assertEqual(result.failures[0].failure_domain, FAILURE_DOMAIN_TRANSPORT)
        self.assertEqual(result.run.termination.failure_domain, FAILURE_DOMAIN_TRANSPORT)

    def test_engine_failure_preserves_gateway_request_id(self) -> None:
        class EngineFailureBackend(SeatAwareBackend):
            def create_env(self, config: Mapping[str, Any]) -> Mapping[str, Any]:
                raise ArgentumRemoteError(503, "engine unavailable", request_id="req-123")

        result = run_full_game(
            ArgentumOrchestrator(EngineFailureBackend()),
            {"players": []},
            seats(),
            run_id="game-engine-failure",
            max_choices=8,
        )

        self.assertEqual(result.qualification, QUALIFICATION_ENGINE_FAILURE)
        self.assertEqual(result.failures[0].failure_domain, FAILURE_DOMAIN_ENGINE)
        self.assertEqual(result.failures[0].request_id, "req-123")
        self.assertEqual(result.run.termination.failure_domain, FAILURE_DOMAIN_ENGINE)
        self.assertEqual(result.to_dict()["failures"][0]["request_id"], "req-123")

    def test_contract_pilot_failure_is_classified_separately(self) -> None:
        class MalformedPilot(FirstPilot):
            def choose(self, observation: Mapping[str, Any]):
                return None

        configured = seats()
        configured[0] = PilotSeat(
            player_name="Seat 0",
            pilot=MalformedPilot("malformed"),
            deck_id="deck-0",
            deck_version="deck-version-0",
        )
        result = run_full_game(
            ArgentumOrchestrator(SeatAwareBackend()),
            {"players": []},
            configured,
            run_id="game-malformed-pilot",
            max_choices=8,
        )

        self.assertEqual(result.qualification, QUALIFICATION_PILOT_FAILURE)
        self.assertFalse(result.to_dict()["training_eligible"])

    def test_missing_acting_seat_projection_fails_closed(self) -> None:
        result = run_full_game(
            ArgentumOrchestrator(SeatAwareBackend(honor_perspective=False)),
            {"players": []},
            seats(),
            run_id="game-no-seat-api",
            max_choices=8,
        )

        self.assertEqual(result.qualification, QUALIFICATION_TECHNICAL_CENSORED)
        self.assertEqual(result.failures[0].stage, "seat_observe")
        self.assertEqual(result.failures[0].failure_domain, FAILURE_DOMAIN_ENGINE)
        self.assertEqual(len(result.decisions), 1)

    def test_choice_bound_is_censored_and_artifact_is_atomic(self) -> None:
        result = run_full_game(
            ArgentumOrchestrator(SeatAwareBackend(terminal_after=100)),
            {"players": []},
            seats(),
            run_id="game-bounded",
            max_choices=4,
        )
        self.assertEqual(result.qualification, QUALIFICATION_TECHNICAL_CENSORED)
        self.assertEqual(result.run.termination.status, RUN_STATUS_STOPPED)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "game.json"
            write_full_game_artifact(path, result)
            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertFalse(payload["training_eligible"])
        self.assertEqual(payload["qualification"], "technical_censored")
        self.assertEqual(len(payload["decisions"]), 4)

    def test_terminal_reached_on_last_allowed_choice_is_complete(self) -> None:
        result = run_full_game(
            ArgentumOrchestrator(SeatAwareBackend(terminal_after=4)),
            {"players": []},
            seats(),
            run_id="game-terminal-at-bound",
            max_choices=4,
        )

        self.assertEqual(result.qualification, QUALIFICATION_VALID_COMPLETE)
        self.assertEqual(result.run.termination.status, RUN_STATUS_COMPLETED)


if __name__ == "__main__":
    unittest.main()
