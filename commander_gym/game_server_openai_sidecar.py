"""Runnable OpenAI-backed policy sidecar for normal Argentum game-server AI seats.

This is the production/development launcher counterpart to the scripted acceptance
sidecar. It preserves the same loopback-only, bearer-authenticated transport and
masked-seat policy boundary, but lazily binds every Argentum AI player id to its own
stable Commander Gym pilot:

    GameServerSeatAdapter -> RoutingPilot -> OpenAIResponsesPilot

The OpenAI SDK is imported only when a live client is needed so the core Commander
Gym test suite does not require optional provider dependencies.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from .game_server_seat import GameServerSeatAdapter, SeatProvenance
from .game_server_sidecar import (
    GameServerSidecarConfig,
    GameServerSidecarConfigurationError,
    GameServerSidecarServer,
)
from .openai_responses_pilot import OpenAIResponsesPilot
from .pilot_routing import RoutingPilot


DEFAULT_OPENAI_GAME_SERVER_MODEL = "gpt-5.6-luna"


class OpenAIGameServerSidecarConfigurationError(RuntimeError):
    """Raised when the live OpenAI-backed sidecar cannot start safely."""


@dataclass(frozen=True)
class OpenAIGameServerSidecarConfig:
    """Runtime configuration for one local game-server policy sidecar."""

    token: str = field(repr=False)
    api_key: str = field(repr=False)
    model: str = DEFAULT_OPENAI_GAME_SERVER_MODEL
    bind_host: str = "127.0.0.1"
    port: int = 8083
    timeout: float = 60.0
    max_attempts: int = 2
    provenance_path: Path | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.token, str) or not self.token.strip():
            raise OpenAIGameServerSidecarConfigurationError(
                "COMMANDER_GYM_SIDECAR_TOKEN must not be blank"
            )
        if not isinstance(self.api_key, str) or not self.api_key.strip():
            raise OpenAIGameServerSidecarConfigurationError(
                "OPENAI_API_KEY must not be blank"
            )
        if not isinstance(self.model, str) or not self.model.strip():
            raise OpenAIGameServerSidecarConfigurationError(
                "COMMANDER_GYM_OPENAI_MODEL must not be blank"
            )
        if isinstance(self.timeout, bool) or not isinstance(self.timeout, (int, float)):
            raise OpenAIGameServerSidecarConfigurationError(
                "COMMANDER_GYM_OPENAI_TIMEOUT must be numeric"
            )
        if self.timeout <= 0:
            raise OpenAIGameServerSidecarConfigurationError(
                "COMMANDER_GYM_OPENAI_TIMEOUT must be positive"
            )
        if type(self.max_attempts) is not int or self.max_attempts < 1:
            raise OpenAIGameServerSidecarConfigurationError(
                "COMMANDER_GYM_OPENAI_MAX_ATTEMPTS must be a positive integer"
            )

        try:
            GameServerSidecarConfig(
                token=self.token,
                bind_host=self.bind_host,
                port=self.port,
            )
        except GameServerSidecarConfigurationError as exc:
            raise OpenAIGameServerSidecarConfigurationError(str(exc)) from exc

        object.__setattr__(self, "model", self.model.strip())
        object.__setattr__(self, "timeout", float(self.timeout))

    def sidecar_config(self) -> GameServerSidecarConfig:
        return GameServerSidecarConfig(
            token=self.token,
            bind_host=self.bind_host,
            port=self.port,
        )


def openai_game_server_sidecar_from_environment(
    environment: Mapping[str, str],
) -> OpenAIGameServerSidecarConfig:
    """Load live game-server sidecar configuration from an explicit environment."""

    def integer(name: str, default: str) -> int:
        raw = environment.get(name, default)
        try:
            return int(raw)
        except (TypeError, ValueError) as exc:
            raise OpenAIGameServerSidecarConfigurationError(
                f"{name} must be an integer"
            ) from exc

    def number(name: str, default: str) -> float:
        raw = environment.get(name, default)
        try:
            return float(raw)
        except (TypeError, ValueError) as exc:
            raise OpenAIGameServerSidecarConfigurationError(
                f"{name} must be numeric"
            ) from exc

    raw_provenance = environment.get("COMMANDER_GYM_SIDECAR_PROVENANCE")
    provenance_path = (
        Path(raw_provenance).expanduser() if raw_provenance and raw_provenance.strip() else None
    )

    return OpenAIGameServerSidecarConfig(
        token=environment.get("COMMANDER_GYM_SIDECAR_TOKEN", ""),
        api_key=environment.get("OPENAI_API_KEY", ""),
        model=environment.get(
            "COMMANDER_GYM_OPENAI_MODEL",
            DEFAULT_OPENAI_GAME_SERVER_MODEL,
        ),
        bind_host=environment.get("COMMANDER_GYM_SIDECAR_HOST", "127.0.0.1"),
        port=integer("COMMANDER_GYM_SIDECAR_PORT", "8083"),
        timeout=number("COMMANDER_GYM_OPENAI_TIMEOUT", "60"),
        max_attempts=integer("COMMANDER_GYM_OPENAI_MAX_ATTEMPTS", "2"),
        provenance_path=provenance_path,
    )


class JsonlSeatProvenanceWriter:
    """Thread-safe private JSONL provenance for masked game-server policy calls."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        parent = path.parent
        if not parent.exists():
            raise OpenAIGameServerSidecarConfigurationError(
                f"provenance directory does not exist: {parent}"
            )
        try:
            with path.open("a", encoding="utf-8"):
                pass
        except OSError as exc:
            raise OpenAIGameServerSidecarConfigurationError(
                f"provenance path is not writable: {path}"
            ) from exc

    def write(self, player_id: str, event: SeatProvenance) -> None:
        record = {
            "event": "game_server_policy",
            "playerId": player_id,
            "callback": event.callback,
            "observation": event.observation,
            "choice": event.choice,
        }
        line = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)
                handle.flush()


