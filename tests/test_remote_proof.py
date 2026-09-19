from __future__ import annotations

from typing import Any, Mapping, Sequence

import pytest

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


def test_direct_proof_runs_complete_action_lifecycle_and_verifies_cleanup() -> None:
    backend = FakeBackend()
    proof = run_direct_proof(ArgentumOrchestrator(backend), {"seed": 42})

    assert proof == {
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
    }
    assert backend.envs == set()
    assert backend.events == [
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
    ]


def test_direct_proof_supports_native_structured_decision_path() -> None:
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

    assert proof["mutation"] == {"kind": "decision"}
    assert proof["openingStateDigest"] == "decision-before"
    assert proof["resultingStateDigest"] == "after-decision"
    assert "decision" in backend.events
    assert "step" not in backend.events


def test_direct_proof_refuses_to_guess_among_multiple_legal_actions_and_still_disposes() -> None:
    backend = FakeBackend()
    backend.observation["legalActions"] = [
        {"actionId": 7, "kind": "PassPriority"},
        {"actionId": 9, "kind": "PlayLand"},
    ]

    with pytest.raises(DirectOrchestrationProofError, match="exactly one legal action"):
        run_direct_proof(ArgentumOrchestrator(backend), {"seed": 42})

    assert backend.envs == set()
    assert "dispose" in backend.events
