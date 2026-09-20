import json
import tempfile
import unittest
from pathlib import Path

from commander_gym import DecisionRecord, RunRecord, StructuredDecisionRecord
from commander_gym.benchmark import (
    BENCHMARK_SUITE_IDENTITY_SCHEMA,
    benchmark_suite_identity,
    load_jsonl,
)
from commander_gym.dataset_export import write_run_dataset_jsonl


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "learning"
BENCHMARK_PATH = FIXTURE_ROOT / "benchmark_public_synthetic_v1.jsonl"
RUN_EVIDENCE_PATH = FIXTURE_ROOT / "run_evidence_public_synthetic_v1.json"
EXPECTED_DATASET_PATH = FIXTURE_ROOT / "dataset_public_synthetic_v1.jsonl"


class PublicLearningFixtureTests(unittest.TestCase):
    def test_committed_benchmark_fixture_loads_and_has_stable_identity(self):
        cases = load_jsonl(BENCHMARK_PATH)

        self.assertEqual(len(cases), 1)
        self.assertTrue(cases[0].held_out)
        self.assertIn("synthetic", cases[0].tags)
        identity = benchmark_suite_identity(cases)
        self.assertEqual(identity["schema"], BENCHMARK_SUITE_IDENTITY_SCHEMA)
        self.assertEqual(identity["case_count"], 1)
        self.assertRegex(identity["fingerprint"], r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(identity, benchmark_suite_identity(load_jsonl(BENCHMARK_PATH)))

    def test_committed_run_fixture_exports_the_expected_dataset(self):
        payload = json.loads(RUN_EVIDENCE_PATH.read_text(encoding="utf-8"))
        run = RunRecord.from_dict(payload["run"])
        records = []
        for item in payload["records"]:
            if item["record_kind"] == "action":
                records.append(DecisionRecord.from_dict(item["record"]))
            elif item["record_kind"] == "structured_decision":
                records.append(StructuredDecisionRecord.from_dict(item["record"]))
            else:
                self.fail(f"unknown public fixture record kind: {item['record_kind']}")

        benchmark_cases = load_jsonl(BENCHMARK_PATH)
        held_out_ids = {case.decision.decision_id for case in benchmark_cases}
        self.assertTrue(held_out_ids.isdisjoint(run.decision_ids))
        self.assertTrue(all(record.metadata.get("synthetic") is True for record in records))

        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "dataset.jsonl"
            write_run_dataset_jsonl(
                destination,
                run,
                records,
                benchmark_paths=[BENCHMARK_PATH],
            )
            self.assertEqual(
                destination.read_bytes(),
                EXPECTED_DATASET_PATH.read_bytes(),
            )

        rows = [
            json.loads(line)
            for line in EXPECTED_DATASET_PATH.read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(
            [row["provenance"]["decision_id"] for row in rows],
            run.decision_ids,
        )
        self.assertTrue(all("deck_id" not in row["input"] for row in rows))
        self.assertTrue(all("pilot" not in row["input"] for row in rows))


if __name__ == "__main__":
    unittest.main()
