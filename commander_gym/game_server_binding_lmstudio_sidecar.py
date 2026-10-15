"""Explicit, credential-free local Binding runtime, separate from the sealed Luna launcher."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import threading

from .binding_catalog import load_binding_catalog
from .deck_package import ArtifactRef
from .game_server_bindings import GameServerBindingRegistry
from .game_server_binding_openai_sidecar import (
    BUILTIN_FORCED_PARAMETERLESS_COMPONENT_REF, BUILTIN_NATIVE_CERTIFIED_FORCED_PASS_COMPONENT_REF,
    _CanonicalBindingPilot,
)
from .game_server_sidecar import GameServerSidecarConfig, GameServerSidecarServer
from .lmstudio_pilot import LMStudioPilot
from .lmstudio_provider import LMStudioConfig
from .local_callback_context import local_callback_context
from .pilot import PilotContractError
from .pilot_composition import ArtificialPlayerSubsystem, MechanicalHandlerSubsystem, compose_pilot_runtime
from .pilot_routing import ForcedParameterlessChoiceHandler


def local_component_ref(config: LMStudioConfig) -> ArtifactRef:
    """Pin model, endpoint and inference limits into the selected Pilot identity."""
    digest = hashlib.sha256(json.dumps(asdict(config), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return ArtifactRef(kind="provider", artifact_id="lmstudio-chat-completions", version="1",
                       digest="sha256:" + digest)


@dataclass(frozen=True)
class LocalBindingConfig:
    sidecar: GameServerSidecarConfig
    inference: LMStudioConfig
    catalog_path: Path
    instance_root: Path
    live_catalog_root: Path | None = None


class LocalBindingComponentResolver:
    def __init__(self, config, transport=None):
        self.config, self.transport = config, transport

    def resolve(self, spec):
        if spec.role == "local_generalist" and spec.ref == local_component_ref(self.config):
            return ArtificialPlayerSubsystem(LMStudioPilot(self.config, transport=self.transport))
        if spec.role == "deterministic" and spec.ref == BUILTIN_FORCED_PARAMETERLESS_COMPONENT_REF:
            return MechanicalHandlerSubsystem(ForcedParameterlessChoiceHandler())
        if spec.role == "deterministic" and spec.ref == BUILTIN_NATIVE_CERTIFIED_FORCED_PASS_COMPONENT_REF:
            from .native_forced_pass import NativeForcedPassSubsystem
            return NativeForcedPassSubsystem()
        raise PilotContractError("local Binding requires an exact registered local component; cloud fallback is forbidden")


def build_binding_lmstudio_sidecar(config: LocalBindingConfig, *, game_capture=None,
                                   provenance_sink=None, transport=None):
    resolver = LocalBindingComponentResolver(config.inference, transport)

    def pilot_factory(pilot, binding):
        if (pilot.generalist_base != local_component_ref(config.inference)
            or pilot.escalation_provider is not None or pilot.skill_set is not None or pilot.specialists):
            raise PilotContractError("local Binding must pin the configured local model and exclude other providers")
        return _CanonicalBindingPilot(compose_pilot_runtime(pilot, resolver), pilot, binding)

    loaded = load_binding_catalog(config.catalog_path, instance_root=config.instance_root,
                                  pilot_factory=pilot_factory)
    registry = GameServerBindingRegistry(loaded.resolver, loaded.binding_ids)
    if config.live_catalog_root is not None:
        from .live_binding_catalog import LiveBindingRegistry
        registry = LiveBindingRegistry(config.live_catalog_root, pilot_factory=pilot_factory,
                                       allowed_pilots=registry.pilot_fingerprints)

    def seat(player_id, profile_id, game_id=None):
        recording = None
        if game_capture is not None:
            if game_id is None:
                raise PilotContractError("local recording requires native game identity")
            recording = game_capture.seat_sink(game_id, player_id)

        def finished(event):
            if recording is not None:
                recording.finished(event)
            if provenance_sink is not None:
                provenance_sink(player_id, event)

        return registry.create_seat(player_id, profile_id, provenance_sink=finished,
                                    decision_start_sink=None if recording is None else recording.started)

    stopping = threading.Event()
    connections = set()
    connection_lock = threading.Lock()

    class LocalServer(GameServerSidecarServer):
        daemon_threads = False  # Reap cancelled inference before the service process exits.

        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(5)  # Bound incomplete HTTP request bodies on shutdown.
            with connection_lock:
                if stopping.is_set():
                    connection.close()
                    raise OSError("local service stopping")
                connections.add(connection)
            return connection, address

        def process_request_thread(self, request, client_address):
            try:
                super().process_request_thread(request, client_address)
            finally:
                with connection_lock:
                    connections.discard(request)

        def server_close(self):
            stopping.set()
            # Inactivity timeouts alone do not bound a trickling partial body.
            # Close every accepted connection before joining callback threads.
            with connection_lock:
                for connection in connections:
                    try:
                        connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
            super().server_close()

    return LocalServer(
        (config.sidecar.bind_host, config.sidecar.port), config.sidecar,
        profiles=registry.profile_payloads(), profile_seat_factory=seat,
        profiles_supplier=registry.profile_payloads if config.live_catalog_root is not None else None,
        profile_exists=registry.contains if config.live_catalog_root is not None else None,
        game_profile_seat_factory=None if game_capture is None else lambda game, player, profile: seat(player, profile, game),
        callback_context=getattr(game_capture, "callback_context", None),
        connection_context=lambda connection: local_callback_context(connection, stopping),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inference-config", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--instance-root", type=Path, required=True)
    parser.add_argument("--live-catalog-root", type=Path)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    # Explicit input only; no provider credential, SDK endpoint or proxy environment is consumed.
    raw = json.loads(args.inference_config.read_text())
    if not isinstance(raw, dict) or raw.get("provider") != "lmstudio":
        raise ValueError("inference configuration must explicitly select lmstudio")
    config = LocalBindingConfig(
        GameServerSidecarConfig(os.environ["COMMANDER_GYM_LOCAL_SIDECAR_TOKEN"], port=args.port,
                               require_manual_human_game=True),
        LMStudioConfig(**raw), args.catalog, args.instance_root, args.live_catalog_root,
    )
    from .recorder_bridge import RemoteCaptureSink
    capture = RemoteCaptureSink(Path(os.environ["COMMANDER_GYM_RECORDER_SOCKET"]),
                                recorder_uid=int(os.environ["COMMANDER_GYM_RECORDER_UID"]))
    server = build_binding_lmstudio_sidecar(config, game_capture=capture)
    def terminate(_signum, _frame):
        # Leave serve_forever on its owning thread, then cancel/reap callbacks.
        raise KeyboardInterrupt
    previous_term = signal.signal(signal.SIGTERM, terminate)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        signal.signal(signal.SIGTERM, previous_term)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
