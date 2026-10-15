"""Headless, read-only Commander Gym console spectator.

Consumes newline-delimited *public spectator* snapshots supplied by a trusted
Argentum spectator bridge. Never reads private pilot observations.
Works on Linux virtual consoles with no graphical desktop.
"""
from __future__ import annotations
import argparse
import json
import os
import select
import sys
import time
from collections import deque

def clean(value: object, width: int = 60) -> str:
    return str(value if value is not None else "—").replace("\x1b", "?").replace("\n", " ")[:width]

def render(snapshot: dict, events: deque[str], width: int = 90) -> str:
    game = snapshot.get("gameState") or {}
    roster = snapshot.get("players") or []
    if not isinstance(roster, list): roster = []
    lines = ["COMMANDER GYM  /  LIVE SPECTATOR", "=" * min(width, 90)]
    lines.append(f"Game: {clean(snapshot.get('gameSessionId'))}  Phase: {clean(snapshot.get('currentPhase'))}")
    lines.append(f"Active: {clean(snapshot.get('activePlayerId'))}  Priority: {clean(snapshot.get('priorityPlayerId'))}")
    lines.append("-" * min(width, 90))
    # The two legacy player fields have complete public board summaries.
    # For additional seats, only render the published roster without guessing.
    for i, seat in enumerate(roster):
        if not isinstance(seat, dict): continue
        ident = seat.get("playerId")
        legacy = next((snapshot.get(k) for k in ("player1", "player2")
                       if isinstance(snapshot.get(k), dict) and snapshot[k].get("playerId") == ident), {})
        name = seat.get("playerName") or legacy.get("playerName") or f"Seat {i+1}"
        life = legacy.get("life", "—")
        lines.append(f"{clean(name, 30):30} LIFE {clean(life, 5):>5}  HAND {clean(legacy.get('handSize'), 5):>5}  BOARD {len(legacy.get('battlefield') or []):>3}" if legacy else f"{clean(name, 30)}  [see public game state]")
        if legacy:
            board = [clean(c.get("name"), 22) for c in (legacy.get("battlefield") or []) if isinstance(c, dict)]
            if board: lines.append("  " + ", ".join(board[:10])[:width-3])
    lines.append("-" * min(width, 90))
    decision = snapshot.get("decisionStatus") or {}
    if isinstance(decision, dict) and decision:
        lines.append("PENDING: " + clean(decision.get("playerName")) + " / " + clean(decision.get("displayText")))
    lines.append("EVENTS (public snapshot changes)")
    lines.extend("  " + clean(e, width-4) for e in events)
    lines.append("")
    lines.append("q quit  |  Ctrl+C quit  |  updates on spectator snapshots")
    return "\n".join(lines)

def run(source, output, refresh: float = 0.25) -> None:
    latest = {}
    events: deque[str] = deque(maxlen=7)
    previous = None
    is_tty = output.isatty()
    while True:
        ready, _, _ = select.select([source], [], [], refresh)
        if ready:
            line = source.readline()
            if not line: break
            try: msg = json.loads(line)
            except json.JSONDecodeError: continue
            if not isinstance(msg, dict): continue
            if msg.get("type") not in ("SpectatorStateUpdate", "spectatorStateUpdate") and "gameSessionId" not in msg:
                continue
            signature = (msg.get("currentPhase"), msg.get("activePlayerId"), msg.get("priorityPlayerId"))
            if previous is not None and signature != previous:
                events.appendleft(f"{clean(signature[0])} / active {clean(signature[1])} / priority {clean(signature[2])}")
            previous = signature
            latest = msg
        if latest:
            if is_tty: output.write("\x1b[H\x1b[2J")
            output.write(render(latest, events, os.get_terminal_size(output.fileno()).columns if is_tty else 90)+"\n")
            output.flush()

def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Linux console spectator")
    parser.add_argument("--input", default="-", help="NDJSON spectator snapshots; - for stdin")
    args = parser.parse_args()
    if args.input == "-": run(sys.stdin, sys.stdout)
    else:
        with open(args.input, encoding="utf-8") as source: run(source, sys.stdout, refresh=0)

if __name__ == "__main__": main()
