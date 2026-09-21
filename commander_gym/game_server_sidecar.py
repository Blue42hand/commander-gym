"""Loopback-only transport for vanilla Argentum ``AiPlayerController`` callbacks.

The transport is deliberately smaller than the pilot contract.  It accepts only the
masked values supplied to ``AiPlayerController`` and delegates every strategic choice
to :class:`GameServerSeatAdapter`.  In particular, there is no field for
``AiControllerContext.snapshot``.
"""

from __future__ import annotations

import hmac
import json
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping

from .game_server_seat import (
    GameServerSeatAdapter,
    NativeActionResponse,
    NativeDecisionResponse,
)


MAX_REQUEST_BYTES = 4 * 1024 * 1024
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
_CALLBACK_PATHS = {
    "/v1/choose-action": "chooseAction",
    "/v1/decide-mulligan": "decideMulligan",
    "/v1/choose-bottom-cards": "chooseBottomCards",
}


class GameServerSidecarConfigurationError(RuntimeError):
    """Raised when the policy sidecar would start with an unsafe configuration."""


@dataclass(frozen=True)
class GameServerSidecarConfig:
    token: str
    bind_host: str = "127.0.0.1"
    port: int = 8083

    def __post_init__(self) -> None:
        if not self.token or not self.token.strip():
            raise GameServerSidecarConfigurationError("sidecar bearer token must not be blank")
        if self.bind_host not in _LOOPBACK_HOSTS:
            raise GameServerSidecarConfigurationError("sidecar must bind to a loopback address")
        if not (1 <= self.port <= 65535):
            raise GameServerSidecarConfigurationError("sidecar port must be between 1 and 65535")


class GameServerSidecarServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        config: GameServerSidecarConfig,
        seats: Mapping[str, GameServerSeatAdapter],
    ) -> None:
        self.sidecar_config = config
        self.seats = dict(seats)
        super().__init__(server_address, GameServerSidecarHandler)


class GameServerSidecarHandler(BaseHTTPRequestHandler):
    server_version = "CommanderGymGameServerSidecar/1"

    def do_POST(self) -> None:  # noqa: N802
        config = self.server.sidecar_config  # type: ignore[attr-defined]
        expected = f"Bearer {config.token}"
        if not hmac.compare_digest(self.headers.get("Authorization", ""), expected):
            self._write(401, {"error": "unauthorized"})
            return
        callback = _CALLBACK_PATHS.get(self.path)
        if callback is None:
            self._write(404, {"error": "not_found"})
            return
        try:
            request = self._read_json()
            player_id = request.get("playerId")
            if not isinstance(player_id, str) or not player_id:
                raise ValueError("callback requires playerId")
            adapter = self.server.seats[player_id]  # type: ignore[attr-defined]
            response = self._invoke(callback, adapter, request)
        except KeyError:
            self._write(404, {"error": "unknown_seat"})
            return
        except (TypeError, ValueError) as exc:
            self._write(422, {"error": str(exc)})
            return
        except Exception as exc:  # fail closed; never substitute another policy
            self._write(503, {"error": "pilot_failure", "detail": type(exc).__name__})
            return
        self._write(200, response)

    def log_message(self, _format: str, *_args: Any) -> None:
        pass

    def _invoke(
        self,
        callback: str,
        adapter: GameServerSeatAdapter,
        request: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if "snapshot" in request:
            raise ValueError("trusted runtime snapshot is forbidden at the policy boundary")
        if callback == "chooseAction":
            self._require_keys(
                request,
                {"playerId", "state", "legalActions", "pendingDecision", "recentGameLog"},
            )
            result = adapter.choose_action(
                request.get("state"),
                request.get("legalActions"),
                request.get("pendingDecision"),
                request.get("recentGameLog", ()),
            )
            if isinstance(result, NativeActionResponse):
                return {
                    "kind": "action",
                    "actionId": result.action_id,
                    "action": result.action,
                    "metadata": result.metadata,
                }
            if isinstance(result, NativeDecisionResponse):
                return {
                    "kind": "decision",
                    "playerId": result.player_id,
                    "response": result.response,
                    "metadata": result.metadata,
                }
            raise TypeError("unsupported adapter response")
        if callback == "decideMulligan":
            self._require_keys(request, {"playerId", "mulligan"})
            return {"keep": adapter.decide_mulligan(request.get("mulligan"))}
        self._require_keys(request, {"playerId", "bottomCards"})
        return {"cardIds": adapter.choose_bottom_cards(request.get("bottomCards"))}

    @staticmethod
    def _require_keys(request: Mapping[str, Any], allowed: set[str]) -> None:
        unexpected = set(request) - allowed
        if unexpected:
            raise ValueError(f"unexpected policy fields: {', '.join(sorted(unexpected))}")

    def _read_json(self) -> Mapping[str, Any]:
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip() != "application/json":
            raise ValueError("callback requires application/json")
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError as exc:
            raise ValueError("callback requires a valid Content-Length") from exc
        if length <= 0 or length > MAX_REQUEST_BYTES:
            raise ValueError("callback body is empty or exceeds the sidecar limit")
        try:
            payload = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("callback body must be valid JSON") from exc
        if not isinstance(payload, Mapping):
            raise ValueError("callback body must be an object")
        return payload

    def _write(self, status: int, body: Mapping[str, Any]) -> None:
        payload = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
