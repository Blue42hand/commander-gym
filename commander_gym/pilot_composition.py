"""Canonical runtime composition for one versioned Commander Gym Pilot.

The canonical :class:`commander_gym.identity.Pilot` already owns the stable component
identities.  This module turns those identities into one ordered artificial-player
runtime without changing the Argentum-facing :class:`ArtificialPlayer` contract.

Routing is deliberately explicit and fail-closed.  Initial foundation routing uses a
static role order rather than model self-reported confidence:

1. certified deterministic handler;
2. executable skill/macro;
3. learned specialist/value-policy adapters in manifest order;
4. local generalist;
5. frontier/cloud escalation.

Each attempted subsystem returns either one normal ``PilotChoice`` or an explicit defer
reason.  The selected choice is annotated with the exact component that handled the
decision plus the complete preceding escalation path, which is already carried into
#53 decision provenance through ``pilot_metadata``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence

from .deck_package import ArtifactRef
from .identity import Pilot as PilotIdentity
from .pilot import ArtificialPlayer, PilotChoice, PilotContractError
from .pilot_routing import CertifiedMechanicalHandler, _annotate

PILOT_ROUTING_MODE_STATIC_ORDER_V1 = "static-order-v1"
PILOT_ROUTING_PROVENANCE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class PilotSubsystemSpec:
    """One exact canonical Pilot component at one position in the routing order."""

    role: str
    ordinal: int
    ref: ArtifactRef

    def component_key(self) -> tuple[str, str, str, str | None]:
        self.ref.validate()
        return (
            self.ref.kind,
            self.ref.artifact_id,
            self.ref.version,
            self.ref.digest,
        )

    def provenance_ref(self) -> dict[str, Any]:
        self.ref.validate()
        return {
            "kind": self.ref.kind,
            "artifactId": self.ref.artifact_id,
            "version": self.ref.version,
            "digest": self.ref.digest,
        }


def subsystem_specs_for_pilot(pilot: PilotIdentity) -> tuple[PilotSubsystemSpec, ...]:
    """Resolve the canonical ordered subsystem plan encoded by one ``Pilot`` revision.

    ``routing_config.mode`` is intentionally narrow for the foundation milestone.  A
    later empirically gated router can add another explicit mode/version without
    silently changing the meaning of existing Pilot revisions.
    """

    if not isinstance(pilot, PilotIdentity):
        raise PilotContractError("pilot composition requires canonical identity.Pilot")
    pilot.validate()

    mode = pilot.routing_config.get("mode", PILOT_ROUTING_MODE_STATIC_ORDER_V1)
    if mode != PILOT_ROUTING_MODE_STATIC_ORDER_V1:
        raise PilotContractError(
            f"unsupported pilot routing mode {mode!r}; expected "
            f"{PILOT_ROUTING_MODE_STATIC_ORDER_V1!r}"
        )

    specs: list[PilotSubsystemSpec] = []

    def add(role: str, ref: ArtifactRef | None) -> None:
        if ref is not None:
            specs.append(PilotSubsystemSpec(role=role, ordinal=len(specs), ref=ref))

    add("deterministic", pilot.deterministic_policy)
    add("skill", pilot.skill_set)
    for specialist in pilot.specialists:
        add("specialist", specialist)
    add("local_generalist", pilot.generalist_base)
    add("frontier_escalation", pilot.escalation_provider)

    if not specs:
        raise PilotContractError("pilot revision contains no executable decision subsystem")
    return tuple(specs)


@dataclass(frozen=True)
class SubsystemDecision:
    """Result of one routing attempt.

    A subsystem either handles the decision with one normal ``PilotChoice`` or defers
    with an explicit reason.  Deferral never fabricates a gameplay action.
    """

    choice: PilotChoice | None = None
    reason: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not isinstance(self.metadata, Mapping):
            raise PilotContractError("subsystem decision metadata must be a mapping")
        if self.choice is None:
            if not isinstance(self.reason, str) or not self.reason.strip():
                raise PilotContractError(
                    "a deferred pilot subsystem must provide an explicit reason"
                )
        elif self.reason not in (None, ""):
            raise PilotContractError(
                "a handled pilot subsystem must not also report a defer reason"
            )


class PilotDecisionSubsystem(Protocol):
    """Replaceable decision subsystem used inside one composed Pilot."""

    name: str
    version: str

    def try_choose(self, observation: Mapping[str, Any]) -> SubsystemDecision:
        ...


@dataclass(frozen=True)
class MechanicalHandlerSubsystem:
    """Adapter for an existing certified deterministic handler."""

    handler: CertifiedMechanicalHandler
    defer_reason: str = "not-certified-for-decision"

    @property
    def name(self) -> str:
        return self.handler.name

    @property
    def version(self) -> str:
        return self.handler.version

    def try_choose(self, observation: Mapping[str, Any]) -> SubsystemDecision:
        choice = self.handler.choose(observation)
        if choice is None:
            return SubsystemDecision(reason=self.defer_reason)
        return SubsystemDecision(choice=choice)


@dataclass(frozen=True)
class ArtificialPlayerSubsystem:
    """Terminal adapter for any ordinary ArtificialPlayer implementation.

    This is suitable for a local generalist or frontier provider.  Conditional skills
    and specialists should implement :class:`PilotDecisionSubsystem` directly so their
    applicability/defer reason is explicit and testable.
    """

    player: ArtificialPlayer

    @property
    def name(self) -> str:
        return str(getattr(self.player, "name", ""))

    @property
    def version(self) -> str:
        return str(getattr(self.player, "version", ""))

    def try_choose(self, observation: Mapping[str, Any]) -> SubsystemDecision:
        return SubsystemDecision(choice=self.player.choose(observation))


class PilotComponentResolver(Protocol):
    """Resolve one exact canonical component reference to executable machinery."""

    def resolve(self, spec: PilotSubsystemSpec) -> PilotDecisionSubsystem:
        ...


class PilotComponentRegistry:
    """Small exact-identity registry useful for public/runtime composition.

    Registry lookup includes the artifact digest when one exists.  A stale or missing
    component therefore fails closed instead of silently substituting another revision.
    """

    def __init__(
        self,
        components: Mapping[
            tuple[str, str, str, str | None], PilotDecisionSubsystem
        ],
    ) -> None:
        self._components = dict(components)

    @staticmethod
    def key_for(ref: ArtifactRef) -> tuple[str, str, str, str | None]:
        ref.validate()
        return (ref.kind, ref.artifact_id, ref.version, ref.digest)

    def resolve(self, spec: PilotSubsystemSpec) -> PilotDecisionSubsystem:
        component = self._components.get(spec.component_key())
        if component is None:
            raise PilotContractError(
                "missing exact pilot component for "
                f"role={spec.role} artifact={spec.ref.artifact_id!r} "
                f"version={spec.ref.version!r} digest={spec.ref.digest!r}"
            )
        name = getattr(component, "name", None)
        version = getattr(component, "version", None)
        if not isinstance(name, str) or not name:
            raise PilotContractError("resolved pilot subsystem must expose non-empty name")
        if not isinstance(version, str) or not version:
            raise PilotContractError("resolved pilot subsystem must expose non-empty version")
        return component


@dataclass(frozen=True)
class ResolvedPilotSubsystem:
    spec: PilotSubsystemSpec
    implementation: PilotDecisionSubsystem


@dataclass(frozen=True)
class ComposedPilot:
    """One canonical Pilot revision executed through an ordered subsystem route."""

    pilot: PilotIdentity
    subsystems: Sequence[ResolvedPilotSubsystem]

    @property
    def name(self) -> str:
        return self.pilot.pilot_id

    @property
    def version(self) -> str:
        return self.pilot.revision

    def choose(self, observation: Mapping[str, Any]) -> PilotChoice:
        attempts: list[dict[str, Any]] = []
        escalation_reasons: list[str] = []

        for resolved in self.subsystems:
            spec = resolved.spec
            implementation = resolved.implementation
            result = implementation.try_choose(observation)
            if not isinstance(result, SubsystemDecision):
                raise PilotContractError(
                    f"pilot subsystem {implementation.name!r} returned unsupported result"
                )
            result.validate()

            attempt = {
                "ordinal": spec.ordinal,
                "role": spec.role,
                "component": spec.provenance_ref(),
                "implementation": implementation.name,
                "implementationVersion": implementation.version,
                "metadata": dict(result.metadata),
            }

            if result.choice is None:
                reason = str(result.reason)
                attempt["status"] = "deferred"
                attempt["reason"] = reason
                attempts.append(attempt)
                escalation_reasons.append(reason)
                continue

            attempt["status"] = "handled"
            attempts.append(attempt)
            routing = {
                "schemaVersion": PILOT_ROUTING_PROVENANCE_SCHEMA_VERSION,
                "mode": PILOT_ROUTING_MODE_STATIC_ORDER_V1,
                "pilotId": self.pilot.pilot_id,
                "pilotRevision": self.pilot.revision,
                "handledBy": {
                    "ordinal": spec.ordinal,
                    "role": spec.role,
                    "component": spec.provenance_ref(),
                    "implementation": implementation.name,
                    "implementationVersion": implementation.version,
                },
                "attempts": attempts,
                "escalated": bool(escalation_reasons),
                "escalationReasons": tuple(escalation_reasons),
            }
            return _annotate(result.choice, routing)

        raise PilotContractError(
            "all configured pilot subsystems deferred; no gameplay choice was fabricated"
        )


def compose_pilot_runtime(
    pilot: PilotIdentity,
    resolver: PilotComponentResolver,
) -> ComposedPilot:
    """Resolve one exact canonical Pilot revision into an executable runtime."""

    specs = subsystem_specs_for_pilot(pilot)
    resolved: list[ResolvedPilotSubsystem] = []
    for spec in specs:
        implementation = resolver.resolve(spec)
        resolved.append(ResolvedPilotSubsystem(spec=spec, implementation=implementation))
    return ComposedPilot(pilot=pilot, subsystems=tuple(resolved))
