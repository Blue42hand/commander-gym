"""Binding-first production launcher for the OpenAI-backed game-server sidecar.

This module composes the existing OpenAI policy runtime with the canonical Binding
resolver and the generic Argentum controller-profile bridge. Instance data is loaded
only from an explicitly supplied catalog/root; no owner-specific manifest is embedded
in public Commander Gym.

A selected Binding's canonical Pilot is authoritative for runtime composition. The
production launcher resolves that Pilot's exact component graph through the #73
composition contract instead of attaching canonical identity to a process-global
fallback policy. Public built-ins are intentionally narrow: the certified forced-choice
handler and the OpenAI Responses frontier adapter. Any other declared component must be
provided by an explicit component resolver and otherwise fails closed at startup.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .binding_catalog import BindingCatalogError, load_binding_catalog
from .deck_package import ArtifactRef
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
from .pilot import (
    ArgentumActionChoice,
    ArgentumDecisionChoice,
    ArtificialPlayer,
    PilotChoice,
    PilotContractError,
)
from .pilot_composition import (
    ArtificialPlayerSubsystem,
    MechanicalHandlerSubsystem,
    PilotComponentResolver,
    PilotSubsystemSpec,
    compose_pilot_runtime,
)
from .pilot_routing import ForcedParameterlessChoiceHandler


# These refs identify the public executable adapter contracts, not a particular model
# weight revision. Exact provider/model request-response identity is already captured by
# the settled #53 model-I/O provenance. Bumping either adapter contract requires a new
# version/digest rather than silently reinterpreting an existing Pilot manifest.
BUILTIN_FORCED_PARAMETERLESS_COMPONENT_REF = ArtifactRef(
    kind="deterministic-policy",
    artifact_id="forced-parameterless-choice",
    version="1",
    digest="sha256:3c00dd57dbed45ceb9937fecbedd0d67f509d9619f8e565eae0eeed789f4ac38",
)
BUILTIN_OPENAI_RESPONSES_COMPONENT_REF = ArtifactRef(
    kind="provider",
    artifact_id="openai-responses",
    version="1",
    digest="sha256:5010883834fef52a65e49a2fc245ec9faa506badcae64ae5f16cdf65e70dac92",
)


def _component_key(ref: ArtifactRef) -> tuple[str, str, str, str | None]:
    ref.validate()
    return (ref.kind, ref.artifact_id, ref.version, ref.digest)


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
    """Attach exact canonical Binding identity to a composed Pilot's choices."""

    delegate: ArtificialPlayer
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


@dataclass(frozen=True)
class OpenAIBindingPilotComponentResolver:
    """Resolve the public built-in components executable by this production sidecar.

    The resolver never interprets a friendly component name as a substitute. Both
    adapter refs require the exact kind/id/version/digest above, and they are valid only
    in their declared routing roles. Skills, specialists, and local models remain
    replaceable by supplying an explicit ``PilotComponentResolver`` to the sidecar
    builder; an unregistered component fails startup rather than falling through to the
    process-configured OpenAI pilot.
    """

    config: OpenAIGameServerSidecarConfig
    client: Any

    def resolve(self, spec: PilotSubsystemSpec):
        key = spec.component_key()
        if (
            spec.role == "deterministic"
            and key == _component_key(BUILTIN_FORCED_PARAMETERLESS_COMPONENT_REF)
        ):
            return MechanicalHandlerSubsystem(ForcedParameterlessChoiceHandler())
        if (
            spec.role == "frontier_escalation"
            and key == _component_key(BUILTIN_OPENAI_RESPONSES_COMPONENT_REF)
        ):
            return ArtificialPlayerSubsystem(
                OpenAIResponsesPilot(
                    client=self.client,
                    model=self.config.model,
                    max_attempts=self.config.max_attempts,
                )
            )
        raise PilotContractError(
            "missing exact Binding Pilot component for "
            f"role={spec.role} artifact={spec.ref.artifact_id!r} "
            f"version={spec.ref.version!r} digest={spec.ref.digest!r}"
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
    component_resolver: PilotComponentResolver | None = None,
) -> GameServerSidecarServer:
    """Build the Binding-only OpenAI sidecar from one instance-supplied catalog.

    The catalog is resolved eagerly through ``BindingResolver``. An explicit selected
    Binding therefore determines the provider-advertised exact deck and the exact
    canonical Pilot runtime used for policy callbacks. The Pilot is always executed
    through ``compose_pilot_runtime``; there is no generic process-global Pilot fallback.

    By default this OpenAI launcher resolves only the public built-in deterministic
    handler and OpenAI frontier adapter refs above. A caller may supply another exact
    ``PilotComponentResolver`` for skills, specialists, local models, or another
    provider without changing the Binding/seat seam. Unknown/stale components remain
    fail-closed during eager Binding resolution.
    """

    if not isinstance(config, BindingOpenAIGameServerConfig):
        raise OpenAIGameServerSidecarConfigurationError(
            "config must be BindingOpenAIGameServerConfig"
        )

    runtime_resolver = component_resolver
    if runtime_resolver is None:
        provider_client = client if client is not None else _default_openai_client(config.sidecar)
        runtime_resolver = OpenAIBindingPilotComponentResolver(
            config=config.sidecar,
            client=provider_client,
        )

    if provenance_sink is None and config.sidecar.provenance_path is not None:
        writer = JsonlSeatProvenanceWriter(config.sidecar.provenance_path)
        provenance_sink = writer.write

    def pilot_factory(pilot: Pilot, binding: Binding) -> _CanonicalBindingPilot:
        runtime = compose_pilot_runtime(pilot, runtime_resolver)
        return _CanonicalBindingPilot(
            delegate=runtime,
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
    except (BindingCatalogError, GameServerBindingError, PilotContractError) as exc:
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
