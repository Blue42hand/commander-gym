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
import textwrap
import unicodedata
from collections import Counter, deque

def clean(value: object, width: int = 60) -> str:
    text = str(value if value is not None else "—")
    return "".join(" " if unicodedata.category(c).startswith("C") else c for c in text)[:max(0, width)]


def player_views(snapshot: dict) -> list[dict]:
    """Read each seat from the already-masked spectator state, with legacy fallback."""
    game = snapshot.get("gameState")
    game = game if isinstance(game, dict) else {}
    public_players = {p["playerId"]: p for p in game.get("players", [])
                      if isinstance(p, dict) and p.get("playerId")}
    legacy_players = {p["playerId"]: p for key in ("player1", "player2")
                      if isinstance(p := snapshot.get(key), dict) and p.get("playerId")}
    roster = snapshot.get("players")
    if not isinstance(roster, list) or not roster:
        roster = list(public_players.values()) or list(legacy_players.values())
    cards = game.get("cards")
    cards = cards if isinstance(cards, dict) else {}
    result = []
    for i, seat in enumerate(roster):
        if not isinstance(seat, dict):
            continue
        ident = seat.get("playerId")
        legacy = legacy_players.get(ident, {})
        public = public_players.get(ident)
        if public is not None:
            board = [card for card in cards.values() if isinstance(card, dict)
                     and isinstance(card.get("zone"), dict)
                     and card["zone"].get("zoneType") == "Battlefield"
                     and (card.get("controllerId") or card["zone"].get("ownerId")) == ident]
            state = public
        else:
            board = legacy.get("battlefield")
            board = board if isinstance(board, list) else []
            state = legacy
        result.append({
            "playerId": ident,
            "name": seat.get("name") or seat.get("playerName") or state.get("name")
                    or state.get("playerName") or f"Seat {i+1}",
            "life": state.get("life"), "handSize": state.get("handSize"),
            "battlefield": board, "available": bool(state),
        })
    return result


def card_label(card: dict) -> str:
    if card.get("isFaceDown"):
        label = "Face-down card"
    else:
        label = clean(card.get("name") or "Unknown card", 120)
        if card.get("power") is not None and card.get("toughness") is not None:
            label += f" {clean(card['power'], 8)}/{clean(card['toughness'], 8)}"
    if card.get("isTapped"):
        label += " [tapped]"
    return label


def render(snapshot: dict, events: deque[str], width: int = 90) -> str:
    width = max(10, min(width, 100))
    players = player_views(snapshot)
    names = {p["playerId"]: p["name"] for p in players}
    def name(player_id):
        return clean(names.get(player_id) or player_id)
    lines = []
    def add(text, indent=""):
        lines.extend(textwrap.wrap(text, width=width, initial_indent=indent,
                                   subsequent_indent=indent) or [""])
    add("COMMANDER GYM  /  LIVE SPECTATOR")
    add("=" * width)
    add(f"Game: {clean(snapshot.get('gameSessionId'))}  Phase: {clean(snapshot.get('currentPhase'))}")
    add(f"Active: {name(snapshot.get('activePlayerId'))}  Priority: {name(snapshot.get('priorityPlayerId'))}")
    for i, player in enumerate(players):
        add("-" * width)
        add(f"{i+1}. {clean(player['name'], 120)}")
        board = [card for card in player["battlefield"] if isinstance(card, dict)]
        if not player["available"]:
            add("Public board state unavailable", "  ")
            continue
        add(f"LIFE {clean(player['life'])}  |  HAND {clean(player['handSize'])}  |  BATTLEFIELD {len(board)}", "  ")
        labels = Counter(card_label(card) for card in board)
        battlefield = "; ".join(f"{count}x {label}" if count > 1 else label
                                for label, count in labels.items())
        add(battlefield or "(empty battlefield)", "  ")
    add("-" * width)
    decision = snapshot.get("decisionStatus") or {}
    if isinstance(decision, dict) and decision:
        add("PENDING: " + clean(decision.get("playerName")) + " / " + clean(decision.get("displayText")))
    add("EVENTS (public snapshot changes)")
    for event in events:
        add(clean(event, 160), "  ")
    add("")
    add("Live updates from the public spectator stream")
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

def live(url: str, game_id: str, output) -> None:
    """Render native public updates straight to the Linux virtual terminal."""
    from .argentum_spectator_stream import watch
    events: deque[str] = deque(maxlen=7)
    previous = [None]
    def draw(snapshot: dict) -> None:
        signature = (snapshot.get("currentPhase"), snapshot.get("activePlayerId"), snapshot.get("priorityPlayerId"))
        if previous[0] is not None and signature != previous[0]:
            events.appendleft(f"{clean(signature[0])} / active {clean(signature[1])} / priority {clean(signature[2])}")
        previous[0] = signature
        tty = output.isatty()
        if tty:
            output.write("\x1b[H\x1b[2J")
        width = os.get_terminal_size(output.fileno()).columns if tty else 90
        output.write(render(snapshot, events, width) + "\n")
        output.flush()
    watch(url, game_id, draw)

def auto_live(url: str, output, lobby_id: str | None = None) -> None:
    """Automatically follow available games on a headless Linux console."""
    from .spectator_discovery import follow
    def status(text: str) -> None:
        if output.isatty(): output.write("\x1b[H\x1b[2J")
        output.write("COMMANDER GYM / LIVE SPECTATOR\n" + text + "\n")
        output.flush()
    events: deque[str] = deque(maxlen=7)
    previous = [None]
    def draw(snapshot: dict) -> None:
        signature = (snapshot.get("currentPhase"), snapshot.get("activePlayerId"), snapshot.get("priorityPlayerId"))
        if previous[0] is not None and signature != previous[0]:
            events.appendleft(f"{clean(signature[0])} / active {clean(signature[1])} / priority {clean(signature[2])}")
        previous[0] = signature
        if output.isatty(): output.write("\x1b[H\x1b[2J")
        width = os.get_terminal_size(output.fileno()).columns if output.isatty() else 90
        output.write(render(snapshot, events, width) + "\n")
        output.flush()
    follow(url,draw,lobby_id=lobby_id,on_idle=lambda:status("Waiting for an active game…"),
           on_game=lambda game:status("Following game "+clean(game)),
           on_error=lambda exc:status("Spectator error: "+clean(type(exc).__name__)))

def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Linux console spectator")
    parser.add_argument("--input", default="-", help="NDJSON public spectator snapshots, or - for stdin")
    parser.add_argument("--server", help="Argentum game WebSocket URL (ws://host:port/game)")
    parser.add_argument("--game", help="Game session ID to spectate")
    parser.add_argument("--auto", action="store_true", help="Auto-select a live game")
    parser.add_argument("--lobby", help="Explicit lobby ID to follow private or public active games")
    args = parser.parse_args()
    if args.server:
        if args.auto:
            auto_live(args.server, sys.stdout, args.lobby)
        elif args.game:
            live(args.server, args.game, sys.stdout)
        else:
            parser.error("--auto or --game is required with --server")
    elif args.input == "-": run(sys.stdin, sys.stdout)
    else:
        with open(args.input, encoding="utf-8") as source: run(source, sys.stdout, refresh=0)

if __name__ == "__main__": main()
