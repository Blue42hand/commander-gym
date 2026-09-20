import unittest
from dataclasses import replace

from commander_gym.benchmark import (
    BENCHMARK_SUITE_IDENTITY_SCHEMA,
    BenchmarkCase,
    BenchmarkJudgment,
    benchmark_suite_identity,
)
from commander_gym.benchmark_compare import (
    BENCHMARK_COMPARISON_VERSION,
    BenchmarkComparisonError,
    compare_benchmark_reports,
)
from commander_gym.benchmark_runner import CallablePilot, first_legal_pilot, run_benchmark
from commander_gym.records import ActionRecord, DecisionRecord, PilotProvenance, RecordValidationError


def synthetic_case(case_id: str, category: str, preferred: str) -> BenchmarkCase:
    turn = 3 if case_id == "case-a" else 4
    return BenchmarkCase(
        case_id=case_id,
        category=category,
        decision=DecisionRecord(
            game_id="synthetic-game",
            decision_id=f"decision-{case_id}",
            decision_type="priority",
            seat=0,
            observation_schema="synthetic-v1",
            observation={"public": {"turn": turn, "phase": "main"}, "hand": {"size": 4}},
            legal_actions=[
                ActionRecord(action_id="pass", payload={"kind": "pass"}),
                ActionRecord(
                    action_id="cast",
                    payload={"card": "Synthetic Spell", "kind": "cast"},
                ),
            ],
            chosen_action_id="cast",
            pilot=PilotProvenance(source="test", implementation="fixture", version="1"),
            deck_id="public-synthetic-deck",
            deck_version="1",
            outcome={"winner": 0},
            metadata={"fixture": True},
        ),
        judgment=BenchmarkJudgment(
            preferred_action_ids=[preferred],
            ranked_action_ids=["cast", "pass"],
            rationale="synthetic held-out judgment",
            provenance={"source": "unit-test"},
        ),
        tags=["public", "synthetic"],
    )


class BenchmarkSuiteIdentityTests(unittest.TestCase):
    def test_identity_canonicalizes_mapping_key_order(self):
        case = synthetic_case("case-a", "sequencing", "cast")
        reordered_decision = replace(
            case.decision,
            observation={"hand": {"size": 4}, "public": {"phase": "main", "turn": 3}},
            legal_actions=[
                ActionRecord(action_id="pass", payload={"kind": "pass"}),
                ActionRecord(
                    action_id="cast",
                    payload={"kind": "cast", "card": "Synthetic Spell"},
                ),
            ],
        )
        reordered = replace(case, decision=reordered_decision)

        first = benchmark_suite_identity([case])
        second = benchmark_suite_identity([reordered])

        self.assertEqual(first["schema"], BENCHMARK_SUITE_IDENTITY_SCHEMA)
        self.assertEqual(first, second)
        self.assertTrue(first["fingerprint"].startswith("sha256:"))

    def test_identity_changes_when_held_out_evidence_changes(self):
        case = synthetic_case("case-a", "sequencing", "cast")
        changed = replace(
            case,
            judgment=replace(case.judgment, preferred_action_ids=["pass"]),
        )

        self.assertNotEqual(
            benchmark_suite_identity([case])["fingerprint"],
            benchmark_suite_identity([changed])["fingerprint"],
        )

    def test_identity_rejects_duplicate_case_ids(self):
        case = synthetic_case("case-a", "sequencing", "cast")
        with self.assertRaisesRegex(RecordValidationError, "duplicate benchmark case_id"):
            benchmark_suite_identity([case, case])


class BenchmarkComparisonTests(unittest.TestCase):
    def setUp(self):
        self.cases = [
            synthetic_case("case-a", "sequencing", "cast"),
            synthetic_case("case-b", "interaction", "pass"),
        ]

    def test_runner_attaches_exact_suite_identity(self):
        report = run_benchmark(self.cases, first_legal_pilot())

        self.assertEqual(report["benchmark"], benchmark_suite_identity(self.cases))
        self.assertEqual(report["benchmark"]["case_count"], 2)

    def test_comparison_reports_preferred_regressions_and_improvements(self):
        baseline = run_benchmark(self.cases, first_legal_pilot("baseline", "1"))
        candidate = run_benchmark(
            self.cases,
            CallablePilot("candidate", "2", lambda _: "cast"),
        )

        comparison = compare_benchmark_reports(baseline, candidate)

        self.assertEqual(comparison["schema_version"], BENCHMARK_COMPARISON_VERSION)
        self.assertEqual(comparison["benchmark"], baseline["benchmark"])
        self.assertEqual(comparison["baseline_pilot"]["name"], "baseline")
        self.assertEqual(comparison["candidate_pilot"]["name"], "candidate")
        self.assertEqual(comparison["summary"]["preferred_improvements"], 1)
        self.assertEqual(comparison["summary"]["preferred_regressions"], 1)
        self.assertEqual(comparison["summary"]["selection_changes"], 2)
        self.assertEqual(comparison["summary"]["preferred_rate"]["delta"], 0.0)
        self.assertEqual(
            [row["case_id"] for row in comparison["cases"]],
            ["case-a", "case-b"],
        )
        self.assertEqual(comparison["cases"][0]["preferred_change"], "improved")
        self.assertEqual(comparison["cases"][1]["preferred_change"], "regressed")
        self.assertIn("sequencing", comparison["categories"])
        self.assertIn("interaction", comparison["categories"])

    def test_comparison_is_deterministic_for_the_same_reports(self):
        baseline = run_benchmark(self.cases, first_legal_pilot("baseline", "1"))
        candidate = run_benchmark(
            self.cases,
            CallablePilot("candidate", "2", lambda benchmark_input: benchmark_input.legal_actions[-1].action_id),
        )

        self.assertEqual(
            compare_benchmark_reports(baseline, candidate),
            compare_benchmark_reports(baseline, candidate),
        )

    def test_comparison_rejects_different_benchmark_evidence(self):
        baseline = run_benchmark(self.cases, first_legal_pilot("baseline", "1"))
        changed_cases = [
            replace(
                self.cases[0],
                judgment=replace(self.cases[0].judgment, preferred_action_ids=["pass"]),
            ),
            self.cases[1],
        ]
        candidate = run_benchmark(changed_cases, first_legal_pilot("candidate", "2"))

        with self.assertRaisesRegex(BenchmarkComparisonError, "identities differ"):
            compare_benchmark_reports(baseline, candidate)

    def test_comparison_rejects_legacy_report_without_suite_identity(self):
        report = run_benchmark(self.cases, first_legal_pilot())
        legacy = dict(report)
        legacy.pop("benchmark")

        with self.assertRaisesRegex(BenchmarkComparisonError, "missing benchmark identity"):
            compare_benchmark_reports(legacy, report)


if __name__ == "__main__":
    unittest.main()
