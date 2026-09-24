import unittest

from commander_gym.deck_package import ArtifactRef
from commander_gym.identity import Pilot
from commander_gym.local_model import (
    LocalModelScope,
    LocalModelSubsystem,
    LocalModelUnavailableError,
)
from commander_gym.pilot import (
    ArgentumActionChoice,
    ArgentumDecisionChoice,
    PilotContractError,
    choose_for_observation,
)
from commander_gym.pilot_composition import (
    PilotComponentRegistry,
    SubsystemDecision,
    compose_pilot_runtime,
)


def ref(kind, artifact_id, version="1", digest=None):
    return ArtifactRef(
        kind=kind,
        artifact_id=artifact_id,
        version=version,
        digest=digest,
    )


LOCAL_REF = ref(
    "local-model",
    "fixture-local-generalist",
    digest="sha256:local-fixture-v1",
)
FRONTIER_REF = ref(
    "provider",
    "fixture-frontier",
    digest="sha256:frontier-fixture-v1",
)


def action_observation():
    return {
        "type": "Game",
        "schemaHash": "argentum-gym-contract@fixture",
        "stateDigest": "state-actions",
        "perspectivePlayerId": "player-1",
        "agentToAct": "player-1",
        "pendingDecision": None,
        "legalActions": [
            {
                "actionId": 2,
                "semanticId": "argentum-action-v1:pass",
                "kind": "PassPriority",
                "description": "Pass priority",
                "affordable": True,
            },
            {
                "actionId": 7,
                "semanticId": "argentum-action-v1:attack",
                "kind": "DeclareAttackers",
                "description": "Declare attackers",
                "affordable": True,
                "validAttackers": ["creature-1"],
                "validAttackTargets": ["player-2"],
            },
        ],
        "terminated": False,
    }


def structured_observation():
    return {
        "type": "Game",
        "schemaHash": "argentum-gym-contract@fixture",
        "stateDigest": "state-structured",
        "perspectivePlayerId": "player-1",
        "agentToAct": "player-1",
        "pendingDecision": {
            "decisionId": "live-decision-9",
            "semanticId": "argentum-decision-v1:choose-targets",
            "kind": "CHOOSE_TARGETS",
            "playerId": "player-1",
            "prompt": "Choose one target",
            "requiresStructuredResponse": True,
        },
        "legalActions": [],
        "terminated": False,
    }


class FixtureLocalBackend:
    name = "fixture-local-runtime"
    version = "model-r7"

    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.inputs = []

    def infer(self, observation):
        self.inputs.append(observation)
        if self.error is not None:
            raise self.error
        return self.result


class FixtureFrontierSubsystem:
    name = "fixture-frontier-runtime"
    version = "teacher-r3"

    def __init__(self):
        self.calls = 0

    def try_choose(self, observation):
        self.calls += 1
        pending = observation.get("pendingDecision")
        if isinstance(pending, dict) and pending.get("requiresStructuredResponse") is True:
            return SubsystemDecision(
                choice=ArgentumDecisionChoice(
                    response={
                        "type": "ChooseTargetsResponse",
                        "targets": ["target-1"],
                        "decisionId": pending["decisionId"],
                    },
                    metadata={"provider": "fixture-frontier"},
                )
            )
        return SubsystemDecision(
            choice=ArgentumActionChoice(
                action_id=observation["legalActions"][0]["actionId"],
                metadata={"provider": "fixture-frontier"},
            )
        )


def runtime(local_backend, *, local_scope=None, frontier=None):
    pilot = Pilot(
        pilot_id="local-foundation-pilot",
        revision="r1",
        generalist_base=LOCAL_REF,
        escalation_provider=FRONTIER_REF,
    )
    local = LocalModelSubsystem(
        backend=local_backend,
        scope=local_scope
        or LocalModelScope(action_kinds=("PassPriority", "DeclareAttackers")),
    )
    frontier = frontier or FixtureFrontierSubsystem()
    registry = PilotComponentRegistry(
        {
            PilotComponentRegistry.key_for(LOCAL_REF): local,
            PilotComponentRegistry.key_for(FRONTIER_REF): frontier,
        }
    )
    return compose_pilot_runtime(pilot, registry), frontier


