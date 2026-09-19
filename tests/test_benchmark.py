import json
import tempfile
import unittest
from pathlib import Path

from commander_gym.benchmark import BenchmarkCase, load_jsonl, score_action, summarize_scores
from commander_gym.records import ActionRecord, DecisionRecord, PilotProvenance, RecordValidationError
from commander_gym.training_guard import assert_training_records_exclude_benchmark


def synthetic_decision(decision_id: str = "decision-1") -> DecisionRecord:
    return DecisionRecord(
        game_id="synthetic-game",
        decision_id=decision_id,
        decision_type="priority",
        seat=0,
        observation_schema="synthetic-v1",
        observation={"public": {"turn": 3}, "private": {"hand_size": 4}},
        legal_actions=[
            ActionRecord(action_id="pass", payload={"kind": "pass"}),
            ActionRecord(action_id="cast", payload={"kind": "cast", "card": "Synthetic Spell"}),
        ],
        chosen_action_id="cast",
        pilot=PilotProvenance(source="test", implementation="synthetic-pilot", version="1"),
        deck_id="public-synthetic-deck",
        deck_version="1",
    )


def synthetic_case_dict(decision_id: str = "decision-1") -> dict:
    return {
        "schema_version": 1,
        "case_id": "synthetic-case",
        "category": "sequencing",
        "held_out": True,
        "tags": ["synthetic", "public"],
        "decision": synthetic_decision(decision_id).to_dict(),
        "judgment": {
            "preferred_action_ids": ["cast"],
            "ranked_action_ids": ["cast", "pass"],
            "rationale": "Synthetic public fixture.",
            "provenance": {"source": "unit-test"},
        },
    }


class BenchmarkSchemaTests(unittest.TestCase):
    def test_loads_scores_and_summarizes_synthetic_jsonl(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "benchmark.jsonl"
            path.write_text(json.dumps(synthetic_case_dict()) + "\n", encoding="utf-8")

            cases = load_jsonl(path)
            self.assertEqual(len(cases), 1)
            self.assertIsInstance(cases[0], BenchmarkCase)
            self.assertTrue(cases[0].held_out)

            preferred = score_action(cases[0], "cast")
            fallback = score_action(cases[0], "pass")
            self.assertEqual(preferred, {"case_id": "synthetic-case", "legal": True, "preferred": True, "rank": 1})
            self.assertEqual(fallback, {"case_id": "synthetic-case", "legal": True, "preferred": False, "rank": 2})
            self.assertEqual(
                summarize_scores([preferred, fallback]),
                {"cases": 2, "legal_rate": 1.0, "preferred_rate": 0.5},
            )

    def test_rejects_judgment_that_references_non_legal_action(self):
        value = synthetic_case_dict()
        value["judgment"]["preferred_action_ids"] = ["not-legal"]
        with self.assertRaises(RecordValidationError):
            BenchmarkCase.from_dict(value)

    def test_rejects_non_array_tags_instead_of_coercing_them(self):
        value = synthetic_case_dict()
        value["tags"] = "synthetic"
        with self.assertRaises(RecordValidationError):
            BenchmarkCase.from_dict(value)

    def test_rejects_case_not_marked_held_out(self):
        value = synthetic_case_dict()
        value["held_out"] = False
        with self.assertRaises(RecordValidationError):
            BenchmarkCase.from_dict(value)

    def test_training_guard_rejects_held_out_decision_and_allows_unrelated(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "benchmark.jsonl"
            path.write_text(json.dumps(synthetic_case_dict()) + "\n", encoding="utf-8")

            held_out = synthetic_decision("decision-1")
            with self.assertRaises(RecordValidationError):
                assert_training_records_exclude_benchmark([held_out], [path])

            unrelated = synthetic_decision("training-only-decision")
            assert_training_records_exclude_benchmark([unrelated], [path])

    def test_jsonl_loader_reports_invalid_row_with_line_number(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "benchmark.jsonl"
            path.write_text("{}\nnot-json\n", encoding="utf-8")
            with self.assertRaisesRegex(RecordValidationError, r":1:"):
                load_jsonl(path)


if __name__ == "__main__":
    unittest.main()
