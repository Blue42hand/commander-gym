"""Select currently available Argentum spectator games without starting any games.

Reads public live-game endpoints or an explicitly selected lobby through the
native spectator protocol. No development endpoint or player credential is used.
"""
from __future__ import annotations
import json
import time
from urllib.parse import urlsplit
from urllib.request import urlopen

def http_base(ws_url: str) -> str:
    p=urlsplit(ws_url)
    if p.scheme not in ("ws","wss") or not p.netloc or p.path!="/game":
        raise ValueError("expected Argentum ws(s) URL ending in /game")
    return ("https" if p.scheme=="wss" else "http")+"://"+p.netloc

def lobby_candidates(ws_url: str, lobby_id: str, *, ws_factory=None) -> list[str]:
    """Join only an explicitly selected, already-active lobby as a spectator."""
    from urllib.parse import quote
    from .argentum_spectator_stream import SpectatorProtocolError, message
    status_url = http_base(ws_url) + "/api/tournaments/" + quote(lobby_id, safe="") + "/status"
    with urlopen(status_url, timeout=3) as response:
        status = json.load(response)
    # Joining a waiting lobby would add a playing seat. Never do that.
    if status.get("state") != "TOURNAMENT_ACTIVE":
        return []
    if ws_factory is None:
        from websocket import create_connection
        ws_factory = create_connection
    ws = ws_factory(ws_url, timeout=3, enable_multithread=True)
    try:
        ws.send(message("connect", playerName="Commander Gym Lobby Spectator"))
        joined = False
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            raw = ws.recv()
            if not raw:
                raise ConnectionError("Lobby spectator connection closed")
            msg = json.loads(raw)
            if not isinstance(msg, dict):
                continue
            kind = msg.get("type")
            if kind == "error":
                raise SpectatorProtocolError("Lobby spectator error: " + str(msg.get("code", "unknown")))
            if kind == "connected" and not joined:
                ws.send(message("joinLobby", lobbyId=lobby_id))
                joined = True
            if not joined or msg.get("lobbyId") != lobby_id:
                continue
            if kind == "freeForAllGameStarting":
                game_id = msg.get("gameSessionId")
                return [game_id] if isinstance(game_id, str) and game_id else []
            if kind == "activeMatches":
                return sorted({game["gameSessionId"] for game in msg.get("matches", [])
                               if isinstance(game, dict) and isinstance(game.get("gameSessionId"), str)
                               and game["gameSessionId"]})
        return []
    finally:
        ws.close()

def candidates(ws_url: str, lobby_id: str | None=None) -> list[str]:
    if lobby_id:
        return lobby_candidates(ws_url, lobby_id)
    base=http_base(ws_url)
    paths=["/api/quick-games/live", "/api/tournaments/live"]
    found=[]
    for path in paths:
        try:
            with urlopen(base+path,timeout=3) as response:
                payload=json.load(response)
        except (OSError,ValueError):
            continue
        games=payload.get("liveGames",[]) if isinstance(payload,dict) else payload
        if not isinstance(games,list):
            continue
        for game in games:
            if isinstance(game,dict):
                identifier=game.get("gameSessionId")
                if isinstance(identifier,str) and identifier and identifier not in found:
                    found.append(identifier)
    return sorted(found)

def follow(
    ws_url: str,
    emit,
    *,
    lobby_id: str | None=None,
    poll_seconds: float=4,
    stop=lambda:False,
    watch_fn=None,
    pause_fn=time.sleep,
    on_idle=lambda:None,
    on_game=lambda game_id:None,
    on_error=lambda exc:None,
) -> None:
    """Subscribe to one discovered game at a time; return to discovery after completion."""
    if poll_seconds<=0: raise ValueError("poll interval must be positive")
    if watch_fn is None:
        from .argentum_spectator_stream import watch as watch_fn
    while not stop():
        try:
            found=candidates(ws_url,lobby_id)
        except Exception as exc:
            on_error(exc)
            if stop(): return
            pause_fn(poll_seconds)
            continue
        if not found:
            on_idle()
            if stop(): return
            pause_fn(poll_seconds)
            continue
        game_id=found[0]
        on_game(game_id)
        try:
            watch_fn(ws_url,game_id,emit,stop=stop)
        except Exception as exc:
            on_error(exc)
        if stop(): return
        pause_fn(poll_seconds)
