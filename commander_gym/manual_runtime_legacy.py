"""Borrow the already-held rollout lock for unchanged keyless delegates.

No lock handoff gap, nested flock acquisition, copy of their policy, or change to
their indexed source files. Imported delegates must have been byte-verified as
the exact legacyNative/legacySource artifact roles by the protected root host.
"""
from contextlib import contextmanager
import os
import threading
import sys

from .manual_runtime_profile import require


def locked_legacy_call(module, method: str, args: tuple, *, held_lock_probe):
    require(threading.current_thread() is threading.main_thread(), "legacy_main_thread_required")
    require(method in ("update", "publish", "admit", "feeder"), "legacy_operation")
    require(held_lock_probe() is True, "legacy_lock_not_held")
    owner_pid, owner_thread = os.getpid(), threading.get_ident()
    host_name = 'LinuxHost' if method == 'admit' else 'IdleHost'
    original = getattr(module, host_name)
    class BorrowedHost(original):
        @contextmanager
        def exclusive_lock(self):
            require(os.getpid() == owner_pid and threading.get_ident() == owner_thread
                    and held_lock_probe() is True, "legacy_borrowed_lock_lost")
            yield
    publish_globals = None
    original_publish_host = None
    if method == 'feeder':
        # source_feeder imports the same class by value and publish uses its own
        # module global. Both remain under this one held rollout flock.
        publish_globals = module.publish.__globals__
        original_publish_host = publish_globals['IdleHost']
        require(original_publish_host is original, 'legacy_feeder_host_mismatch')
        publish_globals['IdleHost'] = BorrowedHost
    setattr(module, host_name, BorrowedHost)
    original_argv = sys.argv
    try:
        if method == 'feeder':
            require(args == (), 'fixed_legacy_feeder')
            return module.tick()
        if method == 'admit':
            require(args == (), 'fixed_legacy_admit')
            sys.argv = ['host_adapter.py', 'admit-current']
            module.main()
            return {'state': 'keyless-admission-delegated'}
        return getattr(module, method)(*args)
    finally:
        setattr(module, host_name, original)
        if publish_globals is not None: publish_globals['IdleHost'] = original_publish_host
        sys.argv = original_argv
