from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping, Sequence

from commander_gym.evidence import RawEvidenceStore
from commander_gym.full_game import (
    FAILURE_DOMAIN_MODEL,
    QUALIFICATION_PILOT_FAILURE,
    _provider_failure_diagnostic,
    run_full_game,
)
from commander_gym.identity import IdentityRef
from commander_gym.openai_responses_pilot import OpenAIResponsesPilot, OpenAIResponsesPilotError
from commander_gym.orchestration import ArgentumOrchestrator
from commander_gym.pilot import ArgentumActionChoice
from commander_gym.pilot_session import PilotSeat
from commander_gym.run_records import RUN_STATUS_FAILED
from commander_gym.storage import StorageLayout


class ProviderTransportError(RuntimeError):
    status_code = 503
    body = {
        "error": {
            "code": "upstream_timeout",
            "message": "gateway timed out",
        }
    }


class FakeResponse:
    def __init__(
        self,
        output_text: str,
        *,
        status: str = "completed",
        incomplete_details: Mapping[str, Any] | None = None,
    ) -> None:
        self.id = "resp-provider-failure"
        self.model = "provider-model-revision"
        self.status = status
        self.error = None
        self.incomplete_details = incomplete_details
        self.output_text = output_text
        self.usage = {"input_tokens": 23, "output_tokens": 2}


class FakeResponses:
    def __init__(self, *, error: Exception | None = None, response: Any = None) -> None:
        self.error = error
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))
        if self.error is not None:
            raise self.error
        return self.response


class FakeClient:
    def __init__(self, *, error: Exception | None = None, response: Any = None) -> None:
        self.responses = FakeResponses(error=error, response=response)


class NeverPilot:
    name = "never-pilot"
    version = "1"

    def choose(self, observation: Mapping[str, Any]) -> ArgentumActionChoice:
        raise AssertionError("non-acting seat should not be queried")


