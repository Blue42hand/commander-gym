#!/usr/bin/env python3
"""Acceptance-only live sidecar for the vanilla Argentum game-server proof."""

from __future__ import annotations

import argparse
import json
import os
import threading
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from commander_gym.game_server_seat import GameServerSeatAdapter, SeatProvenance
from commander_gym.game_server_sidecar import (
    MAX_REQUEST_BYTES,
    GameServerSidecarConfig,
    GameServerSidecarHandler,
    GameServerSidecarServer,
)
from commander_gym.pilot import ArgentumActionChoice, ArgentumDecisionChoice


class EvidenceWriter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()

    def write(self, value: Mapping[str, Any]) -> None:
        line = json.dumps(value, separators=(",", ":"), sort_keys=True)
        with self.lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()

    def provenance(self, record: SeatProvenance) -> None:
        self.write({"event": "provenance", **asdict(record)})


class _CaptureHandler(GameServerSidecarHandler):
    """Test-only exact HTTP body capture; production transport is untouched."""

    def _read_json(self):
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip() != "application/json":
            raise ValueError("callback requires application/json")
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError as exc:
            raise ValueError("callback requires a valid Content-Length") from exc
        if length <= 0 or length > MAX_REQUEST_BYTES:
            raise ValueError("callback body is empty or exceeds the sidecar limit")
        raw = self.rfile.read(length)
        capture_dir = os.getenv("COMMANDER_GYM_HTTP_CAPTURE_DIR")
        if capture_dir:
            destination = Path(capture_dir) / (uuid.uuid4().hex + ".json")
            temporary = destination.with_suffix(".tmp")
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as output:
                output.write(raw)
            os.replace(temporary, destination)
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("callback body must be valid JSON") from exc
        if not isinstance(payload, Mapping):
            raise ValueError("callback body must be an object")
        return payload


class AcceptancePilot:
    name = "game-server-acceptance"
    version = "1"

    def __init__(self, player_id: str, mode_file: Path, evidence: EvidenceWriter) -> None:
        self.player_id = player_id
        self.mode_file = mode_file
        self.evidence = evidence

    def _mode(self) -> str:
        mode = self.mode_file.read_text(encoding="utf-8").strip()
        return mode or "normal"

    def choose(self, observation: Mapping[str, Any]):
        state = observation.get("state")
        if isinstance(state, Mapping) and "mulligan" in state:
            self.evidence.write(
                {
                    "event": "pilot_choice",
                    "callback": "decideMulligan",
                    "mode": self._mode(),
                    "playerId": self.player_id,
                    "choice": "keep",
                }
            )
            return ArgentumActionChoice(0)

        pending = observation.get("pendingDecision")
        if isinstance(pending, Mapping) and pending.get("kind") == "BottomCards":
            hand = pending.get("hand")
            required = pending.get("cardsToPutOnBottom")
            if not isinstance(hand, list) or type(required) is not int:
                raise RuntimeError("malformed bottom-card acceptance observation")
            selected = hand[:required]
            self.evidence.write(
                {
                    "event": "pilot_choice",
                    "callback": "chooseBottomCards",
                    "mode": self._mode(),
                    "playerId": self.player_id,
                    "selectedCards": selected,
                }
            )
            return ArgentumDecisionChoice(
                {
                    "type": "CardsSelectedResponse",
                    "decisionId": pending["decisionId"],
                    "selectedCards": selected,
                }
            )

        actions = observation.get("legalActions")
        if not isinstance(actions, list) or not actions:
            raise RuntimeError("acceptance pilot received no legal action")

        mode = self._mode()
        if mode == "provider-failure":
            self.evidence.write(
                {
                    "event": "pilot_choice",
                    "callback": "chooseAction",
                    "mode": mode,
                    "playerId": self.player_id,
                    "result": "raise",
                }
            )
            raise RuntimeError("acceptance provider failure")

        if mode == "stale":
            stale_id = len(actions) + 100
            self.evidence.write(
                {
                    "event": "pilot_choice",
                    "callback": "chooseAction",
                    "mode": mode,
                    "playerId": self.player_id,
                    "actionId": stale_id,
                    "result": "stale-index",
                }
            )
            return ArgentumActionChoice(stale_id)

        if mode == "invalid":
            self.evidence.write(
                {
                    "event": "pilot_choice",
                    "callback": "chooseAction",
                    "mode": mode,
                    "playerId": self.player_id,
                    "result": "malformed-decision",
                }
            )
            return ArgentumDecisionChoice({"type": "NotAnArgentumDecisionResponse"})

        preferred = ["PlayLand", "CastSpell", "ActivateAbility", "PassPriority"]
        action_id = None
        action_type = None
        for wanted in preferred:
            for index, action in enumerate(actions):
                if action.get("actionType") == wanted:
                    action_id = index
                    action_type = wanted
                    break
            if action_id is not None:
                break
        if action_id is None:
            for index, action in enumerate(actions):
                if action.get("actionType") != "Concede":
                    action_id = index
                    action_type = action.get("actionType")
                    break
        if action_id is None:
            raise RuntimeError("acceptance pilot found no non-concede action")

        self.evidence.write(
            {
                "event": "pilot_choice",
                "callback": "chooseAction",
                "mode": mode,
                "playerId": self.player_id,
                "actionId": action_id,
                "actionType": action_type,
            }
        )
        return ArgentumActionChoice(action_id)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--mode-file", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()

    capture_dir = os.getenv("COMMANDER_GYM_HTTP_CAPTURE_DIR")
    if capture_dir:
        target = Path(capture_dir)
        if not target.is_dir() or target.stat().st_mode & 0o077:
            raise RuntimeError("HTTP capture directory must exist and be private")

    args.mode_file.write_text("normal\n", encoding="utf-8")
    args.evidence.write_text("", encoding="utf-8")
    evidence = EvidenceWriter(args.evidence)

    def factory(player_id: str) -> GameServerSeatAdapter:
        evidence.write({"event": "seat_created", "playerId": player_id})
        return GameServerSeatAdapter(
            AcceptancePilot(player_id, args.mode_file, evidence),
            player_id,
            provenance_sink=evidence.provenance,
        )

    config = GameServerSidecarConfig(token=args.token, port=args.port)
    server = GameServerSidecarServer(
        (config.bind_host, config.port),
        config,
        seat_factory=factory,
    )
    if os.getenv("COMMANDER_GYM_HTTP_CAPTURE_DIR"):
        server.RequestHandlerClass = _CaptureHandler
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
