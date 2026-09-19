import unittest

from commander_gym.pilot import ArgentumActionChoice, ArgentumDecisionChoice, PilotContractError
from commander_gym.pilot_execution import (
    ArgentumGymEnvironment,
    execute_pilot_choice,
)


def action_observation(*, semantic_id="argentum-action@v1:pass"):
    return {
        "type": "Game",
        "schemaHash": "argentum-gym-contract@v1.6-semantic-provenance",
        "stateDigest": "state-a",
        "perspectivePlayerId": "player-1",
        "agentToAct": "player-1",
        "pendingDecision": None,
        "legalActions": [
            {
                "actionId": 7,
                "semanticId": semantic_id,
                "kind": "PassPriority",
                "description": "Pass priority",
            }
        ],
        "terminated": False,
    }


def structured_observation():
    return {
        "type": "Game",
        "schemaHash": "argentum-gym-contract@v1.6-semantic-provenance",
        "stateDigest": "state-b",
        "perspectivePlayerId": "player-1",
        "agentToAct": "player-1",
        "pendingDecision": {
            "decisionId": "routing-nonce-1",
            "semanticId": "argentum-decision@v1:choose-targets",
            "kind": "CHOOSE_TARGETS",
            "requiresStructuredResponse": True,
        },
        "legalActions": [],
        "terminated": False,
    }


class StaticPilot:
    name = "synthetic"
    version = "1"

    def __init__(self, choice):
        self.choice = choice

    def choose(self, observation):
        return self.choice


class FakeEnvironment:
    def __init__(self, observation, result=None):
        self.observation = observation
        self.result = result or {
            "type": "Game",
            "schemaHash": "argentum-gym-contract@v1.6-semantic-provenance",
            "stateDigest": "state-next",
            "perspectivePlayerId": "player-2",
            "agentToAct": "player-2",
            "pendingDecision": None,
            "legalActions": [],
            "terminated": False,
        }
        self.submissions = []

    def observe(self):
        return self.observation

    def submit_action(self, action_id, params):
        self.submissions.append(("action", action_id, dict(params)))
        return self.result

    def submit_decision(self, response):
        self.submissions.append(("decision", dict(response)))
        return self.result


class PilotExecutionTests(unittest.TestCase):
    def test_executes_legal_action_and_records_argentum_semantic_identity(self):
        env = FakeEnvironment(action_observation())
        pilot = StaticPilot(
            ArgentumActionChoice(
                action_id=7,
                params={"xValue": 0},
                metadata={"route": "mechanical"},
            )
        )

        trace = execute_pilot_choice(pilot, env)

        self.assertEqual(env.submissions, [("action", 7, {"xValue": 0})])
        self.assertEqual(trace.channel, "action")
        self.assertEqual(trace.semantic_id, "argentum-action@v1:pass")
        self.assertEqual(trace.live_routing_id, 7)
        self.assertEqual(trace.result_observation["stateDigest"], "state-next")
        self.assertEqual(trace.pilot_metadata, {"route": "mechanical"})
        self.assertEqual(trace.to_dict()["semanticId"], trace.semantic_id)

    def test_executes_structured_decision_using_pending_semantic_identity(self):
        env = FakeEnvironment(structured_observation())
        response = {
            "type": "ChooseTargetsResponse",
            "decisionId": "routing-nonce-1",
            "targets": ["target-1"],
        }
        pilot = StaticPilot(
            ArgentumDecisionChoice(response=response, metadata={"route": "model"})
        )

        trace = execute_pilot_choice(pilot, env)

        self.assertEqual(env.submissions, [("decision", response)])
        self.assertEqual(trace.channel, "decision")
        self.assertEqual(trace.semantic_id, "argentum-decision@v1:choose-targets")
        self.assertEqual(trace.live_routing_id, "routing-nonce-1")
        self.assertEqual(trace.submitted, response)

    def test_missing_semantic_identity_fails_before_mutation(self):
        env = FakeEnvironment(action_observation(semantic_id=None))
        pilot = StaticPilot(ArgentumActionChoice(action_id=7))

        with self.assertRaisesRegex(PilotContractError, "semanticId"):
            execute_pilot_choice(pilot, env)

        self.assertEqual(env.submissions, [])

    def test_stale_choice_fails_before_mutation(self):
        env = FakeEnvironment(action_observation())
        pilot = StaticPilot(ArgentumActionChoice(action_id=99))

        with self.assertRaisesRegex(PilotContractError, "not legal"):
            execute_pilot_choice(pilot, env)

        self.assertEqual(env.submissions, [])

    def test_gym_environment_is_only_a_transport_adapter(self):
        class FakeClient:
            def __init__(self):
                self.calls = []

            def observe_env(self, env_id):
                self.calls.append(("observe", env_id))
                return action_observation()

            def step_env(self, env_id, action_id, *, params=None):
                self.calls.append(("step", env_id, action_id, dict(params or {})))
                return {"stateDigest": "after-step"}

            def submit_decision(self, env_id, response):
                self.calls.append(("decision", env_id, dict(response)))
                return {"stateDigest": "after-decision"}

        client = FakeClient()
        env = ArgentumGymEnvironment(client, "env-1")

        env.observe()
        env.submit_action(7, {"xValue": 2})
        env.submit_decision({"decisionId": "d-1", "type": "YesNoResponse"})

        self.assertEqual(
            client.calls,
            [
                ("observe", "env-1"),
                ("step", "env-1", 7, {"xValue": 2}),
                ("decision", "env-1", {"decisionId": "d-1", "type": "YesNoResponse"}),
            ],
        )


if __name__ == "__main__":
    unittest.main()
