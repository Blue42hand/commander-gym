"""Binding-first production launcher for the OpenAI-backed game-server sidecar.

This module composes the existing OpenAI policy runtime with the canonical Binding
resolver and the generic Argentum controller-profile bridge. Instance data is loaded
only from an explicitly supplied catalog/root; no owner-specific manifest is embedded
in public Commander Gym.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .binding_catalog import BindingCatalogError, load_binding_catalog
from .game_server_bindings import GameServerBindingError, GameServerBindingRegistry
from .game_server_openai_sidecar import (
    JsonlSeatProvenanceWriter,
    OpenAIGameServerSidecarConfig,
    OpenAIGameServerSidecarConfigurationError,
    SeatProvenanceSink,
    openai_game_server_sidecar_from_environment,
)
from .game_server_sidecar import GameServerSidecarServer
from .identity import Binding, Pilot
from .openai_responses_pilot import OpenAIResponsesPilot
from .pilot import ArgentumActionChoice, ArgentumDecisionChoice, PilotChoice
from .pilot_routing import RoutingPilot


@dataclass(frozen=True)
class BindingOpenAIGameServerConfig:
    """Production OpenAI sidecar config plus instance-owned Binding catalog paths."""

    sidecar: OpenAIGameServerSidecarConfig
    catalog_path: Path
    instance_root: Path


def binding_openai_game_server_config_from_environment(
    environment: Mapping[str, str],
) -> BindingOpenAIGameServerConfig:
    """Load Binding-first sidecar configuration from an explicit environment."""

    sidecar = openai_game_server_sidecar_from_environment(environment)
    raw_catalog = environment.get("COMMANDER_GYM_BINDING_CATALOG", "").strip()
    if not raw_catalog:
        raise OpenAIGameServerSidecarConfigurationError(
            "COMMANDER_GYM_BINDING_CATALOG must not be blank"
        )
    catalog_path = Path(raw_catalog).expanduser().resolve()

    raw_root = environment.get("COMMANDER_GYM_INSTANCE_ROOT", "").strip()
    instance_root = (
        Path(raw_root).expanduser().resolve()
        if raw_root
        else catalog_path.parent
    )
    try:
        catalog_path.relative_to(instance_root)
    except ValueError as exc:
        raise OpenAIGameServerSidecarConfigurationError(
            "COMMANDER_GYM_BINDING_CATALOG must be below COMMANDER_GYM_INSTANCE_ROOT"
        ) from exc
    return BindingOpenAIGameServerConfig(
        sidecar=sidecar,
        catalog_path=catalog_path,
        instance_root=instance_root,
    )


@dataclass(frozen=True)
class _CanonicalBindingPilot:
    """Attach exact canonical identity to choices made by the configured OpenAI runtime."""

    delegate: RoutingPilot
    pilot: Pilot
    binding: Binding

    @property
    def name(self) -> str:
        return self.pilot.pilot_id

    @property
    def version(self) -> str:
        return self.pilot.revision

    def choose(self, observation: Mapping[str, Any]) -> PilotChoice:
        choice = self.delegate.choose(observation)
        identity = {
            "binding": self.binding.ref().to_dict(),
            "pilot": self.pilot.ref().to_dict(),
        }
        if isinstance(choice, ArgentumActionChoice):
            return ArgentumActionChoice(
                action_id=choice.action_id,
                params=choice.params,
                metadata={**dict(choice.metadata), **identity},
            )
        if isinstance(choice, ArgentumDecisionChoice):
            return ArgentumDecisionChoice(
                response=choice.response,
                metadata={**dict(choice.metadata), **identity},
            )
        raise OpenAIGameServerSidecarConfigurationError(
            "Binding OpenAI pilot returned an unsupported choice type"
        )


def _default_openai_client(config: OpenAIGameServerSidecarConfig) -> Any:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise OpenAIGameServerSidecarConfigurationError(
            "OpenAI SDK is not installed; install requirements-openai.txt"
        ) from exc
    return OpenAI(api_key=config.api_key, timeout=config.timeout)


def build_binding_openai_game_server_sidecar(
    config: BindingOpenAIGameServerConfig,
    *,
    client: Any | None = None,
    provenance_sink: SeatProvenanceSink | None = None,
) -> GameServerSidecarServer:
    """Build the Binding-only OpenAI sidecar from one instance-supplied catalog.

    The catalog is resolved eagerly through ``BindingResolver``. An explicit selected
    Binding therefore determines both the provider-advertised exact deck and the pilot
    instance used for policy callbacks. Unknown/stale profiles remain fail-closed in
    the existing game-server Binding bridge; this launcher never installs the legacy
    player-id-only seat factory.
    """

    if not isinstance(config, BindingOpenAIGameServerConfig):
        raise OpenAIGameServerSidecarConfigurationError(
            "config must be BindingOpenAIGameServerConfig"
        )
    provider_client = client if client is not None else _default_openai_client(config.sidecar)

    if provenance_sink is None and config.sidecar.provenance_path is not None:
        writer = JsonlSeatProvenanceWriter(config.sidecar.provenance_path)
        provenance_sink = writer.write

    def pilot_factory(pilot: Pilot, binding: Binding) -> _CanonicalBindingPilot:
        strategic = OpenAIResponsesPilot(
            client=provider_client,
            model=config.sidecar.model,
            max_attempts=config.sidecar.max_attempts,
        )
        return _CanonicalBindingPilot(
            delegate=RoutingPilot(strategic_pilot=strategic),
            pilot=pilot,
            binding=binding,
        )

    try:
        loaded = load_binding_catalog(
            config.catalog_path,
            instance_root=config.instance_root,
            pilot_factory=pilot_factory,
        )
        registry = GameServerBindingRegistry(loaded.resolver, loaded.binding_ids)
    except (BindingCatalogError, GameServerBindingError) as exc:
        raise OpenAIGameServerSidecarConfigurationError(str(exc)) from exc

    sidecar = config.sidecar.sidecar_config()

    def profile_seat_factory(player_id: str, profile_id: str):
        sink = (
            None
            if provenance_sink is None
            else lambda event, player_id=player_id: provenance_sink(player_id, event)
        )
        return registry.create_seat(
            player_id,
            profile_id,
            provenance_sink=sink,
        )

    return GameServerSidecarServer(
        (sidecar.bind_host, sidecar.port),
        sidecar,
        profiles=registry.profile_payloads(),
        profile_seat_factory=profile_seat_factory,
    )


def main() -> int:
    """Run the Binding-first OpenAI game-server policy sidecar until interrupted."""

    config = binding_openai_game_server_config_from_environment(os.environ)
    server = build_binding_openai_game_server_sidecar(config)
    print(
        "Commander Gym Binding game-server sidecar "
        f"listening on {config.sidecar.bind_host}:{server.server_port} "
        f"with model {config.sidecar.model} and catalog {config.catalog_path}",
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
