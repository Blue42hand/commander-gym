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


def render(snapshot: dict, events: deque[str], width: int = 90, height: int | None = None) -> str:
    width = max(10, min(width, 100))
    players = player_views(snapshot)
    names = {p["playerId"]: p["name"] for p in players}
    def name(player_id):
        return clean(names.get(player_id) or player_id)
    def wrap(text, indent=""):
        return textwrap.wrap(text, width=width, initial_indent=indent,
                             subsequent_indent=indent) or [""]
    intro = wrap("COMMANDER GYM  /  LIVE SPECTATOR") + ["=" * width]
    intro += wrap(f"Game: {clean(snapshot.get('gameSessionId'))}  Phase: {clean(snapshot.get('currentPhase'))}")
    intro += wrap(f"Active: {name(snapshot.get('activePlayerId'))}  Priority: {name(snapshot.get('priorityPlayerId'))}")
    sections = []
    for i, player in enumerate(players):
        heading = ["-" * width] + wrap(f"{i+1}. {clean(player['name'], 120)}")
        board = [card for card in player["battlefield"] if isinstance(card, dict)]
        if not player["available"]:
            heading += wrap("Public board state unavailable", "  ")
            sections.append((heading, []))
            continue
        heading += wrap(f"LIFE {clean(player['life'])}  |  HAND {clean(player['handSize'])}  |  BATTLEFIELD {len(board)}", "  ")
        labels = Counter(card_label(card) for card in board)
        battlefield = "; ".join(f"{count}x {label}" if count > 1 else label
                                for label, count in labels.items())
        sections.append((heading, wrap(battlefield or "(empty battlefield)", "  ")))
    tail = ["-" * width]
    decision = snapshot.get("decisionStatus") or {}
    if isinstance(decision, dict) and decision:
        tail += wrap("PENDING: " + clean(decision.get("playerName")) + " / " + clean(decision.get("displayText")))
    tail += wrap("EVENTS (public snapshot changes)")
    for event in events:
        tail += wrap(clean(event, 160), "  ")
    tail += [""] + wrap("Live updates from the public spectator stream")
    if height is None:
        return "\n".join(intro + [line for heading, board in sections for line in heading + board] + tail)

    # Leave a row for the final newline: writing on the bottom row scrolls the console.
    limit = max(0, height - 1)
    available = limit - len(intro) - sum(len(heading) for heading, _ in sections)
    if available < sum(bool(board) for _, board in sections):
        # Tiny consoles still prioritize all names/life over headers, boards, and history.
        compact = ["LIVE SPECTATOR"[:width]]
        compact += [f"LIFE {clean(p['life'])} | {clean(p['name'], 120)}"[:width] for p in players]
        compact += ["Battlefield details hidden: terminal too small"[:width]]
        return "\n".join(compact[:limit])
    allocations = [0] * len(sections)
    while available > 0:
        progressed = False
        for i, (_, board) in enumerate(sections):
            if allocations[i] < len(board) and available > 0:
                allocations[i] += 1
                available -= 1
                progressed = True
        if not progressed:
            break
    lines = list(intro)
    for (heading, board), count in zip(sections, allocations):
        lines += heading
        if count < len(board):
            visible = max(0, count - 1)
            lines += board[:visible]
            lines += [f"  +{len(board) - visible} battlefield rows hidden"[:width]]
        else:
            lines += board
    lines += tail[:max(0, limit - len(lines))]
    return "\n".join(lines)


def write_frame(output, snapshot: dict, events: deque[str]) -> None:
    """Use both terminal dimensions; redirected output retains the complete frame."""
    if output.isatty():
        size = os.get_terminal_size(output.fileno())
        frame = render(snapshot, events, width=max(10, size.columns - 1), height=size.lines)
        output.write("\x1b[H\x1b[2J")
    else:
        frame = render(snapshot, events)
    output.write(frame + "\n")
    output.flush()

def run(source, output, refresh: float = 0.25) -> None:
    latest = {}
    events: deque[str] = deque(maxlen=7)
    previous = None
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
            write_frame(output, latest, events)

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
        write_frame(output, snapshot, events)
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
        write_frame(output, snapshot, events)
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
