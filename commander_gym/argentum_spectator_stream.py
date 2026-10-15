"""Authenticated Argentum spectator WebSocket client for the Linux console.

Only forwards spectatorStateUpdate messages. Never requests a pilot/seat view.
The connect token belongs to this dedicated observer, not to a playing seat.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable
from urllib.parse import urlsplit

LOG = logging.getLogger(__name__)

class SpectatorProtocolError(RuntimeError):
    pass

def validate_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in ("ws", "wss") or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("server must be a ws:// or wss:// URL without credentials")
    if parsed.path != "/game":
        raise ValueError("Argentum spectator WebSocket URL must end in /game")

def message(kind: str, **fields: Any) -> str:
    return json.dumps({"type": kind, **fields}, separators=(",", ":"))

def handle_message(payload: str, game_id: str, emit: Callable[[dict[str, Any]], None]) -> str | None:
    """Return 'connected', 'ended', or None. Raise for fatal protocol failures."""
    try:
        msg = json.loads(payload)
    except (ValueError, TypeError):
        return None
    if not isinstance(msg, dict):
        return None
    kind = msg.get("type")
    if kind in ("connected", "reconnected"):
        return "connected"
    if kind == "spectatorStateUpdate" and msg.get("gameSessionId") == game_id:
        emit(msg)
    elif kind == "gameOver" and msg.get("gameId") in (None, game_id):
        return "ended"
    elif kind == "error":
        raise SpectatorProtocolError("Argentum spectator error: " + str(msg.get("code") or msg.get("message") or "unknown"))
    elif kind == "sessionReplaced":
        raise SpectatorProtocolError("spectator session was replaced by another connection")
    return None

def watch(
    url: str,
    game_id: str,
    emit: Callable[[dict[str, Any]], None],
    *,
    player_name: str = "Commander Gym Spectator",
    stop: Callable[[], bool] = lambda: False,
    ws_factory: Callable[..., Any] | None = None,
    retry_delay: float = 2.0,
) -> None:
    """Run a read-only spectator session, resubscribing after a network interruption.

    Uses a fresh, anonymous spectator identity on each connection: never steals an
    existing player session. Requires websocket-client only when actually running.
    """
    validate_url(url)
    if not game_id.strip():
        raise ValueError("game session ID is required")
    if ws_factory is None:
        try:
            from websocket import create_connection
        except ImportError as exc:
            raise RuntimeError("Install websocket-client to connect to Argentum") from exc
        ws_factory = create_connection
    while not stop():
        ws = None
        try:
            ws = ws_factory(url, timeout=15, enable_multithread=True)
            ws.send(message("connect", playerName=player_name))
            subscribed = False
            while not stop():
                raw = ws.recv()
                if not raw:
                    raise ConnectionError("Argentum spectator connection closed")
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                status = handle_message(raw, game_id, emit)
                if status == "connected" and not subscribed:
                    ws.send(message("spectateGame", gameSessionId=game_id))
                    subscribed = True
                elif status == "ended":
                    return
        except SpectatorProtocolError:
            raise
        except Exception as exc:
            # Transport libraries can raise their own WebSocketException subclasses.
            if isinstance(exc, (ValueError, TypeError, KeyError, AttributeError)):
                raise
            LOG.warning("Spectator disconnected (%s); reconnecting", type(exc).__name__)
            if not stop():
                time.sleep(retry_delay)
        finally:
            if ws is not None:
                ws.close()
