"""Local lifecycle control for standalone installations, sharing the legacy lock."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import socket
import stat
import struct
import time

from .manual_runtime_profile import canonical, decode, protected_bytes, require


class ManualControl:
    def __init__(self, uid):
        self.uid = uid

    @contextmanager
    def exclusive_lock(self):
        path = Path('/var/lib/argentum-updater/updater.lock')
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_mode & 0o077 == 0,
                    'updater_lock_custody')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally: os.close(fd)

    def assert_no_unfinished_transaction(self):
        path = Path('/var/lib/argentum-updater/transactions.jsonl')
        if path.exists():
            raw = protected_bytes(path, uid=0, max_bytes=16*1024*1024)
            rows = raw.splitlines()
            require(not rows or decode(rows[-1]).get('phase') == 'completed', 'legacy_transaction_unfinished')

    def rpc(self, operation, **fields):
        require(operation in ('status', 'drain', 'resume'), 'control_operation')
        path = Path('/run/argentum-play/lifecycle.sock')
        info = path.parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == self.uid and info.st_mode & 0o027 == 0,
                'control_directory_custody')
        info = path.lstat()
        require(stat.S_ISSOCK(info.st_mode) and info.st_uid == self.uid and stat.S_IMODE(info.st_mode) == 0o600,
                'control_socket_custody')
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(5); client.connect(str(path))
            _, uid, _ = struct.unpack('3i', client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            require(uid == self.uid, 'control_peer')
            client.sendall(canonical({'protocol': 1, 'op': operation, **fields}) + b'\n')
            raw = b''
            while not raw.endswith(b'\n'):
                chunk = client.recv(4096 - len(raw))
                require(bool(chunk) and len(raw) + len(chunk) < 4096, 'control_response_size')
                raw += chunk
        result = decode(raw)
        require(result.get('protocol') == 1 and result.get('ok') is True, 'control_rejected')
        return result

    def status(self): return self.rpc('status')

    def drain(self, boot):
        self.rpc('drain', bootId=boot)
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            state = self.status()
            require(state.get('bootId') == boot, 'drain_boot_changed')
            if state.get('drainAcknowledged') is True and state.get('recordingDrainComplete') is True:
                return state
            time.sleep(.5)
        require(False, 'drain_timeout')
