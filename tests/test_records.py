import unittest

from commander_gym import (
    ActionRecord,
    DecisionRecord,
    PilotProvenance,
    RecordValidationError,
    StructuredDecisionRecord,
)


class DecisionRecordTests(unittest.TestCase):
    def record(self):
        return DecisionRecord(
            game_id="game-1",
            decision_id="decision-1",
            decision_type="LEGAL_ACTION",
            seat=0,
            observation_schema="schema-v1",
            observation={"perspectivePlayerId": 1},
            legal_actions=[ActionRecord(action_id="action-a", label="Pass")],
            chosen_action_id="action-a",
            pilot=PilotProvenance(source="test", implementation="fixture", version="v1"),
            deck_id="package-a",
            deck_version="revision-1",
        )

    def test_round_trip(self):
        record = self.record()
        self.assertEqual(DecisionRecord.from_dict(record.to_dict()), record)

    def test_rejects_choice_outside_legal_set(self):
        record = self.record()
        invalid = DecisionRecord(
            **{**record.__dict__, "chosen_action_id": "not-legal"}
        )
        with self.assertRaises(RecordValidationError):
            invalid.validate()

    def test_rejects_duplicate_action_ids(self):
        record = self.record()
        invalid = DecisionRecord(
            **{
                **record.__dict__,
                "legal_actions": [
                    ActionRecord(action_id="dup"),
                    ActionRecord(action_id="dup"),
                ],
                "chosen_action_id": "dup",
            }
        )
        with self.assertRaises(RecordValidationError):
            invalid.validate()


if __name__ == "__main__":
    unittest.main()


class StructuredDecisionRecordTests(unittest.TestCase):
    def record(self):
        return StructuredDecisionRecord(
            game_id="game-1",
            decision_id="decision-structured-1",
            decision_type="CHOOSE_TARGETS",
            seat=0,
            observation_schema="schema-v1",
            observation={"pendingDecision": {"semanticId": "decision-semantic"}},
            native_decision_semantic_id="decision-semantic",
            response={"type": "ChooseTargetsResponse", "targets": ["entity-3"]},
            pilot=PilotProvenance(source="test", implementation="fixture", version="v1"),
            deck_id="package-a",
            deck_version="revision-1",
        )

    def test_round_trip(self):
        record = self.record()
        self.assertEqual(StructuredDecisionRecord.from_dict(record.to_dict()), record)

    def test_rejects_live_routing_id_in_durable_response(self):
        record = self.record()
        invalid = StructuredDecisionRecord(
            **{
                **record.__dict__,
                "response": {
                    "type": "ChooseTargetsResponse",
                    "decisionId": "live-routing",
                    "targets": ["entity-3"],
                },
            }
        )
        with self.assertRaises(RecordValidationError):
            invalid.validate()