class LocalModelFoundationTests(unittest.TestCase):
    def test_local_model_replaces_frontier_for_certified_strategic_family(self):
        backend = FixtureLocalBackend(
            {
                "channel": "action",
                "semanticId": "argentum-action-v1:attack",
                "params": {"attackers": {"creature-1": "player-2"}},
            }
        )
        pilot, frontier = runtime(backend)

        choice = choose_for_observation(pilot, action_observation())

        self.assertEqual(choice.action_id, 7)
        self.assertEqual(
            choice.params,
            {"attackers": {"creature-1": "player-2"}},
        )
        self.assertEqual(frontier.calls, 0)
        self.assertEqual(len(backend.inputs), 1)
        self.assertNotIn("actionId", backend.inputs[0]["legalActions"][0])
        self.assertNotIn("actionId", backend.inputs[0]["legalActions"][1])

        routing = choice.metadata["routing"]
        self.assertEqual(routing["path"], "composed")
        self.assertEqual(routing["handledBy"]["role"], "local_generalist")
        self.assertEqual(
            routing["handledBy"]["component"]["artifactId"],
            "fixture-local-generalist",
        )
        self.assertEqual(routing["attempts"][0]["status"], "handled")
        self.assertEqual(
            routing["attempts"][0]["metadata"]["family"],
            "actions:DeclareAttackers,PassPriority",
        )
        self.assertEqual(choice.metadata["localModel"]["backendVersion"], "model-r7")

    def test_out_of_scope_decision_escalates_without_waking_local_backend(self):
        backend = FixtureLocalBackend(result={"channel": "action"})
        pilot, frontier = runtime(backend)

        choice = choose_for_observation(pilot, structured_observation())

        self.assertIsInstance(choice, ArgentumDecisionChoice)
        self.assertEqual(frontier.calls, 1)
        self.assertEqual(backend.inputs, [])
        routing = choice.metadata["routing"]
        self.assertEqual(routing["handledBy"]["role"], "frontier_escalation")
        self.assertEqual(routing["attempts"][0]["role"], "local_generalist")
        self.assertEqual(routing["attempts"][0]["status"], "deferred")
        self.assertEqual(
            routing["attempts"][0]["reason"],
            "outside-certified-local-model-scope",
        )
        self.assertEqual(routing["attempts"][1]["status"], "handled")

    def test_expected_local_backend_unavailability_escalates_and_records_reason(self):
        backend = FixtureLocalBackend(error=LocalModelUnavailableError("socket unavailable"))
        pilot, frontier = runtime(backend)

        choice = choose_for_observation(pilot, action_observation())

        self.assertEqual(choice.action_id, 2)
        self.assertEqual(frontier.calls, 1)
        routing = choice.metadata["routing"]
        self.assertEqual(routing["handledBy"]["role"], "frontier_escalation")
        self.assertEqual(routing["attempts"][0]["status"], "deferred")
        self.assertEqual(routing["attempts"][0]["reason"], "local-model-unavailable")
        self.assertEqual(
            routing["attempts"][0]["metadata"]["errorType"],
            "LocalModelUnavailableError",
        )

    def test_invalid_local_model_output_fails_closed_without_frontier_substitution(self):
        backend = FixtureLocalBackend(
            {
                "channel": "action",
                "semanticId": "invented-action",
                "params": {},
            }
        )
        pilot, frontier = runtime(backend)

        with self.assertRaisesRegex(
            PilotContractError,
            "did not identify exactly one current legal action",
        ):
            pilot.choose(action_observation())
        self.assertEqual(frontier.calls, 0)

    def test_structured_local_scope_hides_live_decision_id_and_injects_it_locally(self):
        backend = FixtureLocalBackend(
            {
                "channel": "decision",
                "response": {
                    "type": "ChooseTargetsResponse",
                    "targets": ["target-1"],
                },
            }
        )
        pilot, frontier = runtime(
            backend,
            local_scope=LocalModelScope(
                structured_decision_kinds=("CHOOSE_TARGETS",),
            ),
        )

        choice = choose_for_observation(pilot, structured_observation())

        self.assertEqual(frontier.calls, 0)
        self.assertNotIn("decisionId", backend.inputs[0]["pendingDecision"])
        self.assertEqual(choice.response["decisionId"], "live-decision-9")
        self.assertEqual(choice.metadata["routing"]["handledBy"]["role"], "local_generalist")

    def test_fixture_demonstrates_measurable_bounded_local_subset(self):
        backend = FixtureLocalBackend(
            {
                "channel": "action",
                "semanticId": "argentum-action-v1:pass",
                "params": {},
            }
        )
        pilot, _ = runtime(backend)

        choices = [
            choose_for_observation(pilot, action_observation()),
            choose_for_observation(pilot, structured_observation()),
        ]
        handled_roles = [choice.metadata["routing"]["handledBy"]["role"] for choice in choices]

        self.assertEqual(handled_roles.count("local_generalist"), 1)
        self.assertEqual(handled_roles.count("frontier_escalation"), 1)
        self.assertEqual(
            handled_roles.count("local_generalist") / len(handled_roles),
            0.5,
        )


if __name__ == "__main__":
    unittest.main()
