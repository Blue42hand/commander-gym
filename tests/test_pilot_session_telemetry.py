from __future__ import annotations

import unittest
from typing import Any, Mapping, Sequence

from commander_gym.orchestration import ArgentumOrchestrator
from commander_gym.pilot import ArgentumActionChoice
from commander_gym.pilot_routing import RoutingPilot
from commander_gym.pilot_session import PilotSeat, run_four_seat_pilot_session


class FirstLegalPilot:
    name = "first-legal"
    version = "1"

    def choose(self, observation: Mapping[str, Any]) -> ArgentumActionChoice:
        return ArgentumActionChoice(
            action_id=observation["legalActions"][0]["actionId"]
        )


class TelemetryBackend:
    def __init__(self) -> None:
        self.player_ids = [f"player-{index}" for index in range(4)]
        self.player_names = [f"Seat {index}" for index in range(4)]
        self.choice_index = 0
        self.envs: set[str] = set()

    def health(self) -> Mapping[str, Any]:
        return {"status": "ok"}

    def status(self) -> Mapping[str, Any]:
        return {
            "service": "argentum-gym-server",
            "schemaHash": "argentum-gym-contract@v1.7-semantic-state-provenance",
            "buildRevision": "argentum-build-telemetry",
        }

    def schema_hash(self) -> Mapping[str, Any]:
        return {"schemaHash": "argentum-gym-contract@v1.7-semantic-state-provenance"}

    def list_envs(self) -> Sequence[str]:
        return sorted(self.envs)

    def create_env(self, config: Mapping[str, Any]) -> Mapping[str, Any]:
        self.envs.add("env-telemetry")
        return {"envId": "env-telemetry"}

    def observe_env(
        self, env_id: str, *, reveal_all: bool | None = None
    ) -> Mapping[str, Any]:
        return self._observation()

    def step_env(
        self,
        env_id: str,
        action_id: int,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        expected = 200 + self.choice_index
        if action_id != expected:
            raise AssertionError(f"expected action {expected}, got {action_id}")
        self.choice_index += 1
        return self._observation()

    def submit_decision(
        self, env_id: str, response: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        raise AssertionError("telemetry fixture uses enumerable actions")

    def dispose_envs(self, env_ids: Sequence[str]) -> None:
        for env_id in env_ids:
            self.envs.discard(env_id)

    def _observation(self) -> Mapping[str, Any]:
        terminal = self.choice_index >= 4
        acting_index = self.choice_index % 4
        acting_id = None if terminal else self.player_ids[acting_index]
        legal_actions: list[Mapping[str, Any]] = []
        if not terminal:
            # First two choices are certified mechanical passes. The last two use a
            # different native action kind so RoutingPilot must wake its strategic
            # delegate. No game semantics are reconstructed by the test or router.
            kind = "PassPriority" if self.choice_index < 2 else "CastSpell"
            legal_actions = [
                {
                    "actionId": 200 + self.choice_index,
                    "semanticId": f"argentum-action@v1:telemetry-{self.choice_index}",
                    "kind": kind,
                    "description": kind,
                    "affordable": True,
                    "isDecisionOption": False,
                    "hasXCost": False,
                    "requiresDamageDistribution": False,
                    "minTargets": 0,
                    "maxTargets": 0,
                    "targetEntityIds": [],
                    "validAttackers": [],
                    "mandatoryAttackers": [],
                    "validAttackTargets": [],
                    "validBlockers": [],
                    "blockerMaxBlockCounts": {},
                    "mandatoryBlockerAssignments": {},
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
            "zones": [],
            "pendingDecision": None,
            "legalActions": legal_actions,
            "terminated": terminal,
            "winnerId": self.player_ids[0] if terminal else None,
        }


def routed_seats() -> list[PilotSeat]:
    return [
        PilotSeat(
            player_name=f"Seat {index}",
            pilot=RoutingPilot(FirstLegalPilot()),
            deck_id=f"deck-{index}",
            deck_version="test-revision",
        )
        for index in range(4)
    ]


class PilotSessionTelemetryTests(unittest.TestCase):
    def test_records_routing_escalation_retry_and_wall_time_metrics(self) -> None:
        backend = TelemetryBackend()

        result = run_four_seat_pilot_session(
            ArgentumOrchestrator(backend),
            {"players": [{"name": f"Seat {index}"} for index in range(4)]},
            routed_seats(),
            run_id="qualification-telemetry",
            max_choices=4,
        )

        metrics = result.run.metrics
        self.assertEqual(metrics["choices"], 4)
        self.assertEqual(metrics["mechanical_decisions"], 2)
        self.assertEqual(metrics["strategic_decisions"], 2)
        self.assertEqual(metrics["decision_escalations"], 2)
        self.assertEqual(metrics["strategic_wakes_avoided"], 2)
        self.assertEqual(metrics["unclassified_decisions"], 0)
        self.assertEqual(metrics["mutation_retries"], 0)
        self.assertGreaterEqual(metrics["decision_wall_time_ms_total"], 0.0)
        self.assertGreaterEqual(metrics["decision_wall_time_ms_mean"], 0.0)
        self.assertGreaterEqual(metrics["decision_wall_time_ms_max"], 0.0)
        self.assertGreaterEqual(metrics["session_wall_time_ms"], 0.0)
        self.assertAlmostEqual(
            metrics["decision_wall_time_ms_mean"],
            metrics["decision_wall_time_ms_total"] / 4,
        )
        self.assertEqual(backend.envs, set())

        routing_paths = [
            record.metadata["pilot_metadata"]["routing"]["path"]
            for record in result.decisions
        ]
        self.assertEqual(routing_paths, ["mechanical", "mechanical", "strategic", "strategic"])


if __name__ == "__main__":
    unittest.main()