SeatProvenanceSink = Callable[[str, SeatProvenance], None]


def _default_openai_client(config: OpenAIGameServerSidecarConfig) -> Any:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise OpenAIGameServerSidecarConfigurationError(
            "OpenAI SDK is not installed; install requirements-openai.txt"
        ) from exc
    return OpenAI(api_key=config.api_key, timeout=config.timeout)


def build_openai_game_server_sidecar(
    config: OpenAIGameServerSidecarConfig,
    *,
    client: Any | None = None,
    provenance_sink: SeatProvenanceSink | None = None,
) -> GameServerSidecarServer:
    """Build a loopback sidecar with one stable Luna-backed pilot per Argentum seat."""

    provider_client = client if client is not None else _default_openai_client(config)

    if provenance_sink is None and config.provenance_path is not None:
        writer = JsonlSeatProvenanceWriter(config.provenance_path)
        provenance_sink = writer.write

    def seat_factory(player_id: str) -> GameServerSeatAdapter:
        strategic = OpenAIResponsesPilot(
            client=provider_client,
            model=config.model,
            max_attempts=config.max_attempts,
        )
        pilot = RoutingPilot(strategic_pilot=strategic)
        sink = (
            None
            if provenance_sink is None
            else lambda event, player_id=player_id: provenance_sink(player_id, event)
        )
        return GameServerSeatAdapter(
            pilot,
            player_id,
            provenance_sink=sink,
        )

    sidecar = config.sidecar_config()
    return GameServerSidecarServer(
        (sidecar.bind_host, sidecar.port),
        sidecar,
        seat_factory=seat_factory,
    )


def main() -> int:
    """Run the normal OpenAI-backed game-server policy sidecar until interrupted."""

    config = openai_game_server_sidecar_from_environment(os.environ)
    server = build_openai_game_server_sidecar(config)
    print(
        "Commander Gym game-server sidecar "
        f"listening on {config.bind_host}:{server.server_port} "
        f"with model {config.model}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