class ProviderFailureBackend:
    def __init__(self) -> None:
        self.ids = [f"p-{index}" for index in range(4)]
        self.names = [f"Seat {index}" for index in range(4)]
        self.envs: set[str] = set()

    def health(self) -> Mapping[str, Any]:
        return {"status": "ok"}

    def status(self) -> Mapping[str, Any]:
        return {
            "service": "argentum-gym-server",
            "schemaHash": "schema-provider-failure",
            "buildRevision": "build-provider-failure",
        }

    def schema_hash(self) -> Mapping[str, Any]:
        return {"schemaHash": "schema-provider-failure"}

    def list_envs(self) -> Sequence[str]:
        return sorted(self.envs)

    def create_env(self, config: Mapping[str, Any]) -> Mapping[str, Any]:
        self.envs.add("env-provider-failure")
        return {"envId": "env-provider-failure"}

    def observe_env(
        self,
        env_id: str,
        *,
        reveal_all: bool | None = None,
        perspective_player_id: str | None = None,
    ) -> Mapping[str, Any]:
        perspective = perspective_player_id or self.ids[0]
        return {
            "type": "Game",
            "schemaHash": "schema-provider-failure",
            "stateDigest": f"state-provider-failure-{perspective}",
            "perspectivePlayerId": perspective,
            "agentToAct": self.ids[0],
            "players": [
                {"id": player_id, "name": name}
                for player_id, name in zip(self.ids, self.names)
            ],
            "pendingDecision": None,
            "legalActions": [
                {
                    "actionId": 10,
                    "semanticId": "action-pass",
                    "kind": "PassPriority",
                    "description": "Pass priority",
                    "affordable": True,
                },
                {
                    "actionId": 11,
                    "semanticId": "action-play",
                    "kind": "PlayLand",
                    "description": "Play a land",
                    "affordable": True,
                },
            ],
            "terminated": False,
        }

    def step_env(
        self,
        env_id: str,
        action_id: int,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        raise AssertionError("provider failure must happen before mutation")

    def submit_decision(self, env_id: str, response: Mapping[str, Any]) -> Mapping[str, Any]:
        raise AssertionError("provider failure must happen before mutation")

    def dispose_envs(self, env_ids: Sequence[str]) -> None:
        self.envs.difference_update(env_ids)


def binding_ref(index: int) -> IdentityRef:
    return IdentityRef("binding", f"binding-{index}", "r1", f"{index + 1:x}" * 64)


def configured_seats(failing_pilot: OpenAIResponsesPilot) -> list[PilotSeat]:
    result: list[PilotSeat] = []
    for index in range(4):
        result.append(
            PilotSeat(
                player_name=f"Seat {index}",
                pilot=failing_pilot if index == 0 else NeverPilot(),
                deck_id=f"deck-{index}",
                deck_version="r1",
                binding=binding_ref(index),
                pilot_config={"source": "test-binding", "binding": binding_ref(index).to_dict()},
            )
        )
    return result


class ProviderFailureEvidenceTests(unittest.TestCase):
    def test_transport_failure_before_trace_is_durable_diagnostic_evidence(self) -> None:
        client = FakeClient(error=ProviderTransportError("network unavailable"))
        pilot = OpenAIResponsesPilot(client=client, model="gpt-test", max_attempts=1)
        result = run_full_game(
            ArgentumOrchestrator(ProviderFailureBackend()),
            {"players": []},
            configured_seats(pilot),
            run_id="provider-transport-failure",
            max_choices=4,
        )

        self.assertEqual(result.qualification, QUALIFICATION_PILOT_FAILURE)
        self.assertEqual(result.run.termination.status, RUN_STATUS_FAILED)
        self.assertEqual(result.run.termination.failure_domain, FAILURE_DOMAIN_MODEL)
        self.assertEqual(result.decisions, ())

        diagnostics = result.run.metadata["diagnostic_failures"]
        self.assertEqual(len(diagnostics), 1)
        diagnostic = diagnostics[0]
        self.assertEqual(diagnostic["kind"], "provider_attempt_failure")
        self.assertEqual(diagnostic["input"]["observation"]["perspectivePlayerId"], "p-0")
        request = diagnostic["input"]["model_io"]["attempts"][0]["request"]
        self.assertEqual(request, client.responses.calls[0])
        model_observation = json.loads(request["input"].split("\n", 1)[1])
        self.assertNotIn("actionId", model_observation["legalActions"][0])
        self.assertNotIn("actionId", model_observation["legalActions"][1])
        self.assertEqual(
            diagnostic["target"]["model_io"]["attempts"],
            [{"attempt": 0}],
        )
        self.assertIsNone(diagnostic["target"]["model_io"]["selected_attempt"])
        self.assertIn(
            "upstream_timeout",
            diagnostic["provenance"]["model_io"]["attempts"][0]["transportError"],
        )
        self.assertNotIn("chosen_action", json.dumps(diagnostic))
        self.assertNotIn("output_text", diagnostic["target"]["model_io"]["attempts"][0])

        with tempfile.TemporaryDirectory() as tempdir:
            layout = StorageLayout.create(Path(tempdir) / "evidence")
            layout.ensure_directories()
            store = RawEvidenceStore(layout)
            written = store.write(
                result.run,
                result.decisions,
                commander_gym_revision="provider-failure-test-revision",
            )
            recovered = store.read("provider-transport-failure")

        self.assertTrue(written.artifact.artifact_id.startswith("sha256:"))
        self.assertEqual(recovered["qualification"]["classification"], "failed")
        self.assertTrue(recovered["qualification"]["diagnostic_only"])
        self.assertEqual(
            recovered["run"]["participants"][0]["binding"],
            binding_ref(0).to_dict(),
        )
        self.assertEqual(
            recovered["run"]["metadata"]["diagnostic_failures"],
            diagnostics,
        )

    def test_provider_response_failure_preserves_only_actual_model_output(self) -> None:
        client = FakeClient(
            response=FakeResponse(
                "{}",
                status="incomplete",
                incomplete_details={"reason": "max_output_tokens"},
            )
        )
        pilot = OpenAIResponsesPilot(client=client, model="gpt-test", max_attempts=1)
        observation = ProviderFailureBackend().observe_env(
            "unused",
            perspective_player_id="p-0",
        )

        with self.assertRaises(OpenAIResponsesPilotError) as raised:
            pilot.choose(observation)

        diagnostic = _provider_failure_diagnostic(
            raised.exception,
            stage="pilot",
            decision_index=0,
            seat=0,
            observation=observation,
        )
        self.assertIsNotNone(diagnostic)
        assert diagnostic is not None
        self.assertEqual(
            diagnostic["target"]["model_io"]["attempts"][0]["output_text"],
            "{}",
        )
        self.assertEqual(
            diagnostic["provenance"]["model_io"]["attempts"][0]["status"],
            "incomplete",
        )
        self.assertEqual(
            diagnostic["provenance"]["model_io"]["attempts"][0]["incompleteDetails"],
            {"reason": "max_output_tokens"},
        )
        self.assertIsNone(diagnostic["target"]["model_io"]["selected_attempt"])
        self.assertNotIn("chosen_action", json.dumps(diagnostic))


if __name__ == "__main__":
    unittest.main()
