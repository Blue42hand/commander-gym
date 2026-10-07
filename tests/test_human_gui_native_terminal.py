from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from commander_gym.human_gui_session import GuiNativeTerminalWatch, GuiProvenanceWatch
from commander_gym.openai_run_budget import OpenAIRunBudget
from scripts import run_human_three_luna_gui as launcher


NATIVE = {"schemaVersion": 1, "source": "native_ai_websocket_game_over", "winnerId": "ai-0"}
original_scan = GuiProvenanceWatch.scan


class NativeTerminalTests(unittest.TestCase):
    def test_atomic_receipt_reader_missing_duplicate_draw_and_invalid_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'native-terminal.json'
            watch = GuiNativeTerminalWatch(path)
            watch.scan()
            self.assertIsNone(watch.receipt)
            path.with_suffix('.tmp').write_text(json.dumps(NATIVE))
            watch.scan()
            self.assertIsNone(watch.receipt)
            path.with_suffix('.tmp').rename(path)
            watch.scan(); watch.scan()
            self.assertEqual(watch.receipt, NATIVE)
            path.write_text(json.dumps({**NATIVE, 'winnerId': None}))
            draw = GuiNativeTerminalWatch(path); draw.scan()
            self.assertIsNone(draw.receipt['winnerId'])
            for invalid in ({**NATIVE, 'source': 'policy'}, {**NATIVE, 'rawState': 'private'},
                            {**NATIVE, 'schemaVersion': True}, {**NATIVE, 'winnerId': ''}):
                path.write_text(json.dumps(invalid))
                with self.assertRaises(ValueError):
                    GuiNativeTerminalWatch(path).scan()

    def run_supervisor(self, root, *, interrupted=False, failed=False, disconnected=False, settle=False,
                       late_error=False, cleanup_error=False, provenance_delay=False):
        plan = {gate: True for gate in launcher.GATES}
        plan.update(runtimeLock=str(root/'runtime.lock'), wallSeconds=100, serverPort=18080,
                    sidecarPort=18083, frontendPort=15175, engineDir=str(root/'engine'),
                    catalogRoot=str(root/'catalog'), budgetLedger=str(root/'budget'),
                    cumulativeCapUsd=5, cumulativeMaxRequests=100)
        before = OpenAIRunBudget(root/'budget', 5, max_requests=100, initialize_new_ledger=True).snapshot()
        proof = dict(classpath=['/tmp/offline.class'], snapshot=before, sessionCapUsd=1, sessionMaxRequests=5)
        processes = [Mock() for _ in range(3)]
        for process in processes:
            process.poll.return_value = 0 if disconnected else None
        scans = []
        run = root/'run'
        def scan(watch):
            scans.append(True)
            if interrupted:
                raise InterruptedError
            watch.failed = failed
            # Three masked AI callbacks, no human callback and no terminal policy callback.
            if len(scans) == 1:
                (run/'policy.jsonl').write_text(''.join(json.dumps({'observation': {
                    'perspectivePlayerId': f'ai-{i}', 'state': {'turnNumber': 32, 'isGameOver': False}
                }})+'\n' for i in range(3)))
                (run/'native-terminal.json').write_text(json.dumps(NATIVE))
        with ExitStack() as stack:
            stack.enter_context(patch.object(launcher, 'preflight', return_value=proof))
            stack.enter_context(patch.object(launcher, 'read_key', return_value='offline'))
            stack.enter_context(patch.object(launcher, 'await_ready'))
            stack.enter_context(patch.object(launcher, 'verify_advertised_profiles'))
            stack.enter_context(patch.object(launcher.socket, 'socket'))
            stack.enter_context(patch.object(launcher.subprocess, 'Popen', side_effect=processes))
            native_scan = GuiNativeTerminalWatch.scan
            def scan_native(watch):
                native_scan(watch)
                if late_error:
                    with (run/'policy.jsonl').open('a') as stream:
                        stream.write('{"choice":{"channel":"error"}}\n')
            stack.enter_context(patch.object(launcher.GuiNativeTerminalWatch, 'scan', scan_native))
            # Use the real incremental provenance reader on rescans so a late
            # error cannot be hidden by the initial scripted callback fixture.
            def rescan(watch):
                scan(watch)
                if len(scans) > 1:
                    # Class method was patched, retain the original below.
                    original_scan(watch)
            stack.enter_context(patch.object(launcher.GuiProvenanceWatch, 'scan', rescan))
            if provenance_delay:
                finalize = launcher._finalize_provenance
                drained_calls = []
                def delayed(*args, **kwargs):
                    drained_calls.append(True)
                    if len(drained_calls) == 1:
                        return [], 0, False, 'reserved callback not yet written'
                    return finalize(*args, **kwargs)
                stack.enter_context(patch.object(launcher, '_finalize_provenance', delayed))
            snapshots = [{**before, 'unsettledRequests': 1}, before] if settle else [before, before]
            budget = stack.enter_context(patch.object(launcher, 'checked_existing_budget', side_effect=snapshots))
            sleep = stack.enter_context(patch.object(launcher.time, 'sleep'))
            def cleanup(_process):
                if cleanup_error:
                    with (run/'policy.jsonl').open('a') as stream:
                        stream.write('{"choice":{"channel":"error"}}\n')
            stop = stack.enter_context(patch.object(launcher, '_stop_and_verify_group', side_effect=cleanup))
            status = launcher.supervise(plan, root, run, root/'unused-key', Path('/offline/python'))
            self.assertEqual([c.args[0] for c in stop.call_args_list], processes[::-1])
            self.assertEqual(stop.call_count, 3)
            self.assertEqual(sleep.call_count, 1 if settle or provenance_delay else 0)
        return status, json.loads((run/'result.json').read_bytes()), budget.call_count

    def test_settlement_waits_for_provenance_and_rescans_racing_failures(self):
        for options in ({'provenance_delay': True}, {'late_error': True}, {'cleanup_error': True}):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as tmp:
                status, receipt, _ = self.run_supervisor(Path(tmp), **options)
                failed = 'provenance_delay' not in options
                self.assertEqual(status, 1 if failed else 0)
                self.assertEqual(receipt['reason'], 'callback_or_native_failure' if failed else 'native_game_over')
                self.assertEqual(receipt['naturalTerminalVerified'], not failed)

    def test_native_notification_without_terminal_policy_callback_settles_and_cleans_once(self):
        for settle, disconnected in ((False, False), (True, False), (False, True)):
            with self.subTest(settle=settle, disconnected=disconnected), tempfile.TemporaryDirectory() as tmp:
                status, receipt, calls = self.run_supervisor(Path(tmp), settle=settle, disconnected=disconnected)
                self.assertEqual(status, 0)
                self.assertEqual(receipt['reason'], 'native_game_over')
                self.assertTrue(receipt['naturalTerminalVerified'])
                self.assertFalse(receipt['qualification'])
                self.assertFalse(receipt['automaticReplacement'])
                self.assertEqual(receipt['nativeTerminalNotification'], NATIVE)
                self.assertEqual(calls, 2 if settle else 1)

    def test_operator_stop_and_callback_failure_cannot_be_promoted_by_terminal_file(self):
        for interrupted, failed, reason in ((True, False, 'operator_stop'),
                                           (False, True, 'callback_or_native_failure')):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as tmp:
                status, receipt, _ = self.run_supervisor(Path(tmp), interrupted=interrupted, failed=failed)
                self.assertEqual(receipt['reason'], reason)
                self.assertFalse(receipt['naturalTerminalVerified'])
                self.assertIsNone(receipt['nativeTerminalNotification'])
                self.assertEqual(status, 0 if interrupted else 1)
