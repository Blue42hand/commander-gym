import json
import tempfile
import unittest
from pathlib import Path

from commander_gym.experiment import (
    ExperimentError,
    ExperimentPlan,
    ExperimentRunSpec,
    run_experiment,
    write_experiment_artifact,
)
from commander_gym.full_game import FullGameResult, QUALIFICATION_VALID_COMPLETE
from commander_gym.run_records import EngineProvenance, RunRecord, RunTermination


class ExperimentTests(unittest.TestCase):
    def plan(self):
        return ExperimentPlan(
            experiment_id="synthetic-comparison-v1",
            runs=(
                ExperimentRunSpec("run-a", seed=11),
                ExperimentRunSpec("run-b", seed=22),
            ),
        )

    def result(self, run_id, seed):
        return FullGameResult(
            run=RunRecord(
                run_id=run_id,
                started_at="2026-09-20T20:00:00Z",
                finished_at="2026-09-20T20:00:01Z",
                engine=EngineProvenance(
                    implementation="argentum-gym-server",
                    version="fixture",
                    schema="argentum-gym-contract@fixture",
                ),
                termination=RunTermination(status="completed"),
                seed=seed,
                metadata={"fixture": True},
            ),
            decisions=(),
            qualification=QUALIFICATION_VALID_COMPLETE,
        )

    def test_plan_fingerprint_is_stable_and_order_sensitive(self):
        plan = self.plan()
        self.assertEqual(plan.fingerprint(), self.plan().fingerprint())
        reversed_plan = ExperimentPlan(
            experiment_id=plan.experiment_id,
            runs=tuple(reversed(plan.runs)),
        )
        self.assertNotEqual(plan.fingerprint(), reversed_plan.fingerprint())

    def test_rejects_duplicate_run_ids(self):
        plan = ExperimentPlan(
            experiment_id="duplicate",
            runs=(ExperimentRunSpec("same", 1), ExperimentRunSpec("same", 2)),
        )
        with self.assertRaises(ExperimentError):
            plan.validate()

    def test_runs_in_declared_order_and_annotates_provenance(self):
        calls = []

        def runner(orchestrator, config, seats, **kwargs):
            calls.append((kwargs["run_id"], kwargs["seed"]))
            return self.result(kwargs["run_id"], kwargs["seed"])

        plan = self.plan()
        result = run_experiment(None, {"format": "fixture"}, (), plan, runner=runner)

        self.assertEqual(calls, [("run-a", 11), ("run-b", 22)])
        self.assertEqual([item.run.experiment_id for item in result.runs], [plan.experiment_id] * 2)
        for index, item in enumerate(result.runs):
            self.assertEqual(item.run.metadata["fixture"], True)
            self.assertEqual(item.run.metadata["experiment"]["run_index"], index)
            self.assertEqual(
                item.run.metadata["experiment"]["plan_fingerprint"],
                plan.fingerprint(),
            )
        self.assertNotIn("config", result.to_dict()["plan"])

    def test_fails_closed_on_mislabeled_runner_evidence(self):
        def wrong_runner(orchestrator, config, seats, **kwargs):
            return self.result("wrong-run", kwargs["seed"])

        with self.assertRaises(ExperimentError):
            run_experiment(None, {}, (), self.plan(), runner=wrong_runner)

    def test_writes_deterministic_aggregate_artifact(self):
        def runner(orchestrator, config, seats, **kwargs):
            return self.result(kwargs["run_id"], kwargs["seed"])

        result = run_experiment(None, {}, (), self.plan(), runner=runner)
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.json"
            second = Path(directory) / "second.json"
            write_experiment_artifact(first, result)
            write_experiment_artifact(second, result)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            payload = json.loads(first.read_text(encoding="utf-8"))
            self.assertEqual(payload["plan_fingerprint"], self.plan().fingerprint())
            self.assertEqual(len(payload["runs"]), 2)


if __name__ == "__main__":
    unittest.main()
