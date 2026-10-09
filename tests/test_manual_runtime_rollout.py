"""Fault-injected closed-admission transactions; no real services or games."""
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from commander_gym.manual_runtime_profile import ManualRuntimeError, ValidatedRuntime, canonical, digest
from commander_gym.manual_runtime_rollout import RuntimeSnapshot, dispatch_update, promote
from test_manual_runtime_profile import fixture_profile, qualification, approval


class FakeHost:
    def __init__(self, runtime):
        self.candidate = runtime
        self.original = RuntimeSnapshot('0'*64, 1, 'keyless', 'c'*40, 'd'*40, 1)
        self.selected = self.original
        self.logical_current = self.original
        self.events = []
        self.locked = False
        self.mutation = {}
        self.candidate_health_failure = False
        self.stop_barrier = True
        self.resume_failure = False
        self.boot = 'qa-old-boot'

    @contextmanager
    def exclusive_lock(self):
        if self.locked:
            raise AssertionError('nested lock')
        self.locked = True
        try:
            yield
        finally:
            self.locked = False

    def _event(self, name):
        assert self.locked
        self.events.append(name)

    def assert_no_unfinished_transaction(self): self._event('transaction-clear')
    def current(self): return self.logical_current
    def verify_snapshot(self, value): self._event('verify:'+value.kind)
    def status(self):
        self._event('status')
        target = self.selected
        if isinstance(target, ValidatedRuntime):
            if self.candidate_health_failure:
                raise ManualRuntimeError('qa health failure')
            profile = target.profile
            runtime_id, engine, gym, schema = target.runtime_id, profile['engineSha'], profile['gymSha'], 1
        else:
            runtime_id, engine, gym, schema = target.runtime_id, target.engine_sha, target.gym_sha, 1
        return {'releaseId': runtime_id, 'engineSha': engine, 'gymSha': gym, 'recordingSchemaVersion': schema,
                'bootId': self.boot, 'observedUnix': 1000, 'recordingHealthy': True, 'recoveryComplete': True,
                'activeGames': 0, 'pendingActivities': 0, 'pendingRecordWrites': 0, 'inFlightAdmissions': 0,
                'acceptingNewGames': False, 'drainAcknowledged': True, 'recordingDrainComplete': True, **self.mutation}
    def drain(self, boot): self._event('drain'); return self.status()
    def begin(self, previous, candidate): self._event('begin')
    def stop_closure(self): self._event('stop')
    def quiescence_barrier(self): self._event('stop-barrier'); return self.stop_barrier
    def select(self, value): self._event('select:'+('manual' if isinstance(value, ValidatedRuntime) else 'previous')); self.selected=value
    def start_closed(self): self._event('start-closed'); self.boot='qa-new-boot'
    def complete(self, value):
        self._event('commit')
        p=value.profile
        self.logical_current=RuntimeSnapshot(value.runtime_id, value.sequence, 'manual-luna-v1', p['engineSha'], p['gymSha'], 1)
    def complete_rollback(self, previous, consumed_sequence):
        self._event('rollback-complete')
        self.logical_current=RuntimeSnapshot(previous.runtime_id, consumed_sequence, previous.kind, previous.engine_sha, previous.gym_sha, previous.recording_schema)
    def resume(self, boot, runtime_id):
        self._event('resume')
        if self.resume_failure: raise OSError('ambiguous resume')
    def held(self, code): self._event('held:'+code)
    def delegate_keyless_update(self): self._event('keyless-delegate'); return {'state':'keyless-updater'}


class ManualRuntimeRolloutTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory(prefix='.runtime-rollout-qa-',dir=Path.cwd())
        self.addCleanup(self.tmp.cleanup)
        self.profile=fixture_profile(Path(self.tmp.name))
        self.receipt=qualification(self.profile)
        self.approval=approval(self.profile,self.receipt)
        self.runtime=ValidatedRuntime(canonical(self.profile),digest(self.profile),2)
        self.host=FakeHost(self.runtime)

    def run_transaction(self):
        with patch('commander_gym.manual_runtime_rollout.precredential_validate',return_value=self.runtime):
            return promote(self.host,self.profile,self.receipt,self.approval,clock=lambda:1000)

    def test_success_publishes_only_after_closed_health_then_opens(self):
        result=self.run_transaction()
        self.assertEqual(result['state'],'promoted')
        events=self.host.events
        self.assertLess(events.index('drain'),events.index('begin'))
        self.assertLess(events.index('stop-barrier'),events.index('select:manual'))
        self.assertLess(events.index('start-closed'),events.index('commit'))
        self.assertLess(events.index('commit'),events.index('resume'))

    def test_every_busy_or_unknown_count_blocks_before_drain(self):
        for key in ('activeGames','pendingActivities','pendingRecordWrites','inFlightAdmissions'):
            for value in (1,None,False):
                self.host=FakeHost(self.runtime)
                self.host.mutation={key:value}
                with self.subTest(key=key,value=value),self.assertRaises(ManualRuntimeError):
                    self.run_transaction()
                self.assertNotIn('drain',self.host.events)
                self.assertNotIn('begin',self.host.events)

    def test_wrong_identity_stale_or_unhealthy_state_has_no_service_effect(self):
        for mutation in ({'releaseId':'9'*64},{'observedUnix':989},{'observedUnix':1001},{'recordingHealthy':False},{'recoveryComplete':False},{'acceptingNewGames':None}):
            self.host=FakeHost(self.runtime);self.host.mutation=mutation
            with self.subTest(mutation=mutation),self.assertRaises(ManualRuntimeError):self.run_transaction()
            self.assertNotIn('begin',self.host.events)

    def test_drain_must_preserve_boot_and_recorder_barrier(self):
        for mutation in ({'bootId':'changed-boot'},{'recordingDrainComplete':False},{'acceptingNewGames':True}):
            self.host=FakeHost(self.runtime)
            original=self.host.drain
            self.host.drain=lambda boot, mutation=mutation: {**original(boot),**mutation}
            with self.subTest(mutation=mutation),self.assertRaises(ManualRuntimeError):self.run_transaction()
            self.assertNotIn('begin',self.host.events)

    def test_current_is_rechecked_after_drain(self):
        original=self.host.drain
        def drain(boot):
            state=original(boot)
            self.host.logical_current=RuntimeSnapshot('8'*64,3,'keyless','c'*40,'d'*40,1)
            return state
        self.host.drain=drain
        with self.assertRaisesRegex(ManualRuntimeError,'current_changed'):self.run_transaction()
        self.assertNotIn('begin',self.host.events)

    def test_precommit_failure_restores_exact_previous_and_consumes_sequence(self):
        self.host.candidate_health_failure=True
        with self.assertRaisesRegex(ManualRuntimeError,'candidate_failed_previous_restored'):self.run_transaction()
        self.assertEqual(self.host.logical_current.runtime_id,self.host.original.runtime_id)
        self.assertEqual(self.host.logical_current.sequence,2)
        self.assertNotIn('commit',self.host.events)
        self.assertIn('rollback-complete',self.host.events)
        self.assertEqual(self.host.events[-1],'resume')

    def test_failed_stop_barrier_holds_without_selecting_or_resuming(self):
        self.host.stop_barrier=False
        with self.assertRaisesRegex(ManualRuntimeError,'rollback_unconfirmed'):self.run_transaction()
        self.assertFalse(any(event.startswith('select:') for event in self.host.events))
        self.assertNotIn('resume',self.host.events)
        self.assertEqual(self.host.events[-1],'held:rollback_unconfirmed')

    def test_ambiguous_resume_never_stops_or_rolls_back_committed_runtime(self):
        self.host.resume_failure=True
        with self.assertRaisesRegex(ManualRuntimeError,'committed_resume_unconfirmed'):self.run_transaction()
        last=self.host.events.index('commit')
        self.assertNotIn('stop',self.host.events[last:])
        self.assertNotIn('rollback-complete',self.host.events)

    def test_manual_active_never_runs_keyless_updater_even_with_candidate(self):
        self.host.logical_current=RuntimeSnapshot('8'*64,4,'manual-luna-v1','a'*40,'b'*40,1)
        result=dispatch_update(self.host,manual_candidate=(self.profile,self.receipt,self.approval))
        self.assertEqual(result['state'],'held-manual-profile')
        self.assertNotIn('keyless-delegate',self.host.events)

    def test_keyless_delegation_is_locked_and_unknown_profile_fails_closed(self):
        self.assertEqual(dispatch_update(self.host)['state'],'keyless-updater')
        self.host.logical_current=RuntimeSnapshot('8'*64,4,'unknown','a'*40,'b'*40,1)
        self.host.events=[]
        with self.assertRaises(ManualRuntimeError):dispatch_update(self.host)
        self.assertNotIn('keyless-delegate',self.host.events)


if __name__=='__main__':unittest.main()
