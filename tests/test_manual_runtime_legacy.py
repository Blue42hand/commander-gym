"""Actual unchanged delegate pattern under one held lock; no host calls."""
from contextlib import contextmanager
from types import SimpleNamespace
import unittest

from commander_gym.manual_runtime_legacy import locked_legacy_call
from commander_gym.manual_runtime_profile import ManualRuntimeError


class Host:
    @contextmanager
    def exclusive_lock(self):
        raise AssertionError('must not reacquire flock')
        yield


class ManualRuntimeLegacyTests(unittest.TestCase):
    def module(self):
        module=SimpleNamespace(IdleHost=Host)
        def update():
            with module.IdleHost().exclusive_lock(): return 'keyless-unchanged'
        module.update=update
        return module

    def test_keyless_delegate_uses_held_lock_without_reacquisition(self):
        module=self.module()
        self.assertEqual(locked_legacy_call(module,'update',(),held_lock_probe=lambda:True),'keyless-unchanged')
        self.assertIs(module.IdleHost,Host)

    def test_unknown_operation_or_lost_lock_never_calls_delegate(self):
        module=self.module()
        for method, probe in (('payload',lambda:True),('update',lambda:False)):
            with self.assertRaises(ManualRuntimeError):locked_legacy_call(module,method,(),held_lock_probe=probe)
        self.assertIs(module.IdleHost,Host)

    def test_nested_delegate_checks_lock_still_held_and_restores_class(self):
        module=self.module()
        answers=iter([True,False])
        with self.assertRaises(ManualRuntimeError):locked_legacy_call(module,'update',(),held_lock_probe=lambda:next(answers))
        self.assertIs(module.IdleHost,Host)

    def test_admission_uses_fixed_argv_and_restores_it(self):
        import sys
        original = sys.argv
        module = SimpleNamespace(LinuxHost=Host)
        def main():
            self.assertEqual(sys.argv, ['host_adapter.py', 'admit-current'])
            with module.LinuxHost().exclusive_lock(): pass
        module.main = main
        locked_legacy_call(module, 'admit', (), held_lock_probe=lambda: True)
        self.assertIs(sys.argv, original)
        self.assertIs(module.LinuxHost, Host)

    def test_feeder_borrows_both_imported_host_aliases(self):
        # Make a function with isolated globals matching candidate_publish.
        globals_dict = {'IdleHost': Host}
        exec('def publish():\n    with IdleHost().exclusive_lock(): return "published"', globals_dict)
        module = SimpleNamespace(IdleHost=Host, publish=globals_dict['publish'])
        def tick():
            with module.IdleHost().exclusive_lock(): return module.publish()
        module.tick=tick
        self.assertEqual(locked_legacy_call(module, 'feeder', (), held_lock_probe=lambda:True), 'published')
        self.assertIs(module.IdleHost,Host)
        self.assertIs(globals_dict['IdleHost'],Host)

    def test_delegate_error_does_not_leave_patched_class(self):
        module=self.module()
        module.update=lambda:(_ for _ in ()).throw(OSError('qa failure'))
        with self.assertRaises(OSError):locked_legacy_call(module,'update',(),held_lock_probe=lambda:True)
        self.assertIs(module.IdleHost,Host)
