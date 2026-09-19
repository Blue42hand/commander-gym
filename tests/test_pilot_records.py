import unittest

from commander_gym.pilot_execution import PilotExecutionTrace
from commander_gym.pilot_records import (
    PilotRecordContext,
    PilotRecordError,
    decision_record_from_execution_trace,
    structured_decision_record_from_execution_trace,
)
from commander_gym.records import DecisionRecord, StructuredDecisionRecord


def action_observation():
    return {
        "type": "Game",
        "schemaHash": "argentum-gym-contract@v1.7-semantic-state-provenance",
        "stateDigest": "state-a",
        "perspectivePlayerId": "player-1",
        "agentToAct": "player-1",
        "pendingDecision": None,
        "legalActions": [
            {
                "actionId": 7,
                "semanticId": "argentum-action@v1:pass",
                "kind": "PassPriority",
                "description": "Pass priority",
                "affordable": True,
                "isDecisionOption": False,
            },
            {
                "actionId": 8,
                "semanticId": "argentum-action@v1:play-land",
                "kind": "PlayLand",
                "description": "Play Mountain",
                "affordable": True,
                "sourceEntityId": "card-1",
                "isDecisionOption": False,
            },
        ],
        "terminated": False,
    }


def result_observation():
    return {
        "type": "Game",
        "schemaHash": "argentum-gym-contract@v1.7-semantic-state-provenance",
        "stateDigest": "state-b",
        "perspectivePlayerId": "player-1",
        "agentToAct": "player-2",
        "pendingDecision": {
            "decisionId": "next-routing-nonce",
            "semanticId": "argentum-decision@v1:next",
            "kind": "YES_NO",
            "requiresStructuredResponse": False,
        },
        "legalActions": [
            {
                "actionId": 0,
                "semanticId": "argentum-response@v1:yes",
                "kind": "DECISION",
                "description": "Yes",
                "isDecisionOption": True,
            }
        ],
        "terminated": False,
    }


def context():
    return PilotRecordContext(
        game_id="game-1",
        decision_id="decision-1",
        seat=0,
        deck_id="deck-a",
        deck_version="rev-1",
        primer_version="primer-2",
    )


def trace(observation=None):
    return PilotExecutionTrace(
        pilot_name="certified-routing",
        pilot_version="1",
        channel="action",
        semantic_id="argentum-action@v1:pass",
        live_routing_id=7,
        observation=observation or action_observation(),
        submitted={"actionId": 7, "params": {}},
        result_observation=result_observation(),
        pilot_metadata={
            "routing": {
                "path": "mechanical",
                "handler": "forced-parameterless-choice",
                "strategicWakeAvoided": True,
            }
        },
    )


