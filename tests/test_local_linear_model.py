import unittest

from commander_gym.deck_package import ArtifactRef
from commander_gym.identity import Pilot
from commander_gym.local_linear_model import (
    LINEAR_ACTION_MODEL_SCHEMA,
    LinearActionModel,
    LinearActionModelBackend,
)
from commander_gym.local_model import LocalModelScope, LocalModelSubsystem
from commander_gym.pilot import ArgentumActionChoice, ArgentumDecisionChoice, PilotContractError
from commander_gym.pilot_composition import (
    PilotComponentRegistry,
    SubsystemDecision,
    compose_pilot_runtime,
)


FRONTIER_REF = ArtifactRef(
    kind="provider",
    artifact_id="fixture-frontier",
    version="1",
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
                "validAttackers": ["creature-1", "creature-2"],
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


def concrete_local_model():
    return LinearActionModel.from_mapping(
        {
            "schema": LINEAR_ACTION_MODEL_SCHEMA,
            "modelId": "foundation-linear-magic-action",
            "version": "r1",
            "bias": 0.0,
            "weights": {
                "kind:DeclareAttackers": 0.5,
                "validAttackerCount": 1.0,
                "validAttackTargetCount": 0.25,
                "kind:PassPriority": -0.25,
            },
        }
    )


def concrete_runtime():
    model = concrete_local_model()
    backend = LinearActionModelBackend(model)
    local_ref = ArtifactRef(
        kind="local-model",
        artifact_id=model.model_id,
        version=model.version,
        digest=model.digest,
    )
    pilot = Pilot(
        pilot_id="concrete-local-foundation-pilot",
        revision="r1",
        generalist_base=local_ref,
        escalation_provider=FRONTIER_REF,
    )
    local = LocalModelSubsystem(
        backend=backend,
        scope=LocalModelScope(action_kinds=("PassPriority", "DeclareAttackers")),
    )
    frontier = FixtureFrontierSubsystem()
    registry = PilotComponentRegistry(
        {
            PilotComponentRegistry.key_for(local_ref): local,
            PilotComponentRegistry.key_for(FRONTIER_REF): frontier,
        }
    )
    return compose_pilot_runtime(pilot, registry), frontier, model


class ConcreteLocalModelFoundationTests(unittest.TestCase):
    def test_concrete_local_numeric_model_replaces_frontier_for_bounded_strategic_case(self):
        pilot, frontier, model = concrete_runtime()

        choice = pilot.choose(action_observation())

        self.assertIsInstance(choice, ArgentumActionChoice)
        self.assertEqual(choice.action_id, 7)
        self.assertEqual(frontier.calls, 0)
        routing = choice.metadata["routing"]
        self.assertEqual(routing["handledBy"]["role"], "local_generalist")
        self.assertEqual(routing["handledBy"]["component"]["artifactId"], model.model_id)
        self.assertEqual(routing["handledBy"]["component"]["version"], model.version)
        self.assertEqual(routing["handledBy"]["component"]["digest"], model.digest)
        self.assertEqual(choice.metadata["localModel"]["backend"], model.model_id)
        self.assertEqual(choice.metadata["localModel"]["backendVersion"], model.version)

    def test_same_local_model_pilot_escalates_unsupported_family_and_proves_measurable_subset(self):
        pilot, frontier, _ = concrete_runtime()

        local_choice = pilot.choose(action_observation())
        escalated_choice = pilot.choose(structured_observation())

        self.assertIsInstance(local_choice, ArgentumActionChoice)
        self.assertIsInstance(escalated_choice, ArgentumDecisionChoice)
        self.assertEqual(frontier.calls, 1)
        handled_roles = [
            local_choice.metadata["routing"]["handledBy"]["role"],
            escalated_choice.metadata["routing"]["handledBy"]["role"],
        ]
        self.assertEqual(handled_roles.count("local_generalist"), 1)
        self.assertEqual(handled_roles.count("frontier_escalation"), 1)
        self.assertEqual(handled_roles.count("local_generalist") / len(handled_roles), 0.5)
        self.assertEqual(
            escalated_choice.metadata["routing"]["attempts"][0]["reason"],
            "outside-certified-local-model-scope",
        )

    def test_model_digest_is_deterministic_and_changes_with_weights(self):
        model = concrete_local_model()
        same = LinearActionModel.from_mapping(model.to_mapping())
        changed_payload = model.to_mapping()
        changed_payload["weights"]["validAttackerCount"] = 2.0
        changed = LinearActionModel.from_mapping(changed_payload)

        self.assertEqual(model.digest, same.digest)
        self.assertNotEqual(model.digest, changed.digest)

    def test_model_rejects_live_routing_handles_and_ambiguous_scores(self):
        model = LinearActionModel.from_mapping(
            {
                "modelId": "ambiguous-model",
                "version": "r1",
                "weights": {"affordable": 1.0},
            }
        )
        backend = LinearActionModelBackend(model)
        observation = action_observation()

        with self.assertRaisesRegex(PilotContractError, "must not contain actionId"):
            backend.infer(observation)

        sanitized = {
            **observation,
            "legalActions": [
                {key: value for key, value in action.items() if key != "actionId"}
                for action in observation["legalActions"]
            ],
        }
        with self.assertRaisesRegex(PilotContractError, "non-unique best legal action"):
            backend.infer(sanitized)


if __name__ == "__main__":
    unittest.main()
