"""Opt-in experiment stop controls, separate from rules and player decisions."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import math
import os
from pathlib import Path
import stat
import time
from typing import Mapping, Any

from .pilot import PilotContractError


class ExperimentalPrefixStopped(PilotContractError):
    pass


@dataclass(frozen=True)
class PrefixGuard:
    turn_limit: int
    deadline_unix: float
    receipt_path: Path

    def __post_init__(self):
        if (type(self.turn_limit) is not int or not 1 <= self.turn_limit <= 8
            or not math.isfinite(self.deadline_unix) or self.deadline_unix <= 0
            or not self.receipt_path.is_absolute()):
            raise ValueError("invalid experimental prefix guard")

    def check(self, observation: Mapping[str, Any]):
        reason, turn = None, None
        if time.time() >= self.deadline_unix:
            reason = "prefix_wall_limit"
        else:
            state = observation.get("state")
            if not isinstance(state, Mapping):
                raise ExperimentalPrefixStopped("experimental prefix requires a masked state")
            turn = state.get("turnNumber")
            if type(turn) is int and turn > self.turn_limit:
                reason = "prefix_turn_limit"
            elif type(turn) is not int and not (
                "mulligan" in state or (
                    isinstance(observation.get("pendingDecision"), Mapping)
                    and observation["pendingDecision"].get("kind") == "BottomCards"
                )
            ):
                raise ExperimentalPrefixStopped("experimental prefix requires native turnNumber")
        if reason:
            # No observations, seat IDs or model content in this control receipt.
            from .two_luna_debug import _write_private_json
            try:
                _write_private_json(self.receipt_path, {"reason": reason, "turnNumber": turn,
                                    "turnLimit": self.turn_limit})
            except FileExistsError:
                pass  # All seats share the first durable stop receipt.
            raise ExperimentalPrefixStopped(reason)


@contextmanager
def exclusive_runtime_lock(path: Path):
    if not path.is_absolute() or not path.parent.is_dir() or path.is_symlink():
        raise ValueError("experimental runtime lock must be an absolute nonsymlink path")
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode) or stat.S_IMODE(os.fstat(fd).st_mode) & 0o077:
            raise ValueError("experimental runtime lock must be a private regular file")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another process owns the shared game runtime") from exc
        yield
    finally:
        os.close(fd)
