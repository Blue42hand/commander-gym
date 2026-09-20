import json
import tempfile
import unittest
from pathlib import Path

from commander_gym import (
    ActionRecord,
    DecisionRecord,
    EngineProvenance,
    PilotProvenance,
    RunRecord,
    RunTermination,
    StructuredDecisionRecord,
)
from commander_gym.dataset_export import (
    DATASET_EXPORT_SCHEMA_VERSION,
    DatasetExportError,
    build_run_dataset_rows,
    write_run_dataset_jsonl,
)


class DatasetExportTests(unittest.TestCase):
    def action_record(self):
        return DecisionRecord(
            game_id="game-1",
            decision_id="decision-1",
            decision_type="LEGAL_ACTION",
            seat=0,
            observation_schema="argentum-schema-v1",
            observation={
                "perspectivePlayerId": 1,
                "stateDigest": "state-a",
                "legalActions": [
                    {"actionId": 7, "semanticId": "semantic-pass"},
                    {"actionId": 8, "semanticId": "semantic-play"},
                ],
            },
            legal_actions=[
                ActionRecord(action_id="semantic-pass", payload={"kind": "pass"}),
                ActionRecord(action_id="semantic-play", payload={"kind": "play"}),
            ],
            chosen_action_id="semantic-play",
            pilot=PilotProvenance(
                source="commander-gym",
                implementation="fixture-pilot",
                version="v1",
            ),
            deck_id="public-fixture",
            deck_version="revision-1",
            outcome={"stateDigest": "state-b"},
        )

    def structured_record(self):
        return StructuredDecisionRecord(
            game_id="game-1",
            decision_id="decision-2",
            decision_type="CHOOSE_TARGETS",
            seat=1,
            observation_schema="argentum-schema-v1",
            observation={
                "perspectivePlayerId": 2,
                "pendingDecision": {"semanticId": "semantic-target-choice"},
            },
            native_decision_semantic_id="semantic-target-choice",
            response={"type": "ChooseTargetsResponse", "targets": ["entity-3"]},
            pilot=PilotProvenance(
                source="commander-gym",
                implementation="fixture-pilot",
                version="v1",
            ),
            deck_id="public-fixture-2",
            deck_version="revision-2",
        )

    def run_record(self):
        return RunRecord(
            run_id="run-1",
            experiment_id="experiment-a",
            game_id="game-1",
            started_at="2026-09-20T16:00:00Z",
            finished_at="2026-09-20T16:01:00Z",
            engine=EngineProvenance(
                implementation="argentum",
                version="build-123",
                schema="argentum-schema-v1",
                revision="build-123",
            ),
            termination=RunTermination(status="stopped", reason="bounded fixture"),
            seed=17,
            decision_ids=["decision-1", "decision-2"],
        )

    def test_rows_preserve_native_evidence_and_run_provenance(self):
        rows = build_run_dataset_rows(
            self.run_record(),
            [self.action_record(), self.structured_record()],
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["dataset_schema_version"], DATASET_EXPORT_SCHEMA_VERSION)
        self.assertEqual(rows[0]["run_id"], "run-1")
        self.assertEqual(rows[0]["seed"], 17)
        self.assertEqual(rows[0]["engine"]["schema"], "argentum-schema-v1")
        self.assertEqual(rows[0]["record_kind"], "action")
        self.assertEqual(
            [action["action_id"] for action in rows[0]["decision"]["legal_actions"]],
            ["semantic-pass", "semantic-play"],
        )
        self.assertEqual(rows[0]["decision"]["observation"]["stateDigest"], "state-a")
        self.assertEqual(rows[1]["record_kind"], "structured_decision")
        self.assertEqual(
            rows[1]["decision"]["native_decision_semantic_id"],
            "semantic-target-choice",
        )
        self.assertNotIn("decisionId", rows[1]["decision"]["response"])

    def test_requires_exact_run_join_and_game_identity(self):
        run = self.run_record()
        with self.assertRaises(DatasetExportError):
            build_run_dataset_rows(
                run,
                [self.structured_record(), self.action_record()],
            )

        wrong_game = DecisionRecord(
            **{**self.action_record().__dict__, "game_id": "different-game"}
        )
        with self.assertRaises(DatasetExportError):
            build_run_dataset_rows(
                RunRecord(**{**run.__dict__, "decision_ids": ["decision-1"]}),
                [wrong_game],
            )

    def test_held_out_benchmark_overlap_fails_before_replacing_destination(self):
        action = self.action_record()
        run = RunRecord(
            **{**self.run_record().__dict__, "decision_ids": [action.decision_id]}
        )
        benchmark_row = {
            "schema_version": 1,
            "case_id": "held-out-1",
            "category": "sequencing",
            "decision": action.to_dict(),
            "judgment": {
                "preferred_action_ids": ["semantic-play"],
                "ranked_action_ids": ["semantic-play", "semantic-pass"],
                "provenance": {"source": "synthetic-test"},
            },
            "held_out": True,
            "tags": ["public-fixture"],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            benchmark_path = root / "benchmark.jsonl"
            benchmark_path.write_text(json.dumps(benchmark_row) + "\n", encoding="utf-8")
            destination = root / "training.jsonl"
            destination.write_text("sentinel\n", encoding="utf-8")

            with self.assertRaises(DatasetExportError):
                write_run_dataset_jsonl(
                    destination,
                    run,
                    [action],
                    benchmark_paths=[benchmark_path],
                )

            self.assertEqual(destination.read_text(encoding="utf-8"), "sentinel\n")

    def test_jsonl_output_is_deterministic_and_self_contained(self):
        run = self.run_record()
        records = [self.action_record(), self.structured_record()]

        with tempfile.TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "first.jsonl"
            second = Path(temp_dir) / "second.jsonl"
            write_run_dataset_jsonl(first, run, records)
            write_run_dataset_jsonl(second, run, records)

            self.assertEqual(first.read_bytes(), second.read_bytes())
            rows = [json.loads(line) for line in first.read_text().splitlines()]
            self.assertEqual([row["decision"]["decision_id"] for row in rows], run.decision_ids)
            self.assertTrue(all(row["engine"]["revision"] == "build-123" for row in rows))


if __name__ == "__main__":
    unittest.main()
