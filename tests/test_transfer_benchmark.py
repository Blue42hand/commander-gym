import unittest

from commander_gym.benchmark import BenchmarkCase, BenchmarkJudgment
from commander_gym.benchmark_runner import BenchmarkInput, CallablePilot, PilotDecision, run_benchmark
from commander_gym.records import ActionRecord, DecisionRecord, PilotProvenance
from commander_gym.transfer_benchmark import (
    TRANSFER_COHORT_ROLES,
    TransferBenchmarkError,
    TransferCohort,
    build_transfer_benchmark_summary,
    capability_tag,
)


def synthetic_case(case_id: str, capability: str, preferred: str = "cast") -> BenchmarkCase:
    return BenchmarkCase(
        case_id=case_id,
        category="priority",
        decision=DecisionRecord(
            game_id="synthetic-game",
            decision_id=f"decision-{case_id}",
            decision_type="priority",
            seat=0,
            observation_schema="synthetic-v1",
            observation={"public": {"case": case_id}},
            legal_actions=[
                ActionRecord(action_id="pass", payload={"kind": "pass"}),
                ActionRecord(action_id="cast", payload={"kind": "cast"}),
            ],
            chosen_action_id=preferred,
            pilot=PilotProvenance(source="test", implementation="fixture", version="1"),
            deck_id="synthetic-deck",
            deck_version="1",
        ),
        judgment=BenchmarkJudgment(preferred_action_ids=[preferred]),
        tags=[capability_tag(capability), "synthetic"],
    )


def report(cases, name: str, chooser, *, escalated: bool = False):
    def choose(value: BenchmarkInput):
        return PilotDecision(
            action_id=chooser(value),
            escalated=escalated,
            escalation_reason="teacher" if escalated else None,
        )

    return run_benchmark(cases, CallablePilot(name, "1", choose))


class TransferBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.cases = [
            synthetic_case("seq", "sequencing", "cast"),
            synthetic_case("threat", "threat_assessment", "pass"),
        ]
        self.reports = {
            "baseline": report(self.cases, "baseline", lambda _: "pass"),
            "one_v_one_enriched": report(self.cases, "one-v-one", lambda _: "cast"),
            "commander_adapted": report(
                self.cases,
                "commander",
                lambda value: "cast"
                if value.observation["public"]["case"] == "seq"
                else "pass",
            ),
            "frontier_reference": report(
                self.cases,
                "frontier",
                lambda value: "cast"
                if value.observation["public"]["case"] == "seq"
                else "pass",
                escalated=True,
            ),
        }

    def cohorts(self):
        return [
            TransferCohort(
                role=role,
                report=self.reports[role],
                provenance_artifact_ids=(f"artifact:{role}",),
            )
            for role in TRANSFER_COHORT_ROLES
        ]

    def test_builds_capability_and_disagreement_report(self):
        summary = build_transfer_benchmark_summary(
            self.cases,
            self.cohorts(),
            source_group_by_case={"seq": "game-a", "threat": "game-b"},
        )

        self.assertFalse(summary["notes"]["frontier_reference_is_ground_truth"])
        self.assertEqual(summary["source_groups"]["group_count"], 2)
        self.assertEqual(
            summary["capabilities"]["sequencing"]["cohorts"]["commander_adapted"][
                "preferred_rate"
            ],
            1.0,
        )
        self.assertEqual(
            summary["cohorts"]["frontier_reference"]["metrics"]["escalation_rate"],
            1.0,
        )
        self.assertEqual(summary["disagreement"]["any_disagreement_rate"], 1.0)
        self.assertEqual(
            summary["cohorts"]["commander_adapted"]["metrics"][
                "preferred_delta_vs_baseline"
            ],
            0.5,
        )

    def test_requires_exact_four_canonical_cohorts(self):
        with self.assertRaisesRegex(TransferBenchmarkError, "canonical cohorts"):
            build_transfer_benchmark_summary(
                self.cases,
                self.cohorts()[:-1],
                source_group_by_case={"seq": "game-a", "threat": "game-b"},
            )

    def test_requires_capability_tags(self):
        broken = BenchmarkCase(
            case_id="untagged",
            category=self.cases[0].category,
            decision=self.cases[0].decision,
            judgment=self.cases[0].judgment,
            tags=["synthetic"],
        )
        broken_reports = {
            role: report([broken], role, lambda _: "cast")
            for role in TRANSFER_COHORT_ROLES
        }
        cohorts = [
            TransferCohort(role, broken_reports[role], (f"artifact:{role}",))
            for role in TRANSFER_COHORT_ROLES
        ]
        with self.assertRaisesRegex(TransferBenchmarkError, "capability tag"):
            build_transfer_benchmark_summary(
                [broken],
                cohorts,
                source_group_by_case={"untagged": "game-a"},
            )

    def test_requires_exact_source_group_membership(self):
        with self.assertRaisesRegex(TransferBenchmarkError, "exactly match"):
            build_transfer_benchmark_summary(
                self.cases,
                self.cohorts(),
                source_group_by_case={"seq": "game-a"},
            )


if __name__ == "__main__":
    unittest.main()
