"""Request-local cancellation when the native callback connection closes."""
from contextlib import contextmanager
from contextvars import ContextVar
import select
import socket

current_cancel = ContextVar("lmstudio_callback_cancel", default=None)


class ConnectionCancellation:
    def __init__(self, connection, stopping):
        self.connection, self.stopping = connection, stopping

    def is_set(self):
        if self.stopping.is_set():
            return True
        try:
            readable, _, _ = select.select([self.connection], [], [], 0)
            return bool(readable) and self.connection.recv(1, socket.MSG_PEEK) == b""
        except (OSError, ValueError):
            return True


@contextmanager
def local_callback_context(connection, stopping):
    token = current_cancel.set(ConnectionCancellation(connection, stopping))
    try:
        yield
    finally:
        current_cancel.reset(token)
