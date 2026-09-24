"""Loopback-only transport for vanilla Argentum AiPlayerController callbacks.

The transport is deliberately smaller than the pilot contract. It accepts only the
masked values supplied to AiPlayerController and delegates every strategic choice
to GameServerSeatAdapter. In particular, there is no field for
AiControllerContext.snapshot.

External-controller profile ids remain opaque transport values. A configured
profile catalog can advertise exact provider-owned seat presets to Argentum, and a
profile-aware factory resolves the selected profile to the correct seat adapter.
"""

from __future__ import annotations

import hmac
import json
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Mapping, Sequence

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
_PROFILE_PATH = "/v1/controller-profiles"

SeatFactory = Callable[[str], GameServerSeatAdapter]
ProfileSeatFactory = Callable[[str, str], GameServerSeatAdapter]


class GameServerSidecarConfigurationError(RuntimeError):
    """Raised when the policy sidecar would start with an unsafe configuration."""


class UnknownProfileError(LookupError):
    """Raised when an explicit external-controller profile cannot be resolved."""


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
        seats: Mapping[str, GameServerSeatAdapter] | None = None,
        *,
        seat_factory: SeatFactory | None = None,
        profiles: Sequence[Mapping[str, Any]] = (),
        profile_seat_factory: ProfileSeatFactory | None = None,
    ) -> None:
        self.sidecar_config = config
        self.seats = dict(seats or {})
        self._seat_factory = seat_factory
        self._profile_seat_factory = profile_seat_factory
        self._profile_seats: dict[tuple[str, str], GameServerSeatAdapter] = {}
        self._profiles = self._validate_profiles(profiles)
        self._seat_lock = threading.Lock()
        super().__init__(server_address, GameServerSidecarHandler)

    @staticmethod
    def _validate_profiles(
        profiles: Sequence[Mapping[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        if isinstance(profiles, (str, bytes)) or not isinstance(profiles, Sequence):
            raise GameServerSidecarConfigurationError("profiles must be a sequence")
        result: dict[str, dict[str, Any]] = {}
        for raw in profiles:
            if not isinstance(raw, Mapping):
                raise GameServerSidecarConfigurationError("profiles must contain objects")
            profile_id = raw.get("id")
            display_name = raw.get("displayName")
            deck = raw.get("deck")
            if not isinstance(profile_id, str) or not profile_id:
                raise GameServerSidecarConfigurationError("profile id must be non-empty")
            if not isinstance(display_name, str) or not display_name:
                raise GameServerSidecarConfigurationError(
                    f"profile {profile_id!r} displayName must be non-empty"
                )
            if profile_id in result:
                raise GameServerSidecarConfigurationError(
                    f"duplicate controller profile {profile_id!r}"
                )
            if not isinstance(deck, Mapping):
                raise GameServerSidecarConfigurationError(
                    f"profile {profile_id!r} requires an exact bound deck"
                )
            cards = deck.get("cards")
            if not isinstance(cards, Mapping) or not cards:
                raise GameServerSidecarConfigurationError(
                    f"profile {profile_id!r} deck cards must be a non-empty object"
                )
            for name, count in cards.items():
                if not isinstance(name, str) or not name or type(count) is not int or count <= 0:
                    raise GameServerSidecarConfigurationError(
                        f"profile {profile_id!r} deck cards must have positive counts"
                    )
            description = raw.get("description")
            if description is not None and (
                not isinstance(description, str) or not description
            ):
                raise GameServerSidecarConfigurationError(
                    f"profile {profile_id!r} description must be non-empty"
                )
            commander = deck.get("commander")
            if commander is not None and (not isinstance(commander, str) or not commander):
                raise GameServerSidecarConfigurationError(
                    f"profile {profile_id!r} commander must be non-empty"
                )
            label = deck.get("label")
            if label is not None and (not isinstance(label, str) or not label):
                raise GameServerSidecarConfigurationError(
                    f"profile {profile_id!r} deck label must be non-empty"
                )
            result[profile_id] = {
                "id": profile_id,
                "displayName": display_name,
                **({"description": description} if description is not None else {}),
                "deck": {
                    "cards": dict(cards),
                    "label": label or display_name,
                    **({"commander": commander} if commander is not None else {}),
                },
            }
        if result and profile_seat_factory is None:  # type: ignore[name-defined]
            pass
        return result

    @property
    def controller_profiles(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(profile) for profile in self._profiles.values())

    def resolve_seat(
        self,
        player_id: str,
        profile_id: str | None = None,
    ) -> GameServerSeatAdapter:
        """Return the stable adapter for one Argentum seat/profile pair.

        An explicit profile never falls back to the legacy player-id-only factory.
        This is the fail-closed boundary that prevents a stale Binding selection from
        silently becoming a generic pilot.
        """

        if profile_id is not None:
            if profile_id not in self._profiles or self._profile_seat_factory is None:
                raise UnknownProfileError(profile_id)
            key = (player_id, profile_id)
            adapter = self._profile_seats.get(key)
            if adapter is not None:
                return adapter
            with self._seat_lock:
                adapter = self._profile_seats.get(key)
                if adapter is None:
                    adapter = self._profile_seat_factory(player_id, profile_id)
                    if not isinstance(adapter, GameServerSeatAdapter):
                        raise TypeError(
                            "profile_seat_factory must return GameServerSeatAdapter"
                        )
                    self._profile_seats[key] = adapter
                return adapter

        adapter = self.seats.get(player_id)
        if adapter is not None:
            return adapter
        if self._seat_factory is None:
            raise KeyError(player_id)

        with self._seat_lock:
            adapter = self.seats.get(player_id)
            if adapter is None:
                adapter = self._seat_factory(player_id)
                if not isinstance(adapter, GameServerSeatAdapter):
                    raise TypeError("seat_factory must return GameServerSeatAdapter")
                self.seats[player_id] = adapter
            return adapter


class GameServerSidecarHandler(BaseHTTPRequestHandler):
    server_version = "CommanderGymGameServerSidecar/2"

    def do_GET(self) -> None:  # noqa: N802
        if not self._authorized():
            self._write(401, {"error": "unauthorized"})
            return
        if self.path != _PROFILE_PATH:
            self._write(404, {"error": "not_found"})
            return
        profiles = self.server.controller_profiles  # type: ignore[attr-defined]
        self._write(200, {"profiles": list(profiles)})

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
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
            profile_id = request.get("profileId")
            if profile_id is not None and (
                not isinstance(profile_id, str) or not profile_id
            ):
                raise ValueError("profileId must be a non-empty string")
            adapter = self.server.resolve_seat(player_id, profile_id)  # type: ignore[attr-defined]
            response = self._invoke(callback, adapter, request)
        except UnknownProfileError:
            self._write(404, {"error": "unknown_profile"})
            return
        except KeyError:
            self._write(404, {"error": "unknown_seat"})
            return
        except (TypeError, ValueError) as exc:
            self._write(422, {"error": str(exc)})
            return
        except Exception as exc:
            self._write(503, {"error": "pilot_failure", "detail": type(exc).__name__})
            return
        self._write(200, response)

    def _authorized(self) -> bool:
        config = self.server.sidecar_config  # type: ignore[attr-defined]
        expected = f"Bearer {config.token}"
        return hmac.compare_digest(self.headers.get("Authorization", ""), expected)

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
        common = {"playerId", "profileId"}
        if callback == "chooseAction":
            self._require_keys(
                request,
                common | {"state", "legalActions", "pendingDecision", "recentGameLog"},
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
            self._require_keys(request, common | {"mulligan"})
            return {"keep": adapter.decide_mulligan(request.get("mulligan"))}
        self._require_keys(request, common | {"bottomCards"})
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
