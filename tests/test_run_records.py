import unittest

from commander_gym import (
    EngineProvenance,
    PilotProvenance,
    RecordValidationError,
    RunParticipant,
    RunRecord,
    RunTermination,
)


class RunRecordTests(unittest.TestCase):
    def record(self):
        return RunRecord(
            run_id="run-1",
            experiment_id="experiment-a",
            benchmark_id="generalist-public-v1",
            game_id="game-1",
            started_at="2026-09-19T16:00:00Z",
            finished_at="2026-09-19T16:05:00+00:00",
            engine=EngineProvenance(
                implementation="argentum",
                version="0.1.0",
                schema="gym-schema-v2",
                revision="abcdef0",
            ),
            participants=[
                RunParticipant(
                    seat=0,
                    pilot=PilotProvenance(
                        source="commander-gym",
                        implementation="fixture-pilot",
                        version="v1",
                    ),
                    deck_id="public-fixture",
                    deck_version="revision-1",
                )
            ],
            termination=RunTermination(status="completed"),
            seed=1234,
            decision_ids=["decision-a", "decision-b"],
            metrics={"score": 0.75},
            metadata={"purpose": "synthetic-test"},
        )

    def test_round_trip(self):
        record = self.record()
        self.assertEqual(RunRecord.from_dict(record.to_dict()), record)

    def test_failed_run_requires_reason_and_domain(self):
        record = self.record()
        invalid = RunRecord(
            **{
                **record.__dict__,
                "termination": RunTermination(status="failed", reason="model timeout"),
            }
        )
        with self.assertRaises(RecordValidationError):
            invalid.validate()

    def test_failure_before_game_or_participant_is_recordable(self):
        record = RunRecord(
            run_id="preflight-failure",
            started_at="2026-09-19T16:00:00Z",
            finished_at="2026-09-19T16:00:01Z",
            engine=EngineProvenance(implementation="argentum", version="0.1.0"),
            termination=RunTermination(
                status="failed",
                reason="engine unavailable",
                failure_domain="engine",
            ),
        )
        self.assertEqual(RunRecord.from_dict(record.to_dict()), record)

    def test_decision_refs_require_game_identity(self):
        record = self.record()
        invalid = RunRecord(**{**record.__dict__, "game_id": None})
        with self.assertRaises(RecordValidationError):
            invalid.validate()

    def test_rejects_duplicate_seats(self):
        record = self.record()
        duplicate = RunParticipant(
            seat=0,
            pilot=PilotProvenance(source="test", implementation="other"),
        )
        invalid = RunRecord(
            **{**record.__dict__, "participants": [record.participants[0], duplicate]}
        )
        with self.assertRaises(RecordValidationError):
            invalid.validate()

    def test_rejects_naive_or_reversed_timestamps(self):
        record = self.record()
        naive = RunRecord(**{**record.__dict__, "started_at": "2026-09-19T16:00:00"})
        with self.assertRaises(RecordValidationError):
            naive.validate()

        reversed_time = RunRecord(
            **{
                **record.__dict__,
                "started_at": "2026-09-19T16:05:01Z",
                "finished_at": "2026-09-19T16:05:00Z",
            }
        )
        with self.assertRaises(RecordValidationError):
            reversed_time.validate()

    def test_completed_run_cannot_claim_failure_domain(self):
        record = self.record()
        invalid = RunRecord(
            **{
                **record.__dict__,
                "termination": RunTermination(
                    status="completed",
                    failure_domain="engine",
                ),
            }
        )
        with self.assertRaises(RecordValidationError):
            invalid.validate()

    def test_deck_identity_is_versioned_as_a_pair(self):
        record = self.record()
        invalid_participant = RunParticipant(
            seat=0,
            pilot=record.participants[0].pilot,
            deck_id="public-fixture",
        )
        invalid = RunRecord(
            **{**record.__dict__, "participants": [invalid_participant]}
        )
        with self.assertRaises(RecordValidationError):
            invalid.validate()


if __name__ == "__main__":
    unittest.main()
