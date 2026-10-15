"""Select currently available Argentum spectator games without starting any games.

Reads existing public live-game endpoints and, optionally, an explicitly
configured private AI-tournament status endpoint. No privileged access.
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

def candidates(ws_url: str, lobby_id: str | None=None) -> list[str]:
    base=http_base(ws_url)
    paths=[]
    if lobby_id:
        from urllib.parse import quote
        paths.append("/api/dev/ai-tournament/"+quote(lobby_id,safe=""))
    else:
        paths.extend(("/api/quick-games/live","/api/tournaments/live"))
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
        found=candidates(ws_url,lobby_id)
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
