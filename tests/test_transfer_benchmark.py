import hashlib
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
    transfer_capability_identity,
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

    def capability_sidecar(self):
        return {
            "seq": ("sequencing",),
            "threat": ("threat_assessment",),
        }

    def cohorts(self):
        return [
            TransferCohort(
                role=role,
                report=self.reports[role],
                provenance_artifact_ids=(f"sha256:{hashlib.sha256(role.encode()).hexdigest()}",),
            )
            for role in TRANSFER_COHORT_ROLES
        ]

    def test_builds_capability_and_disagreement_report(self):
        summary = build_transfer_benchmark_summary(
            self.cases,
            self.cohorts(),
            leakage_group_by_case={"seq": ("source-a", "game-a"), "threat": ("source-b", "game-b")},
            capability_by_case=self.capability_sidecar(),
        )

        self.assertFalse(summary["notes"]["frontier_reference_is_ground_truth"])
        self.assertEqual(summary["leakage_groups"]["group_count"], 2)
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
                leakage_group_by_case={"seq": ("source-a", "game-a"), "threat": ("source-b", "game-b")},
                capability_by_case=self.capability_sidecar(),
            )

    def test_requires_exact_capability_sidecar_membership(self):
        with self.assertRaisesRegex(TransferBenchmarkError, "capability-sidecar membership"):
            build_transfer_benchmark_summary(
                self.cases,
                self.cohorts(),
                leakage_group_by_case={
                    "seq": ("source-a", "game-a"),
                    "threat": ("source-b", "game-b"),
                },
                capability_by_case={"seq": ("sequencing",)},
            )

    def test_capability_sidecar_fingerprint_changes_on_reclassification(self):
        first = transfer_capability_identity(self.capability_sidecar())
        changed = transfer_capability_identity(
            {"seq": ("sequencing", "resource_use"), "threat": ("threat_assessment",)}
        )
        self.assertNotEqual(first["fingerprint"], changed["fingerprint"])
        self.assertFalse(first["qualification_ready"])
        self.assertIn("resource_use", first["missing_capabilities"])

    def test_rejects_noncanonical_provenance_artifact_ids(self):
        bad = list(self.cohorts())
        bad[0] = TransferCohort(
            role="baseline",
            report=self.reports["baseline"],
            provenance_artifact_ids=("artifact:not-canonical",),
        )
        with self.assertRaisesRegex(TransferBenchmarkError, "canonical sha256"):
            build_transfer_benchmark_summary(
                self.cases,
                bad,
                leakage_group_by_case={
                    "seq": ("source-a", "game-a"),
                    "threat": ("source-b", "game-b"),
                },
                capability_by_case=self.capability_sidecar(),
            )

    def test_requires_exact_leakage_group_membership(self):
        with self.assertRaisesRegex(TransferBenchmarkError, "exactly match"):
            build_transfer_benchmark_summary(
                self.cases,
                self.cohorts(),
                leakage_group_by_case={"seq": ("source-a", "game-a")},
                capability_by_case=self.capability_sidecar(),
            )


if __name__ == "__main__":
    unittest.main()
