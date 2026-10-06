"""Binding-first production launcher for the OpenAI-backed game-server sidecar.

This module composes the existing OpenAI policy runtime with the canonical Binding
resolver and the generic Argentum controller-profile bridge. Instance data is loaded
only from an explicitly supplied catalog/root; no owner-specific manifest is embedded
in public Commander Gym.

A selected Binding's canonical Pilot is authoritative for runtime composition. The
production launcher resolves that Pilot's exact component graph through the #73
composition contract instead of attaching canonical identity to a process-global
fallback policy. Each built-in policy has an exact component reference; any other
declared component requires an explicit resolver and otherwise fails closed at startup.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .binding_catalog import BindingCatalogError, load_binding_catalog
from .bounded_provider_process import BoundedProcessClient
from .deck_package import ArtifactRef
from .delegated_autopass import DelegatedAutopassPilot
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
from .openai_run_budget import OpenAIRunBudget
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
from .pilot_routing import (
    AllUnaffordablePassHandler, ForcedParameterlessChoiceHandler, NativeNoChoiceHandler,
    StandingManaOnlyPassHandler,
)


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
BUILTIN_NATIVE_NO_CHOICE_COMPONENT_REF = ArtifactRef(
    kind="deterministic-policy",
    artifact_id="native-no-choice",
    version="3",
    digest="sha256:16580bbe6cf9b073f02083a5480bf3f376d1250f438ab02ed0414ca5d70a272a",
)
BUILTIN_STANDING_MANA_ONLY_PASS_COMPONENT_REF = ArtifactRef(
    kind="deterministic-policy",
    artifact_id="standing-mana-only-pass",
    version="1",
    digest="sha256:727132e3a10fd28a551f62324f96e0d0d943560bdb7933c9d6edf9afa4e5c2bd",
)
BUILTIN_NATIVE_UNAFFORDABLE_PASS_COMPONENT_REF = ArtifactRef(
    kind="deterministic-policy",
    artifact_id="native-unaffordable-ability-pass",
    version="2",
    digest="sha256:989156518fba4374673e3218fb9c538898ecef79ea2f02c791c793e1765db443",
)
BUILTIN_ALL_UNAFFORDABLE_PASS_COMPONENT_REF = ArtifactRef(
    kind="deterministic-policy",
    artifact_id="native-all-unaffordable-pass",
    version="1",
    digest="sha256:9e54a77e5614f433be4feaa62fba42dca8e886f762303a69c15b69df50c896eb",
)
BUILTIN_ALL_UNAFFORDABLE_CYCLE_PASS_COMPONENT_REF = ArtifactRef(
    kind="deterministic-policy",
    artifact_id="native-all-unaffordable-pass",
    version="2",
    digest="sha256:5a85e6b3bcc8ca8c0c76c29d4656a68fc97d049e0693f53645e7c1e70e2d6433",
)
BUILTIN_OPENAI_RESPONSES_COMPONENT_REF = ArtifactRef(
    kind="provider",
    artifact_id="openai-responses",
    version="1",
    digest="sha256:5010883834fef52a65e49a2fc245ec9faa506badcae64ae5f16cdf65e70dac92",
)
BUILTIN_DELEGATED_AUTOPASS_COMPONENT_REF = ArtifactRef(
    kind="provider",
    artifact_id="openai-responses-delegated-autopass",
    version="1",
    digest="sha256:8f01e8269a20bd30dfab13a025972d7f8320b3fb9296a118d027b84d26487e0c",
)
BUILTIN_FORGE_CONDITIONAL_WAIT_COMPONENT_REF = ArtifactRef(
    kind="provider",
    artifact_id="openai-responses-forge-conditional-wait",
    version="2",
    digest="sha256:339686cd2684e701162b5d73fe0f6a91e0958217466d1a914fc1dc215436dca1",
)
BUILTIN_FORGE_CONDITIONAL_WAIT_V3_COMPONENT_REF = ArtifactRef(
    kind="provider",
    artifact_id="openai-responses-forge-conditional-wait",
    version="3",
    digest="sha256:668ec1bf45106b1debc850940b2703bf01017d1c6806f0a9a938df75a7c79a46",
)
BUILTIN_FORGE_CONDITIONAL_WAIT_COMPACT_COMPONENT_REF = ArtifactRef(
    kind="provider",
    artifact_id="openai-responses-forge-conditional-wait",
    version="4",
    digest="sha256:cdb76ab2dac1ec60961b4e75b96b64c2943d1391888872e8f38e5bdd878563a4",
)
BUILTIN_FORGE_GUARDED_THEN_CAST_COMPONENT_REF = ArtifactRef(
    kind="provider",
    artifact_id="openai-responses-forge-conditional-wait",
    version="5",
    digest="sha256:9527c8a9234e9573711e5f4c03d9fe9d92b5226eb04d54322cab7a928487cf9a",
)
BUILTIN_FORGE_DECLARATIVE_CONTINUATION_COMPONENT_REF = ArtifactRef(
    kind="provider",
    artifact_id="openai-responses-forge-conditional-wait",
    version="6",
    digest="sha256:425ea6c84b91cd8a1ffae495c79214acaf2d7518f6a0f9db714c25ee634391e0",
)
BUILTIN_FORGE_BOUNDED_RECOVERY_COMPONENT_REF = ArtifactRef(
    kind="provider",
    artifact_id="openai-responses-forge-conditional-wait",
    version="7",
    digest="sha256:c31e254cae2e22aa347022706296d6996dd09d78b232be157b2b832296dc342f",
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
    budget: OpenAIRunBudget | None = None
    cache_friendly_history: bool = False


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
    ledger_value = environment.get("COMMANDER_GYM_OPENAI_BUDGET_LEDGER")
    cap_value = environment.get("COMMANDER_GYM_OPENAI_BUDGET_CAP_USD")
    authorized_max_value = environment.get("COMMANDER_GYM_OPENAI_BUDGET_AUTHORIZED_MAX_USD")
    request_limit_value = environment.get("COMMANDER_GYM_OPENAI_BUDGET_MAX_REQUESTS")
    if bool(ledger_value) != bool(cap_value):
        raise OpenAIGameServerSidecarConfigurationError(
            "OpenAI budget ledger and cap must be configured together"
        )
    if not ledger_value and (authorized_max_value or request_limit_value):
        raise OpenAIGameServerSidecarConfigurationError(
            "OpenAI budget ceiling and request limit require a ledger and cap"
        )
    try:
        cap_usd = float(cap_value) if cap_value else None
        authorized_max_usd = float(authorized_max_value) if authorized_max_value else 5.0
        max_requests = int(request_limit_value) if request_limit_value else None
    except ValueError as exc:
        raise OpenAIGameServerSidecarConfigurationError(
            "OpenAI budget cap, ceiling, and request limit must be numeric"
        ) from exc
    if cap_usd is not None and cap_usd > 5 and max_requests is None:
        raise OpenAIGameServerSidecarConfigurationError(
            "a budget above $5 requires an absolute request limit"
        )
    cache_value = environment.get("COMMANDER_GYM_CACHE_FRIENDLY_HISTORY", "false").lower()
    if cache_value not in ("true", "false"):
        raise OpenAIGameServerSidecarConfigurationError(
            "COMMANDER_GYM_CACHE_FRIENDLY_HISTORY must be true or false"
        )
    return BindingOpenAIGameServerConfig(
        sidecar=sidecar,
        catalog_path=catalog_path,
        instance_root=instance_root,
        budget=(
            OpenAIRunBudget(
                Path(environment["COMMANDER_GYM_OPENAI_BUDGET_LEDGER"]).expanduser().resolve(),
                cap_usd, authorized_max_usd=authorized_max_usd,
                max_requests=max_requests,
            )
            if ledger_value and cap_value
            else None
        ),
        cache_friendly_history=cache_value == "true",
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
    budget: OpenAIRunBudget | None = None
    cache_friendly_history: bool = False
    bounded_recovery_client: Any | None = None

    def resolve(self, spec: PilotSubsystemSpec):
        key = spec.component_key()
        if (
            spec.role == "deterministic"
            and key == _component_key(BUILTIN_FORCED_PARAMETERLESS_COMPONENT_REF)
        ):
            return MechanicalHandlerSubsystem(ForcedParameterlessChoiceHandler())
        if (
            spec.role == "deterministic"
            and key == _component_key(BUILTIN_NATIVE_NO_CHOICE_COMPONENT_REF)
        ):
            return MechanicalHandlerSubsystem(NativeNoChoiceHandler())
        if (
            spec.role == "deterministic"
            and key == _component_key(BUILTIN_STANDING_MANA_ONLY_PASS_COMPONENT_REF)
        ):
            return MechanicalHandlerSubsystem(StandingManaOnlyPassHandler())
        if (
            spec.role == "deterministic"
            and key == _component_key(BUILTIN_NATIVE_UNAFFORDABLE_PASS_COMPONENT_REF)
        ):
            return MechanicalHandlerSubsystem(StandingManaOnlyPassHandler(
                name="native-unaffordable-ability-pass", version="2",
                ignore_unaffordable_abilities=True,
            ))
        if (
            spec.role == "deterministic"
            and key == _component_key(BUILTIN_ALL_UNAFFORDABLE_PASS_COMPONENT_REF)
        ):
            return MechanicalHandlerSubsystem(AllUnaffordablePassHandler())
        if (
            spec.role == "deterministic"
            and key == _component_key(BUILTIN_ALL_UNAFFORDABLE_CYCLE_PASS_COMPONENT_REF)
        ):
            return MechanicalHandlerSubsystem(AllUnaffordablePassHandler(
                version="2", allow_unaffordable_cycling=True,
            ))
        if (
            spec.role == "frontier_escalation"
            and key == _component_key(BUILTIN_OPENAI_RESPONSES_COMPONENT_REF)
        ):
            return ArtificialPlayerSubsystem(
                OpenAIResponsesPilot(
                    client=self.client,
                    model=self.config.model,
                    max_attempts=self.config.max_attempts,
                    budget=self.budget,
                    cache_friendly_history=self.cache_friendly_history,
                )
            )
        if (
            spec.role == "frontier_escalation"
            and key == _component_key(BUILTIN_DELEGATED_AUTOPASS_COMPONENT_REF)
        ):
            return ArtificialPlayerSubsystem(
                DelegatedAutopassPilot(
                    OpenAIResponsesPilot(
                        client=self.client,
                        model=self.config.model,
                        max_attempts=self.config.max_attempts,
                        budget=self.budget,
                        cache_friendly_history=self.cache_friendly_history,
                        allow_priority_delegation=True,
                    )
                )
            )
        if (
            spec.role == "frontier_escalation"
            and key == _component_key(BUILTIN_FORGE_CONDITIONAL_WAIT_COMPONENT_REF)
        ):
            return ArtificialPlayerSubsystem(
                DelegatedAutopassPilot(
                    OpenAIResponsesPilot(
                        client=self.client,
                        model=self.config.model,
                        max_attempts=self.config.max_attempts,
                        budget=self.budget,
                        cache_friendly_history=self.cache_friendly_history,
                        allow_priority_delegation=True,
                        allow_named_deferrals=True,
                    ),
                    name="forge-conditional-wait", version="2",
                    allow_named_deferrals=True,
                )
            )
        if (
            spec.role == "frontier_escalation"
            and key == _component_key(BUILTIN_FORGE_CONDITIONAL_WAIT_V3_COMPONENT_REF)
        ):
            return ArtificialPlayerSubsystem(
                DelegatedAutopassPilot(
                    OpenAIResponsesPilot(
                        client=self.client,
                        model=self.config.model,
                        max_attempts=self.config.max_attempts,
                        budget=self.budget,
                        cache_friendly_history=self.cache_friendly_history,
                        allow_priority_delegation=True,
                        allow_named_deferrals=True,
                        require_nonempty_named_deferrals=True,
                    ),
                    name="forge-conditional-wait", version="3",
                    allow_named_deferrals=True,
                )
            )
        if (
            spec.role == "frontier_escalation"
            and key == _component_key(BUILTIN_FORGE_CONDITIONAL_WAIT_COMPACT_COMPONENT_REF)
        ):
            return ArtificialPlayerSubsystem(
                DelegatedAutopassPilot(
                    OpenAIResponsesPilot(
                        client=self.client,
                        model=self.config.model,
                        max_attempts=self.config.max_attempts,
                        budget=self.budget,
                        cache_friendly_history=self.cache_friendly_history,
                        allow_priority_delegation=True,
                        allow_named_deferrals=True,
                        require_nonempty_named_deferrals=True,
                        compact_model_observation=True,
                    ),
                    name="forge-conditional-wait", version="3",
                    allow_named_deferrals=True,
                )
            )
        if (
            spec.role == "frontier_escalation"
            and key == _component_key(BUILTIN_FORGE_GUARDED_THEN_CAST_COMPONENT_REF)
        ):
            return ArtificialPlayerSubsystem(
                DelegatedAutopassPilot(
                    OpenAIResponsesPilot(
                        client=self.client,
                        model=self.config.model,
                        max_attempts=self.config.max_attempts,
                        budget=self.budget,
                        cache_friendly_history=self.cache_friendly_history,
                        allow_priority_delegation=True,
                        allow_named_deferrals=True,
                        require_nonempty_named_deferrals=True,
                        compact_model_observation=True,
                        guarded_then_cast_templates=True,
                    ),
                    name="forge-conditional-wait", version="4",
                    allow_named_deferrals=True,
                    guarded_then_cast_templates=True,
                )
            )
        if (
            spec.role == "frontier_escalation"
            and key == _component_key(BUILTIN_FORGE_DECLARATIVE_CONTINUATION_COMPONENT_REF)
        ):
            return ArtificialPlayerSubsystem(
                DelegatedAutopassPilot(
                    OpenAIResponsesPilot(
                        client=self.client,
                        model=self.config.model,
                        max_attempts=self.config.max_attempts,
                        budget=self.budget,
                        cache_friendly_history=self.cache_friendly_history,
                        allow_priority_delegation=True,
                        allow_named_deferrals=True,
                        require_nonempty_named_deferrals=True,
                        compact_model_observation=True,
                        guarded_then_cast_templates=True,
                        allow_declarative_continuation=True,
                    ),
                    name="forge-conditional-wait", version="5",
                    allow_named_deferrals=True,
                    guarded_then_cast_templates=True,
                    allow_declarative_continuation=True,
                )
            )
        if (
            spec.role == "frontier_escalation"
            and key == _component_key(BUILTIN_FORGE_BOUNDED_RECOVERY_COMPONENT_REF)
        ):
            if self.budget is None:
                raise PilotContractError("bounded provider recovery requires a durable budget")
            return ArtificialPlayerSubsystem(
                DelegatedAutopassPilot(
                    OpenAIResponsesPilot(
                        client=self.bounded_recovery_client or self.client,
                        model=self.config.model,
                        max_attempts=self.config.max_attempts,
                        budget=self.budget,
                        retry_transient_server_errors=True,
                        cache_friendly_history=self.cache_friendly_history,
                        allow_priority_delegation=True,
                        allow_named_deferrals=True,
                        require_nonempty_named_deferrals=True,
                        compact_model_observation=True,
                        guarded_then_cast_templates=True,
                        allow_declarative_continuation=True,
                    ),
                    name="forge-conditional-wait", version="6",
                    allow_named_deferrals=True,
                    guarded_then_cast_templates=True,
                    allow_declarative_continuation=True,
                )
            )
        raise PilotContractError(
            "missing exact Binding Pilot component for "
            f"role={spec.role} artifact={spec.ref.artifact_id!r} "
            f"version={spec.ref.version!r} digest={spec.ref.digest!r}"
        )


def _default_openai_client(config: OpenAIGameServerSidecarConfig, *, bounded: bool) -> Any:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise OpenAIGameServerSidecarConfigurationError(
            "OpenAI SDK is not installed; install requirements-openai.txt"
        ) from exc
    return OpenAI(api_key=config.api_key, timeout=config.timeout, max_retries=0 if bounded else 2)


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
        provider_client = client if client is not None else _default_openai_client(
            config.sidecar, bounded=config.budget is not None,
        )
        runtime_resolver = OpenAIBindingPilotComponentResolver(
            config=config.sidecar,
            client=provider_client,
            budget=config.budget,
            cache_friendly_history=config.cache_friendly_history,
            bounded_recovery_client=(
                BoundedProcessClient(config.sidecar.api_key)
                if client is None and config.budget is not None else None
            ),
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
