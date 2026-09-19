from __future__ import annotations

import unittest
from typing import Any, Mapping, Sequence

from commander_gym.orchestration import ArgentumOrchestrator
from commander_gym.remote_proof import DirectOrchestrationProofError, run_direct_proof


class FakeBackend:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.envs: set[str] = set()
        self.observation: dict[str, Any] = {
            "stateDigest": "before",
            "legalActions": [{"actionId": 7, "kind": "PassPriority"}],
        }

    def health(self) -> Mapping[str, Any]:
        self.events.append("health")
        return {"status": "ok"}

    def status(self) -> Mapping[str, Any]:
        self.events.append("status")
        return {
            "service": "argentum-gym-server",
            "schemaHash": "schema-v1",
            "buildRevision": "build-abc",
        }

    def schema_hash(self) -> Mapping[str, Any]:
        self.events.append("schema")
        return {"schemaHash": "schema-v1"}

    def list_envs(self) -> Sequence[str]:
        self.events.append("list")
        return sorted(self.envs)

    def create_env(self, config: Mapping[str, Any]) -> Mapping[str, Any]:
        self.events.append("create")
        assert config == {"seed": 42}
        self.envs.add("env-1")
        return {"envId": "env-1"}

    def observe_env(self, env_id: str, *, reveal_all: bool | None = None) -> Mapping[str, Any]:
        self.events.append("observe")
        assert env_id in self.envs
        return dict(self.observation)

    def step_env(
        self,
        env_id: str,
        action_id: int,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        self.events.append("step")
        assert env_id in self.envs
        assert action_id == 7
        assert params is None
        self.observation = {
            "stateDigest": "after",
            "legalActions": [{"actionId": 8, "kind": "PassPriority"}],
        }
        return dict(self.observation)

    def submit_decision(self, env_id: str, response: Mapping[str, Any]) -> Mapping[str, Any]:
        self.events.append("decision")
        assert env_id in self.envs
        self.observation = {"stateDigest": "after-decision", "legalActions": []}
        return dict(self.observation)

    def dispose_envs(self, env_ids: Sequence[str]) -> None:
        self.events.append("dispose")
        for env_id in env_ids:
            self.envs.discard(env_id)


class DirectOrchestrationProofTests(unittest.TestCase):
    def test_complete_action_lifecycle_and_cleanup(self) -> None:
        backend = FakeBackend()
        proof = run_direct_proof(ArgentumOrchestrator(backend), {"seed": 42})

        self.assertEqual(
            proof,
            {
                "proof": "commander-gym-direct-orchestration-v1",
                "service": "argentum-gym-server",
                "schemaHash": "schema-v1",
                "buildRevision": "build-abc",
                "health": "ok",
                "envId": "env-1",
                "preexistingEnvironmentCount": 0,
                "openingStateDigest": "before",
                "mutation": {"kind": "action", "actionId": 7},
                "resultingStateDigest": "after",
                "disposed": True,
                "serviceHealthyAfterDispose": True,
            },
        )
        self.assertEqual(backend.envs, set())
        self.assertEqual(
            backend.events,
            [
                "health",
                "status",
                "schema",
                "list",
                "create",
                "observe",
                "step",
                "observe",
                "dispose",
                "list",
                "health",
                "status",
                "schema",
            ],
        )

    def test_native_structured_decision_path(self) -> None:
        backend = FakeBackend()
        backend.observation = {
            "stateDigest": "decision-before",
            "legalActions": [],
            "pendingDecision": {"decisionId": "live-1", "type": "ChooseCards"},
        }

        proof = run_direct_proof(
            ArgentumOrchestrator(backend),
            {"seed": 42},
            decision_response={"type": "ChooseCards", "decisionId": "live-1", "cardIds": []},
        )

        self.assertEqual(proof["mutation"], {"kind": "decision"})
        self.assertEqual(proof["openingStateDigest"], "decision-before")
        self.assertEqual(proof["resultingStateDigest"], "after-decision")
        self.assertIn("decision", backend.events)
        self.assertNotIn("step", backend.events)

    def test_multiple_actions_fail_closed_and_cleanup(self) -> None:
        backend = FakeBackend()
        backend.observation["legalActions"] = [
            {"actionId": 7, "kind": "PassPriority"},
            {"actionId": 9, "kind": "PlayLand"},
        ]

        with self.assertRaisesRegex(DirectOrchestrationProofError, "exactly one legal action"):
            run_direct_proof(ArgentumOrchestrator(backend), {"seed": 42})

        self.assertEqual(backend.envs, set())
        self.assertIn("dispose", backend.events)


if __name__ == "__main__":
    unittest.main()
