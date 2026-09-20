import unittest

from commander_gym.benchmark import BenchmarkCase, BenchmarkJudgment
from commander_gym.benchmark_runner import (
    BENCHMARK_REPORT_VERSION,
    BenchmarkInput,
    CallablePilot,
    PilotDecision,
    benchmark_input_for_case,
    first_legal_pilot,
    run_benchmark,
)
from commander_gym.records import ActionRecord, DecisionRecord, PilotProvenance


def synthetic_case(case_id: str, category: str, preferred: str = "cast") -> BenchmarkCase:
    turn = 3 if case_id == "case-a" else 4
    decision = DecisionRecord(
        game_id="synthetic-game",
        decision_id=f"decision-{case_id}",
        decision_type="priority",
        seat=0,
        observation_schema="synthetic-v1",
        observation={"public": {"turn": turn}, "private": {"hand_size": 4}},
        legal_actions=[
            ActionRecord(action_id="pass", payload={"kind": "pass"}),
            ActionRecord(action_id="cast", payload={"kind": "cast", "card": "Synthetic Spell"}),
        ],
        chosen_action_id="cast",
        pilot=PilotProvenance(source="test", implementation="fixture", version="1"),
        deck_id="public-synthetic-deck",
        deck_version="1",
        primer_version="fixture-primer-v1",
        outcome={"winner": 0},
        metadata={"source": "post-decision-evidence"},
    )
    return BenchmarkCase(
        case_id=case_id,
        category=category,
        decision=decision,
        judgment=BenchmarkJudgment(
            preferred_action_ids=[preferred],
            ranked_action_ids=["cast", "pass"],
            rationale="held-out reference rationale",
            provenance={"source": "unit-test"},
        ),
        tags=["synthetic", "public"],
    )


class BenchmarkRunnerTests(unittest.TestCase):
    def test_reports_scores_categories_usage_and_pilot_provenance(self):
        cases = [
            synthetic_case("case-a", "sequencing"),
            synthetic_case("case-b", "interaction", preferred="pass"),
        ]

        def choose(benchmark_input: BenchmarkInput) -> PilotDecision:
            action_id = "cast" if benchmark_input.observation["public"]["turn"] == 3 else "pass"
            return PilotDecision(
                action_id=action_id,
                input_tokens=10,
                output_tokens=2,
                cost_usd=0.01,
                metadata={"provider": "synthetic"},
            )

        report = run_benchmark(cases, CallablePilot("synthetic-pilot", "1", choose))

        self.assertEqual(report["schema_version"], BENCHMARK_REPORT_VERSION)
        self.assertEqual(report["pilot"], {"name": "synthetic-pilot", "version": "1"})
        self.assertEqual(report["summary"]["cases"], 2)
        self.assertEqual(report["summary"]["legal_rate"], 1.0)
        self.assertEqual(report["summary"]["preferred_rate"], 1.0)
        self.assertEqual(report["summary"]["invalid_output_rate"], 0.0)
        self.assertEqual(report["summary"]["error_rate"], 0.0)
        self.assertEqual(report["summary"]["input_tokens"], 20)
        self.assertEqual(report["summary"]["output_tokens"], 4)
        self.assertAlmostEqual(report["summary"]["cost_usd"], 0.02)
        self.assertEqual(set(report["categories"]), {"interaction", "sequencing"})
        self.assertEqual(report["cases"][0]["pilot_metadata"], {"provider": "synthetic"})

    def test_pilot_failure_is_recorded_without_policy_substitution(self):
        case = synthetic_case("case-failure", "resilience")

        def fail(_: BenchmarkInput) -> str:
            raise RuntimeError("model unavailable")

        report = run_benchmark([case], CallablePilot("failing-pilot", "1", fail))
        row = report["cases"][0]

        self.assertEqual(row["selected_action_id"], "")
        self.assertFalse(row["legal"])
        self.assertTrue(row["invalid_output"])
        self.assertIn("RuntimeError: model unavailable", row["error"])
        self.assertEqual(report["summary"]["error_rate"], 1.0)
        self.assertEqual(report["summary"]["legal_rate"], 0.0)

    def test_baseline_helper_stays_within_fixture_legal_actions(self):
        case = synthetic_case("case-helper", "baseline")

        first = run_benchmark([case], first_legal_pilot())

        self.assertEqual(first["cases"][0]["selected_action_id"], "pass")
        self.assertTrue(first["cases"][0]["legal"])

    def test_benchmark_input_excludes_reference_target_and_provenance(self):
        case = synthetic_case("case-isolation", "threat-assessment")
        captured = []

        def inspect(benchmark_input: BenchmarkInput) -> str:
            captured.append(benchmark_input)
            return "cast"

        report = run_benchmark([case], CallablePilot("inspector", "1", inspect))

        self.assertTrue(report["cases"][0]["preferred"])
        self.assertEqual(len(captured), 1)
        benchmark_input = captured[0]
        self.assertEqual(
            set(benchmark_input.__dataclass_fields__),
            {"decision_type", "seat", "observation_schema", "observation", "legal_actions"},
        )
        for leaked_name in (
            "case_id",
            "category",
            "tags",
            "judgment",
            "game_id",
            "decision_id",
            "chosen_action_id",
            "pilot",
            "deck_id",
            "deck_version",
            "primer_version",
            "outcome",
            "metadata",
        ):
            self.assertFalse(hasattr(benchmark_input, leaked_name), leaked_name)

    def test_benchmark_input_is_detached_from_held_out_case(self):
        case = synthetic_case("case-copy", "resilience")
        benchmark_input = benchmark_input_for_case(case)

        benchmark_input.observation["public"]["turn"] = 999
        benchmark_input.legal_actions[0].payload["kind"] = "mutated"

        self.assertNotEqual(case.decision.observation["public"]["turn"], 999)
        self.assertEqual(case.decision.legal_actions[0].payload["kind"], "pass")

    def test_malformed_pilot_result_fails_closed(self):
        case = synthetic_case("case-malformed", "resilience")
        pilot = CallablePilot(
            "malformed-pilot",
            "1",
            lambda _: PilotDecision(action_id="cast", input_tokens=-1),
        )

        report = run_benchmark([case], pilot)
        row = report["cases"][0]

        self.assertFalse(row["legal"])
        self.assertTrue(row["invalid_output"])
        self.assertIn("input_tokens", row["error"])


if __name__ == "__main__":
    unittest.main()
