"""Supervised one-human lifecycle guards; never a player or rules fallback."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from pathlib import Path
import threading
import time
from typing import Any, Mapping

from .pilot import PilotContractError


class HumanGuiSessionStopped(PilotContractError):
    pass


@dataclass
class HumanGuiSessionGuard:
    deadline_unix: float
    receipt_path: Path
    _turns: dict[Any, int] = field(default_factory=dict, init=False)
    _seats: set[Any] = field(default_factory=set, init=False)
    _lock: Any = field(default_factory=threading.Lock, init=False)

    def __post_init__(self):
        if not math.isfinite(self.deadline_unix) or self.deadline_unix <= 0 or not self.receipt_path.is_absolute():
            raise ValueError("invalid human GUI session guard")

    def _stop(self, reason: str):
        from .two_luna_debug import _write_private_json
        try:
            _write_private_json(self.receipt_path, {"reason": reason})
        except FileExistsError:
            pass
        raise HumanGuiSessionStopped(reason)

    def check(self, observation: Mapping[str, Any]):
        # Only the already-masked seat observation enters this guard. No state is
        # modified and no action is selected on a stop.
        with self._lock:
            if self.receipt_path.exists():
                raise HumanGuiSessionStopped("human GUI session already stopped")
            if time.time() >= self.deadline_unix:
                self._stop("human_gui_wall_limit")
            seat = observation.get("perspectivePlayerId")
            if not isinstance(seat, str) or not seat:
                self._stop("human_gui_missing_seat")
            self._seats.add(seat)
            if len(self._seats) > 3:
                self._stop("human_gui_replacement_game_or_extra_ai")
            state = observation.get("state")
            if not isinstance(state, Mapping):
                self._stop("human_gui_missing_native_state")
            pending = observation.get("pendingDecision")
            opening = "mulligan" in state or isinstance(pending, Mapping) and pending.get("kind") == "BottomCards"
            if opening:
                if self._turns:
                    self._stop("human_gui_replacement_game")
                return
            turn = state.get("turnNumber")
            if type(turn) is not int or turn < 0:
                self._stop("human_gui_missing_native_turn")
            if turn < self._turns.get(seat, turn):
                self._stop("human_gui_replacement_game_or_stale_turn")
            self._turns[seat] = turn
            if state.get("isGameOver") is True:
                self._stop("human_gui_native_game_over_observed")


class GuiProvenanceWatch:
    """Incremental private receipt reader; human idle time is never a stall."""
    def __init__(self, path: Path):
        self.path, self.offset, self.partial = path, 0, b""
        self.callbacks = 0
        self.failed = False
        self.native_game_over = False

    def scan(self):
        if not self.path.exists():
            return
        with self.path.open("rb") as stream:
            stream.seek(self.offset)
            added = stream.read()
            self.offset = stream.tell()
        *lines, self.partial = (self.partial + added).split(b"\n")
        for line in lines:
            row = json.loads(line)
            self.callbacks += 1
            self.failed |= row.get("choice", {}).get("channel") == "error"
            self.native_game_over |= row.get("observation", {}).get("state", {}).get("isGameOver") is True
