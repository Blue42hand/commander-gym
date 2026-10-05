"""Offline budget/resume gates for the private saved-position probe."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from contextlib import redirect_stdout
from io import StringIO
import multiprocessing
import json
import unittest

from commander_gym.cache_probe_session import (
    ProbeSession, ProbeSessionError, TRIALS, INCREMENTAL_USD,
)
from commander_gym.openai_run_budget import OpenAIRunBudget
from scripts.run_saved_cache_probe import _request, execute_trials


def sample_requests():
    return [{"model": "gpt-6-luna", "input": f"JSON trial {n}",
             "max_output_tokens": 2048, "store": False}
            for n in range(len(TRIALS))]


def response():
    return SimpleNamespace(usage={
        "input_tokens": 100, "output_tokens": 10,
        "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
    })


def ledger(path):
    budget = OpenAIRunBudget(
        path, 5, authorized_max_usd=18, max_requests=2212,
        initialize_new_ledger=True,
    )
    budget.snapshot()
    budget.increase_cap(18)
    return OpenAIRunBudget(path, 18, authorized_max_usd=18, max_requests=2212)


def dispatch(session, budget, trial_index, *, retry=False, valid=True):
    request = sample_requests()[trial_index]
    if retry:
        request = {**request, "input": request["input"] + " correction"}
    session.verify(budget.snapshot())
    ordinal, reserved = session.begin(trial_index, request, retry=retry)
    returned = session.guarded_budget(budget.path).create(lambda **_request: response(), request)
    session.finish(ordinal, usage=returned.usage, reserved=reserved,
                   valid=valid, result_kind="provider_response")
    session.verify(budget.snapshot())


def try_second_runner(run_dir, ledger_path, outcome):
    try:
        budget = OpenAIRunBudget(ledger_path, 18, authorized_max_usd=18,
                                 max_requests=2212)
        with ProbeSession.open(run_dir, budget, trace_sha256="a" * 64,
                               source_head="head-a", requests=sample_requests()):
            outcome.send("acquired")
    except ProbeSessionError as error:
        outcome.send(str(error))
    finally:
        outcome.close()


class CacheProbeSessionTests(unittest.TestCase):
    def test_second_process_cannot_open_held_session(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            budget = ledger(root / "ledger.json")
            with ProbeSession.open(root / "run", budget,
                    trace_sha256="a" * 64, source_head="head-a",
                    requests=sample_requests()):
                receiver, sender = multiprocessing.get_context("spawn").Pipe()
                contender = multiprocessing.get_context("spawn").Process(
                    target=try_second_runner,
                    args=(root / "run", root / "ledger.json", sender),
                )
                contender.start()
                sender.close()
                self.assertEqual(receiver.recv(), "another process owns the probe session")
                contender.join(timeout=5)
                self.assertEqual(contender.exitcode, 0)
                self.assertEqual(budget.snapshot()["requests"], 0)

    def test_retry_budget_error_stops_resumed_session(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            budget = ledger(root / "ledger.json")
            session = ProbeSession.open(root / "run", budget,
                trace_sha256="a" * 64, source_head="head-a", requests=sample_requests())
            dispatch(session, budget, 0, valid=False)
            retry_request = {**sample_requests()[0], "input": "JSON trial 0 correction"}
            ordinal, reserved = session.begin(0, retry_request, retry=True)
            missing_details = SimpleNamespace(usage={
                "input_tokens": 100, "output_tokens": 10,
                "input_tokens_details": {},
            }, id="retry-response", output_text="invalid")
            from commander_gym.openai_run_budget import OpenAIRunBudgetError
            with self.assertRaises(OpenAIRunBudgetError) as caught:
                session.guarded_budget(budget.path).create(
                    lambda **_request: missing_details, retry_request)
            self.assertTrue(caught.exception.dispatched)
            session.record_response(ordinal, retry_request, missing_details,
                                    result_kind="budget_error")
            session.finish(ordinal, usage=missing_details.usage, reserved=reserved,
                           valid=False, result_kind="budget_error")
            session.verify(budget.snapshot())
            session.close()
            with ProbeSession.open(root / "run", budget,
                    trace_sha256="a" * 64, source_head="head-a",
                    requests=sample_requests()) as resumed:
                with self.assertRaisesRegex(ProbeSessionError, "earlier provider error"):
                    execute_trials(resumed, resumed.guarded_budget(budget.path), None, [])
            receipt = json.loads((root / "run" / "attempt-01.json").read_text())
            self.assertEqual(receipt["response"]["id"], "retry-response")

    def test_final_retry_transport_error_never_reports_completed(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            budget = ledger(root / "ledger.json")
            session = ProbeSession.open(root / "run", budget,
                trace_sha256="a" * 64, source_head="head-a", requests=sample_requests())
            for trial_index in range(len(TRIALS)):
                dispatch(session, budget, trial_index, valid=trial_index != 9)
            retry_request = {**sample_requests()[9], "input": "JSON trial 9 correction"}
            ordinal, reserved = session.begin(9, retry_request, retry=True)
            def disconnected(**_request):
                raise RuntimeError("simulated ambiguous transport")
            with self.assertRaisesRegex(RuntimeError, "ambiguous transport"):
                session.guarded_budget(budget.path).create(disconnected, retry_request)
            session.finish(ordinal, usage=None, reserved=reserved,
                           valid=False, result_kind="transport_error")
            session.verify(budget.snapshot())
            session.close()
            with ProbeSession.open(root / "run", budget,
                    trace_sha256="a" * 64, source_head="head-a",
                    requests=sample_requests()) as resumed:
                with self.assertRaisesRegex(ProbeSessionError, "earlier provider error"):
                    execute_trials(resumed, resumed.guarded_budget(budget.path), None, [])

    def test_runner_resumes_and_counts_warmups_and_validation_retry(self):
        observation = {
            "type": "Game", "perspectivePlayerId": "p1", "agentToAct": "p1",
            "pendingDecision": None, "terminated": False,
            "state": {"gameLog": [{"description": "masked"}]},
            "legalActions": [{"actionId": 2, "semanticId": "pass",
                              "kind": "PassPriority", "affordable": True}],
        }
        rows = [{"observation": observation, "request": {
            "model": "gpt-6-luna", "instructions": "Return JSON",
            "input": "Return one JSON object for this observation:\n" + json.dumps(observation),
            "text": {"format": {"type": "json_object"}},
            "max_output_tokens": 2048, "store": False,
        }} for _ in range(47)]
        requests = [_request(rows[position], variant) for position, variant in TRIALS]

        class FakeResponses:
            def __init__(self):
                self.calls = []

            def create(self, **request):
                self.calls.append(request)
                output = "{}" if len(self.calls) == 2 else json.dumps({
                    "channel": "action", "semanticId": "pass", "params": {},
                })
                return SimpleNamespace(
                    id=f"response-{len(self.calls)}", model="gpt-6-luna",
                    output_text=output, status="completed", error=None,
                    incomplete_details=None, usage=response().usage,
                )

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            budget = ledger(root / "ledger.json")
            session = ProbeSession.open(root / "run", budget,
                trace_sha256="a" * 64, source_head="head-a", requests=requests)
            client = SimpleNamespace(responses=FakeResponses())
            with redirect_stdout(StringIO()):
                execute_trials(session, session.guarded_budget(budget.path), client, rows)
                session.close()
                # A second process resumes the same frozen session without a request.
                resumed = ProbeSession.open(root / "run", budget,
                    trace_sha256="a" * 64, source_head="head-a", requests=requests)
                execute_trials(resumed, resumed.guarded_budget(budget.path), client, rows)
            intents = [json.loads(line) for line in (root / "run" / "intents.jsonl").read_text().splitlines()]
            self.assertEqual(len(client.responses.calls), 11)
            self.assertEqual(budget.snapshot()["requests"], 11)
            self.assertEqual(sum(item["retry"] for item in intents), 1)
            self.assertEqual(sum(not item["retry"] for item in intents), 10)
            self.assertEqual((root / "run" / "attempt-00.json").stat().st_mode & 0o777, 0o600)
            self.assertIn("output_text", json.loads(
                (root / "run" / "attempt-00.json").read_text())["response"])
            self.assertEqual([(item["trialIndex"], item["retry"]) for item in intents[3:7]],
                             [(2, False), (3, False), (4, False), (5, False)])

    def test_freezes_start_and_reuses_absolute_limits_after_resume(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            budget = ledger(root / "ledger.json")
            session = ProbeSession.open(
                root / "run", budget, trace_sha256="a" * 64,
                source_head="head-a", requests=sample_requests(),
            )
            original = dict(session.manifest)
            self.assertLess(original["worstTotalReservationUsd"], INCREMENTAL_USD)
            self.assertEqual(original["sessionMaxRequests"], 12)
            self.assertEqual(original["sessionCapUsd"], 0.5)
            dispatch(session, budget, 0)
            session.close()
            resumed = ProbeSession.open(
                root / "run", budget, trace_sha256="a" * 64,
                source_head="head-a", requests=sample_requests(),
            )
            self.assertEqual(resumed.manifest, original)
            self.assertEqual(resumed.guarded_budget(budget.path).session_cap_usd, 0.5)
            resumed.close()
            with self.assertRaisesRegex(ProbeSessionError, "source, or payload"):
                ProbeSession.open(root / "run", budget, trace_sha256="b" * 64,
                                  source_head="head-a", requests=sample_requests())
            manifest_path = root / "run" / "manifest.json"
            changed = json.loads(manifest_path.read_text())
            changed["sessionCapUsd"] = 0.75
            manifest_path.write_text(json.dumps(changed))
            with self.assertRaisesRegex(ProbeSessionError, "frozen session ceilings"):
                ProbeSession.open(root / "run", budget, trace_sha256="a" * 64,
                                  source_head="head-a", requests=sample_requests())

    def test_unresolved_intent_blocks_resume_without_duplicate_dispatch(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            budget = ledger(root / "ledger.json")
            session = ProbeSession.open(root / "run", budget,
                trace_sha256="a" * 64, source_head="head-a", requests=sample_requests())
            session.begin(0, sample_requests()[0])
            session.close()
            with self.assertRaisesRegex(ProbeSessionError, "unresolved attempt intent"):
                ProbeSession.open(root / "run", budget,
                    trace_sha256="a" * 64, source_head="head-a", requests=sample_requests())
            self.assertEqual(budget.snapshot()["requests"], 0)

    def test_all_warmups_are_primary_attempts_and_retries_share_ledger(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            budget = ledger(root / "ledger.json")
            session = ProbeSession.open(root / "run", budget,
                trace_sha256="a" * 64, source_head="head-a", requests=sample_requests())
            for trial_index in range(len(TRIALS)):
                dispatch(session, budget, trial_index, valid=trial_index in (0, 1, 2, 4, 5, 6, 7, 9))
                if trial_index in (3, 8):
                    dispatch(session, budget, trial_index, retry=True)
            intents = [json.loads(line) for line in (root / "run" / "intents.jsonl").read_text().splitlines()]
            self.assertEqual(len(intents), 12)
            self.assertEqual(sum(item["retry"] for item in intents), 2)
            self.assertEqual(sum(not item["retry"] for item in intents), 10)
            self.assertEqual(budget.snapshot()["requests"], 12)
            self.assertLess(sum(item["reservationUsd"] for item in intents), INCREMENTAL_USD)
            with self.assertRaisesRegex(ProbeSessionError, "session attempt limit"):
                session.begin(9, sample_requests()[9])

    def test_retry_order_and_historical_counter_drift_fail_closed(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            budget = ledger(root / "ledger.json")
            # Preserve an old ambiguous reservation, as in the real cumulative ledger.
            with self.assertRaises(RuntimeError):
                budget.create(lambda **_request: (_ for _ in ()).throw(RuntimeError("old")),
                              sample_requests()[0])
            session = ProbeSession.open(root / "run", budget,
                trace_sha256="a" * 64, source_head="head-a", requests=sample_requests())
            self.assertEqual(session.manifest["startLedger"]["unsettledRequests"], 1)
            dispatch(session, budget, 0)
            with self.assertRaisesRegex(ProbeSessionError, "retry must immediately"):
                session.begin(0, sample_requests()[0], retry=True)
            with self.assertRaisesRegex(ProbeSessionError, "primary attempt order"):
                session.begin(2, sample_requests()[2])
            # An old reserve settling would decrement unsettled and change totals.
            def old_settles(data):
                data["unsettledRequests"] -= 1
                data["inputTokens"] += 10
                data["estimatedUsd"] -= 0.0001
            budget._transact(old_settles)
            with self.assertRaisesRegex(ProbeSessionError, "counters changed"):
                session.verify(budget.snapshot())

    def test_new_external_writer_counter_mismatch_fails_closed(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            budget = ledger(root / "ledger.json")
            session = ProbeSession.open(root / "run", budget,
                trace_sha256="a" * 64, source_head="head-a", requests=sample_requests())
            budget.create(lambda **_request: response(), sample_requests()[0])
            with self.assertRaisesRegex(ProbeSessionError, "counters changed"):
                session.verify(budget.snapshot())


if __name__ == "__main__":
    unittest.main()