class PilotRecordBridgeTests(unittest.TestCase):
    def test_records_argentum_semantic_candidates_and_chosen_identity(self):
        record = decision_record_from_execution_trace(trace(), context())

        self.assertEqual(record.decision_type, "PassPriority")
        self.assertEqual(record.chosen_action_id, "argentum-action@v1:pass")
        self.assertEqual(
            [candidate.action_id for candidate in record.legal_actions],
            ["argentum-action@v1:pass", "argentum-action@v1:play-land"],
        )
        self.assertEqual(record.pilot.implementation, "certified-routing")
        self.assertEqual(record.pilot.version, "1")
        self.assertEqual(record.observation_schema, action_observation()["schemaHash"])
        self.assertEqual(record.metadata["result_state_digest"], "state-b")
        self.assertTrue(
            record.metadata["pilot_metadata"]["routing"]["strategicWakeAvoided"]
        )
        self.assertEqual(DecisionRecord.from_dict(record.to_dict()), record)

    def test_keeps_live_routing_only_outside_model_facing_observation_and_candidates(self):
        record = decision_record_from_execution_trace(trace(), context())

        self.assertNotIn("actionId", record.observation["legalActions"][0])
        self.assertNotIn("actionId", record.legal_actions[0].payload)
        self.assertNotIn("semanticId", record.legal_actions[0].payload)
        self.assertEqual(record.metadata["live_routing_id"], 7)
        self.assertEqual(record.metadata["submitted"], {"actionId": 7, "params": {}})

        result = record.outcome["result_observation"]
        self.assertNotIn("decisionId", result["pendingDecision"])
        self.assertNotIn("actionId", result["legalActions"][0])
        self.assertEqual(
            result["pendingDecision"]["semanticId"],
            "argentum-decision@v1:next",
        )

    def test_uses_pending_native_kind_for_folded_decision_actions(self):
        observation = action_observation()
        observation["pendingDecision"] = {
            "decisionId": "routing-folded",
            "semanticId": "argentum-decision@v1:yes-no",
            "kind": "YES_NO",
            "requiresStructuredResponse": False,
        }
        observation["legalActions"] = [
            {
                "actionId": 3,
                "semanticId": "argentum-response@v1:yes",
                "kind": "DECISION",
                "description": "Yes",
                "isDecisionOption": True,
            }
        ]
        folded = PilotExecutionTrace(
            pilot_name="provider-neutral",
            pilot_version="1",
            channel="action",
            semantic_id="argentum-response@v1:yes",
            live_routing_id=3,
            observation=observation,
            submitted={"actionId": 3, "params": {}},
            result_observation=result_observation(),
            pilot_metadata={"routing": {"path": "strategic"}},
        )

        record = decision_record_from_execution_trace(folded, context())

        self.assertEqual(record.decision_type, "YES_NO")
        self.assertEqual(record.chosen_action_id, "argentum-response@v1:yes")
        self.assertNotIn("decisionId", record.observation["pendingDecision"])

    def test_structured_response_fails_closed_instead_of_fabricating_legal_action(self):
        structured = PilotExecutionTrace(
            pilot_name="provider-neutral",
            pilot_version="1",
            channel="decision",
            semantic_id="argentum-decision@v1:choose-targets",
            live_routing_id="routing-1",
            observation={
                "schemaHash": "argentum-gym-contract@v1.7-semantic-state-provenance",
                "stateDigest": "structured-a",
                "pendingDecision": {
                    "decisionId": "routing-1",
                    "semanticId": "argentum-decision@v1:choose-targets",
                    "kind": "CHOOSE_TARGETS",
                    "requiresStructuredResponse": True,
                },
                "legalActions": [],
            },
            submitted={
                "type": "ChooseTargetsResponse",
                "decisionId": "routing-1",
                "targets": ["entity-3"],
            },
            result_observation={"stateDigest": "structured-b"},
            pilot_metadata={},
        )

        with self.assertRaisesRegex(PilotRecordError, "structured decision"):
            decision_record_from_execution_trace(structured, context())

    def test_structured_response_records_native_decision_without_fake_candidates(self):
        structured = PilotExecutionTrace(
            pilot_name="provider-neutral",
            pilot_version="1",
            channel="decision",
            semantic_id="argentum-decision@v1:choose-targets",
            live_routing_id="routing-1",
            observation={
                "schemaHash": "argentum-gym-contract@v1.7-semantic-state-provenance",
                "stateDigest": "structured-a",
                "pendingDecision": {
                    "decisionId": "routing-1",
                    "semanticId": "argentum-decision@v1:choose-targets",
                    "kind": "CHOOSE_TARGETS",
                    "requiresStructuredResponse": True,
                },
                "legalActions": [],
            },
            submitted={
                "type": "ChooseTargetsResponse",
                "decisionId": "routing-1",
                "targets": ["entity-3"],
            },
            result_observation={
                "schemaHash": "argentum-gym-contract@v1.7-semantic-state-provenance",
                "stateDigest": "structured-b",
                "pendingDecision": None,
                "legalActions": [],
            },
            pilot_metadata={"model": "test-model", "routing": {"path": "strategic"}},
        )

        record = structured_decision_record_from_execution_trace(structured, context())

        self.assertIsInstance(record, StructuredDecisionRecord)
        self.assertEqual(record.decision_type, "CHOOSE_TARGETS")
        self.assertEqual(
            record.native_decision_semantic_id,
            "argentum-decision@v1:choose-targets",
        )
        self.assertEqual(
            record.response,
            {"type": "ChooseTargetsResponse", "targets": ["entity-3"]},
        )
        self.assertNotIn("decisionId", record.observation["pendingDecision"])
        self.assertEqual(record.metadata["live_routing_id"], "routing-1")
        self.assertEqual(record.pilot.model, "test-model")
        self.assertEqual(StructuredDecisionRecord.from_dict(record.to_dict()), record)

    def test_structured_response_rejects_mismatched_live_routing(self):
        structured = PilotExecutionTrace(
            pilot_name="provider-neutral",
            pilot_version="1",
            channel="decision",
            semantic_id="argentum-decision@v1:choose-targets",
            live_routing_id="routing-1",
            observation={
                "schemaHash": "schema-v1",
                "stateDigest": "state-a",
                "pendingDecision": {
                    "decisionId": "routing-1",
                    "semanticId": "argentum-decision@v1:choose-targets",
                    "kind": "CHOOSE_TARGETS",
                    "requiresStructuredResponse": True,
                },
                "legalActions": [],
            },
            submitted={
                "type": "ChooseTargetsResponse",
                "decisionId": "wrong-routing",
                "targets": ["entity-3"],
            },
            result_observation={"stateDigest": "state-b"},
            pilot_metadata={},
        )

        with self.assertRaisesRegex(PilotRecordError, "live routing id"):
            structured_decision_record_from_execution_trace(structured, context())

    def test_missing_candidate_semantic_identity_fails_closed(self):
        observation = action_observation()
        observation["legalActions"][1]["semanticId"] = None

        with self.assertRaisesRegex(PilotRecordError, "semanticId"):
            decision_record_from_execution_trace(trace(observation), context())


if __name__ == "__main__":
    unittest.main()
