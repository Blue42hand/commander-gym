from pathlib import Path
from argparse import Namespace
from contextlib import redirect_stdout
import hashlib
from http.client import IncompleteRead
import io
import json
import os
import stat
import tempfile
import unittest
from unittest.mock import ANY, patch

from commander_gym.openai_run_budget import OpenAIRunBudget, OpenAIRunBudgetError
from commander_gym.two_luna_debug import (
    _FatalActionWatch, _ProgressGuard, _capture_terminal_replay, _natural_terminal_game,
    _provenance_size, _request_json, _run_budget, _summarize, run,
    _terminal_artifact_result, _write_private_json,
)


class TwoLunaDebugReportTests(unittest.TestCase):
    def test_payment_correction_counts_are_scoped_to_active_game(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "server.log"
            log.write_text(
                "WARN External AI payment rejected for seat ai-a in game game-1; same pilot is correcting\n"
                "WARN External AI payment rejected for seat ai-b in game other; same pilot is correcting\n"
            )
            summary = _summarize(
                [{"callback": "chooseAction", "playerId": "ai-a",
                  "observation": {"nativePaymentError": "native rejection"},
                  "metadata": {"provider": "openai", "usage": {}}}],
                completed=False, lobby_id="lobby", game_ids=["game-1"],
                max_turn=3, log_path=log, wall_time_seconds=1,
            )
            self.assertEqual(summary["nativeInvalidPaymentAttempts"], 1)
            self.assertEqual(summary["paymentCorrectionCallbacks"], 1)
            self.assertEqual(summary["paymentCorrectionFatalRejections"], 0)
            with log.open("a") as output:
                output.write(
                    "ERROR External AI action failed for seat ai-a in game game-1: "
                    "Selected mana sources cannot pay this spell's cost "
                    "— refusing server-side strategic fallback\n"
                )
            failed = _summarize(
                [], completed=False, lobby_id="lobby", game_ids=["game-1"],
                max_turn=3, log_path=log, wall_time_seconds=1,
            )
            self.assertEqual(failed["paymentCorrectionFatalRejections"], 1)

    def test_fatal_action_watch_matches_active_game_only_after_complete_log_line(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "server.log"
            watch = _FatalActionWatch(log)
            log.write_text(
                "ERROR External AI action failed for seat ai-x in game other: "
                "illegal block — refusing server-side strategic fallback\n"
                "ERROR External AI action failed for seat ai-a in game game-1: "
                "Goblin Piledriver has protection from blue"
            )
            self.assertIsNone(watch.scan(["game-1"]))
            with log.open("a") as output:
                output.write(" — refusing server-side strategic fallback\n")
            self.assertEqual(
                watch.scan(["game-1"]),
                "Goblin Piledriver has protection from blue",
            )
            self.assertIsNone(watch.scan(["unrelated-game"]))

    def test_run_stops_on_fatal_external_action_without_waiting_for_stall(self):
        profiles = {"profiles": [
            {"id": "a", "deck": {"commander": "Krenko", "cards": {"Mountain": 99}}},
            {"id": "b", "deck": {"commander": "Talrand", "cards": {"Island": 99}}},
        ]}
        status = {"complete": False, "state": "TOURNAMENT_ACTIVE", "round": 1,
                  "liveGames": [{"gameSessionId": "game-1", "turnNumber": 10}],
                  "completedGames": []}
        snapshot = {"capUsd": 18, "estimatedUsd": 7.5, "requests": 975,
                    "inputTokens": 1680321, "outputTokens": 22672,
                    "unsettledRequests": 3}
        budget = type("Budget", (), {"snapshot": lambda self: snapshot})()
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "server.log"
            log.write_text(
                "ERROR External AI action failed for seat ai-a in game game-1: "
                "illegal block — refusing server-side strategic fallback\n"
            )
            args = Namespace(
                profile_a="a", profile_b="b", sidecar_url="http://127.0.0.1:1235",
                server_url="http://127.0.0.1:1234", timeout=3600, stall_seconds=600,
                poll_seconds=1, provenance=None, server_log=str(log),
                terminal_evidence_dir=directory,
            )
            output = io.StringIO()
            with patch.dict(os.environ, {"COMMANDER_GYM_SIDECAR_TOKEN": "local-test"}), \
                    patch("commander_gym.two_luna_debug._request_json",
                          side_effect=[profiles, {"lobbyId": "lobby"}, status]) as request, \
                    patch("commander_gym.two_luna_debug._run_budget", return_value=budget), \
                    patch("commander_gym.two_luna_debug.time.sleep"), redirect_stdout(output):
                exit_code = run(args)
            result = json.loads(next(s.partition("=")[2] for s in output.getvalue().splitlines()
                                     if s.startswith("TWO_LUNA_DEBUG_RESULT=")))
            self.assertEqual(exit_code, 1)
            self.assertEqual(request.call_count, 3)
            self.assertEqual(result["stopReason"], "external_ai_action_rejected")
            self.assertEqual(result["fatalActionFailure"], "illegal block")
            self.assertIsNone(result["terminalEvidence"])

    def test_captures_replay_and_native_terminal_frame_before_server_cleanup(self):
        replay = {"metadata": {"gameId": "game-1", "snapshotCount": 3,
                               "stateReproducible": True},
                  "initialSnapshot": {}, "deltas": [{}, {}]}
        state = {"gameOver": True, "winnerId": "ai-a", "turnNumber": 13}
        # The pre-fix status could omit nativeGameOver yet still identify the
        # completed match. Replay capture must remain independent of that DTO.
        finished = [{"gameSessionId": "game-1", "winnerId": "ai-a",
                     "nativeGameOver": False, "finalTurnNumber": None}]
        with tempfile.TemporaryDirectory() as directory:
            with patch("commander_gym.two_luna_debug._request_json",
                       side_effect=[replay, state]) as request:
                receipt = _capture_terminal_replay("http://127.0.0.1:1234", finished, Path(directory))
            request.assert_any_call("http://127.0.0.1:1234/api/public/replays/game-1", timeout=ANY)
            request.assert_any_call(
                "http://127.0.0.1:1234/api/public/replays/game-1/frames/2/full-state",
                timeout=ANY,
            )
            self.assertEqual(receipt["finalTurnNumber"], 13)
            for name, expected_hash in (("terminal-replay.json", receipt["replaySha256"]),
                                        ("terminal-state.json", receipt["terminalStateSha256"])):
                path = Path(directory) / name
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected_hash)
            self.assertEqual(json.loads((Path(directory) / "terminal-state.json").read_text()), state)

    def test_capture_rejects_a_nonterminal_final_frame(self):
        replay = {"metadata": {"gameId": "game-1", "snapshotCount": 1}}
        with tempfile.TemporaryDirectory() as directory:
            def partial(url, **_):
                return replay if url.endswith("/game-1") else {"gameOver": False, "turnNumber": 13}
            with patch("commander_gym.two_luna_debug._request_json", side_effect=partial):
                with self.assertRaisesRegex(RuntimeError, "native terminal replay unavailable"):
                    _capture_terminal_replay(
                        "http://127.0.0.1:1234",
                        [{"gameSessionId": "game-1", "winnerId": "ai-a"}], Path(directory),
                        timeout_seconds=0.005, poll_seconds=0.001,
                    )
            self.assertFalse((Path(directory) / "terminal-replay.json").exists())
            self.assertFalse((Path(directory) / "terminal-state.json").exists())

    def test_capture_waits_for_replay_save_after_completion_and_skips_partial_checkpoint(self):
        partial = {"metadata": {"gameId": "game-1", "snapshotCount": 2}}
        terminal = {"metadata": {"gameId": "game-1", "snapshotCount": 3}}
        responses = [RuntimeError("HTTP 404: replay not yet saved"), partial,
                     {"gameOver": False, "turnNumber": 12}, terminal,
                     {"gameOver": True, "winnerId": "ai-a", "turnNumber": 13}]
        with tempfile.TemporaryDirectory() as directory:
            with patch("commander_gym.two_luna_debug._request_json", side_effect=responses), \
                    patch("commander_gym.two_luna_debug.time.sleep"):
                receipt = _capture_terminal_replay(
                    "http://127.0.0.1:1234",
                    [{"gameSessionId": "game-1", "winnerId": "ai-a"}], Path(directory),
                    timeout_seconds=2,
                )
            self.assertEqual(receipt["snapshotCount"], 3)
            self.assertEqual(receipt["finalTurnNumber"], 13)
            self.assertEqual(json.loads((Path(directory) / "terminal-replay.json").read_text()), terminal)

    def test_truncated_http_body_becomes_diagnostic_and_capture_error(self):
        with patch("commander_gym.two_luna_debug.urlopen",
                   side_effect=IncompleteRead(b'{"metadata":', 5)):
            with self.assertRaisesRegex(RuntimeError, "cannot reach"):
                _request_json("http://127.0.0.1:1234/api/public/replays/game-1")
        with tempfile.TemporaryDirectory() as directory:
            with patch("commander_gym.two_luna_debug._capture_terminal_replay",
                       side_effect=IncompleteRead(b'{"metadata":', 5)):
                result = _terminal_artifact_result(
                    "http://127.0.0.1:1234", [{"gameSessionId": "game-1"}], Path(directory),
                )
            self.assertIn("IncompleteRead", result["error"])

    def test_failed_private_write_leaves_no_final_or_temporary_name(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "terminal-state.json"
            for operation in ("fsync", "link"):
                with self.subTest(operation=operation):
                    with patch(f"commander_gym.two_luna_debug.os.{operation}",
                               side_effect=OSError("disk full")):
                        with self.assertRaisesRegex(OSError, "disk full"):
                            _write_private_json(target, {"gameOver": True})
                    self.assertFalse(target.exists())
                    self.assertEqual(list(Path(directory).iterdir()), [])

    def test_failed_state_write_keeps_a_complete_replay_and_reports_error(self):
        replay = {"metadata": {"gameId": "game-1", "snapshotCount": 1}}
        final = {"gameOver": True, "winnerId": "ai-a", "turnNumber": 13}
        real_write = _write_private_json
        with tempfile.TemporaryDirectory() as directory:
            def write(path, value):
                if path.name == "terminal-state.json":
                    raise OSError("disk full")
                return real_write(path, value)
            with patch("commander_gym.two_luna_debug._request_json", side_effect=[replay, final]), \
                    patch("commander_gym.two_luna_debug._write_private_json", side_effect=write):
                result = _terminal_artifact_result(
                    "http://127.0.0.1:1234",
                    [{"gameSessionId": "game-1", "winnerId": "ai-a"}], Path(directory),
                )
            self.assertIn("disk full", result["error"])
            self.assertEqual(json.loads((Path(directory) / "terminal-replay.json").read_text()), replay)
            self.assertFalse((Path(directory) / "terminal-state.json").exists())

    def test_completed_run_emits_result_when_terminal_capture_transport_fails(self):
        profiles = {"profiles": [
            {"id": "a", "deck": {"commander": "Krenko", "cards": {"Mountain": 99}}},
            {"id": "b", "deck": {"commander": "Talrand", "cards": {"Island": 99}}},
        ]}
        status = {"complete": True, "state": "TOURNAMENT_COMPLETE", "round": 1,
                  "liveGames": [], "completedGames": [{
                      "gameSessionId": "game-1", "winnerId": "ai-a", "draw": False,
                      "simulated": False, "nativeGameOver": True, "finalTurnNumber": 13,
                  }]}
        budget_snapshot = {"capUsd": 8, "estimatedUsd": 1, "requests": 5,
                           "inputTokens": 20, "outputTokens": 3, "unsettledRequests": 0}
        budget = type("Budget", (), {"snapshot": lambda self: budget_snapshot})()
        with tempfile.TemporaryDirectory() as directory:
            args = Namespace(
                profile_a="a", profile_b="b", sidecar_url="http://127.0.0.1:1235",
                server_url="http://127.0.0.1:1234", timeout=10, stall_seconds=10,
                poll_seconds=0.001, provenance=None, server_log=None,
                terminal_evidence_dir=directory,
            )
            output = io.StringIO()
            with patch.dict(os.environ, {"COMMANDER_GYM_SIDECAR_TOKEN": "local-test"}), \
                    patch("commander_gym.two_luna_debug._request_json",
                          side_effect=[profiles, {"lobbyId": "lobby"}, status]), \
                    patch("commander_gym.two_luna_debug._run_budget", return_value=budget), \
                    patch("commander_gym.two_luna_debug._capture_terminal_replay",
                          side_effect=IncompleteRead(b'{"metadata":', 5)), \
                    patch("commander_gym.two_luna_debug.time.sleep"), redirect_stdout(output):
                exit_code = run(args)
        result_line = next(s for s in output.getvalue().splitlines()
                           if s.startswith("TWO_LUNA_DEBUG_RESULT="))
        result = json.loads(result_line.partition("=")[2])
        self.assertEqual(exit_code, 1)  # no policy provenance in this synthetic run
        self.assertIn("IncompleteRead", result["terminalArtifact"]["error"])
        self.assertEqual(result["terminalEvidence"]["winnerId"], "ai-a")

    def test_existing_elevated_cumulative_budget_is_read_without_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            original = OpenAIRunBudget(path, 5, authorized_max_usd=8,
                                       max_requests=610, initialize_new_ledger=True)
            original.snapshot()
            original.set_request_limit(906)
            original.increase_cap(8)
            args = Namespace(budget_ledger=str(path), budget_cap=8,
                             budget_authorized_max=8, budget_max_requests=906)
            self.assertEqual(_run_budget(args).snapshot()["maxRequests"], 906)
            self.assertEqual(_run_budget(args).snapshot()["requests"], 0)
            args.budget_max_requests = 907
            with self.assertRaises(OpenAIRunBudgetError):
                _run_budget(args).snapshot()

    def test_progress_guard_counts_callbacks_and_api_reservations_but_expires_old_inflight(self):
        guard = _ProgressGuard(stall_seconds=300, last_change=0)
        # The third field is the cumulative reservation count. A request that
        # takes two minutes is activity even before provenance is written.
        self.assertFalse(guard.observe(("turn-4", 100, 10), 0))
        self.assertFalse(guard.observe(("turn-4", 100, 10), 250))
        self.assertFalse(guard.observe(("turn-4", 100, 11), 251))
        self.assertFalse(guard.observe(("turn-4", 150, 11), 500))
        self.assertTrue(guard.observe(("turn-4", 150, 11), 800))
        # Unsettled counts are deliberately absent: three pre-existing
        # reservations cannot keep a stalled game alive indefinitely.

    def test_provenance_size_reports_missing_and_written_callbacks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.jsonl"
            self.assertEqual(_provenance_size(path), 0)
            path.write_text("{}\n")
            self.assertEqual(_provenance_size(path), 3)
            self.assertIsNone(_provenance_size(None))

    def test_requires_one_played_native_terminal_match(self):
        played = {
            "gameSessionId": "game-1",
            "winnerId": "ai-a",
            "draw": False,
            "simulated": False,
            "nativeGameOver": True,
            "finalTurnNumber": 8,
        }
        self.assertEqual(_natural_terminal_game([played]), played)
        self.assertIsNone(_natural_terminal_game([]))
        self.assertIsNone(_natural_terminal_game([played, played]))
        for change in (
            {"gameSessionId": None},
            {"nativeGameOver": False},
            {"simulated": True},
            {"winnerId": None},
            {"finalTurnNumber": None},
            {"draw": None},
            {"simulated": None},
        ):
            with self.subTest(change=change):
                self.assertIsNone(_natural_terminal_game([{**played, **change}]))
        self.assertIsNotNone(_natural_terminal_game([{**played, "winnerId": None, "draw": True}]))
        self.assertIsNone(_natural_terminal_game([{**played, "simulated": None, "isSimulated": False}]))

    def test_reports_mechanical_wakes_provider_usage_and_clean_qualification(self):
        records = [
            {
                "callback": "chooseAction",
                "playerId": "ai-a",
                "observation": {
                    "agentToAct": "ai-a",
                    "perspectivePlayerId": "ai-a",
                    "terminated": False,
                    "pendingDecision": None,
                    "knownDeck": {"cards": {"Island": 20}},
                    "legalActions": [],
                },
                "choice": {
                    "channel": "action",
                    "metadata": {
                        "routing": {
                            "path": "strategic",
                            "strategicWakeAvoided": False,
                        },
                        "provider": "openai",
                        "retryCount": 0,
                        "usage": {
                            "input_tokens": 20,
                            "input_tokens_details": {"cached_tokens": 8},
                            "output_tokens": 4,
                        },
                    },
                },
            },
            {
                "callback": "chooseAction",
                "playerId": "ai-b",
                "observation": {
                    "agentToAct": "ai-b",
                    "perspectivePlayerId": "ai-b",
                    "terminated": False,
                    "pendingDecision": {
                        "decisionId": "damage-1",
                        "type": "AssignDamageDecision",
                        "requiresStructuredResponse": True,
                        "defaultAssignments": {"x": 2},
                    },
                    "knownDeck": {"cards": {"Mountain": 20}},
                    "legalActions": [],
                },
                "choice": {
                    "channel": "decision",
                    "metadata": {
                        "routing": {
                            "path": "mechanical",
                            "handler": "certified-native-decision",
                            "strategicWakeAvoided": True,
                        }
                    },
                },
            },
        ]
        records.append({
            "callback": "chooseAction", "playerId": "ai-a",
            "observation": records[0]["observation"],
            "choice": {"channel": "action", "metadata": {
                "routing": {"path": "composed", "handledBy": {
                    "role": "deterministic", "implementation": "delegated-pass",
                }},
                "delegatedPass": {"leaseId": "lease-1", "strategicWakeAvoided": True},
            }},
        })
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "server.log"
            log.write_text("normal game log\n")
            summary = _summarize(
                records,
                completed=True,
                lobby_id="lobby-1",
                game_ids=["game-1"],
                max_turn=8,
                log_path=log,
                wall_time_seconds=12.5,
            )

        self.assertTrue(summary["technicalQualified"])
        self.assertTrue(summary["provenanceComplete"])
        self.assertEqual(summary["policySeats"], ["ai-a", "ai-b"])
        self.assertEqual(summary["providerCalls"], 1)
        self.assertEqual(summary["strategicWakesAvoided"], 2)
        self.assertEqual(summary["delegatedPasses"], 1)
        self.assertEqual(summary["strategicByKind"], {"chooseAction": 1})
        self.assertEqual(summary["mechanicalByHandler"], {
            "certified-native-decision": 1, "delegated-pass": 1,
        })
        self.assertEqual(summary["routingClassificationErrors"], 0)
        self.assertEqual(summary["inputTokens"], 20)
        self.assertEqual(summary["cachedInputTokens"], 8)
        self.assertEqual(summary["outputTokens"], 4)
        self.assertEqual(summary["wallTimeSeconds"], 12.5)
        self.assertEqual(summary["communicationErrors"], [])
        self.assertEqual(summary["avoidableStrategicWakes"], [])

    def test_composed_frontier_forced_pass_is_an_avoidable_wake(self):
        route = {"path": "composed", "handledBy": {
            "role": "frontier_escalation", "implementation": "openai-frontier",
        }}
        records = [
            {
                "callback": "chooseAction", "playerId": "ai-a",
                "observation": {"agentToAct": "ai-a", "knownDeck": {},
                                "legalActions": [{"actionId": 1, "kind": "PassPriority"}]},
                "choice": {"channel": "action", "actionId": 1,
                           "metadata": {"routing": route, "provider": "openai"}},
            },
            {
                "callback": "chooseAction", "playerId": "ai-b",
                "observation": {"agentToAct": "ai-b", "knownDeck": {},
                                "legalActions": [{"actionId": 2, "kind": "PassPriority"}]},
                "choice": {"channel": "action", "actionId": 2,
                           "metadata": {"routing": {"path": "composed", "handledBy": {
                               "role": "deterministic", "implementation": "native-no-choice",
                           }}}},
            },
        ]
        summary = _summarize(records, completed=True, lobby_id="lobby", game_ids=["game"],
                             max_turn=1, log_path=None, wall_time_seconds=1)
        self.assertEqual(summary["routing"], {"strategic": 1, "mechanical": 1})
        self.assertEqual(summary["strategicByKind"], {"PassPriority": 1})
        self.assertEqual(summary["mechanicalByHandler"], {"native-no-choice": 1})
        self.assertEqual(len(summary["avoidableStrategicWakes"]), 1)
        self.assertFalse(summary["technicalQualified"])

    def test_malformed_composed_route_cannot_qualify(self):
        records = [{"callback": "chooseAction", "choice": {"metadata": {
            "provider": "openai", "routing": {"path": "composed"},
        }}}]
        summary = _summarize(records, completed=True, lobby_id="lobby", game_ids=["game"],
                             max_turn=1, log_path=None, wall_time_seconds=1)
        self.assertEqual(summary["routingClassificationErrors"], 1)
        self.assertFalse(summary["technicalQualified"])


if __name__ == "__main__":
    unittest.main()
