"""Transport-independent Commander Gym orchestration contract.

This module defines the durable control-plane seam for persistent Argentum
instances.  It deliberately mirrors Argentum-owned environment operations without
embedding HTTP, GitHub Actions, tunnel, or rules-engine semantics.

Concrete transports (the current :class:`ArgentumGymClient`, a future direct RPC
client, or another authenticated adapter) can satisfy :class:`OrchestrationBackend`
structurally.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence


class OrchestrationError(RuntimeError):
    """Base class for orchestration-contract failures."""


class OrchestrationCompatibilityError(OrchestrationError):
    """Raised when the connected Argentum instance is incompatible or unhealthy."""


class OrchestrationReconciliationError(OrchestrationError):
    """Raised when authoritative state cannot resolve an uncertain mutation."""


class OrchestrationBackend(Protocol):
    """Minimal transport-neutral backend required by the orchestration layer.

    Implementations remain responsible for their own authentication and transport
    security.  Mutating implementations must fail closed when delivery state is
    unknown; :class:`ArgentumGymClient` already does so.
    """

    def health(self) -> Mapping[str, Any]: ...

    def status(self) -> Mapping[str, Any]: ...

    def schema_hash(self) -> Mapping[str, Any]: ...

    def list_envs(self) -> Sequence[str]: ...

    def create_env(self, config: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def observe_env(self, env_id: str, *, reveal_all: bool | None = None) -> Mapping[str, Any]: ...

    def step_env(
        self,
        env_id: str,
        action_id: int,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]: ...

    def submit_decision(self, env_id: str, response: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def dispose_envs(self, env_ids: Sequence[str]) -> None: ...


@dataclass(frozen=True)
class ServerIdentity:
    """Authoritative engine identity observed at the orchestration boundary."""

    service: str
    schema_hash: str
    build_revision: str


@dataclass(frozen=True)
class OrchestrationInspection:
    """Compatibility evidence gathered before mutating a remote environment."""

    identity: ServerIdentity
    health_status: str


@dataclass(frozen=True)
class EnvironmentReconciliation:
    """Authoritative result of reconciling one known environment id."""

    env_id: str
    exists: bool
    observation: Mapping[str, Any] | None


@dataclass(frozen=True)
class CreationReconciliation:
    """Authoritative result when one uncertain create can be uniquely identified."""

    env_id: str
    observation: Mapping[str, Any]


class ArgentumOrchestrator:
    """Stable Commander Gym-facing orchestration facade.

    The facade contains no transport lifecycle assumptions.  In particular it does
    not know whether its backend is local HTTP, authenticated HTTPS, a custom RPC
    integration, or the temporary GitHub relay.  It performs compatibility checks,
    delegates Argentum-native operations, and provides explicit reconciliation
    helpers instead of retrying uncertain mutations.
    """

    def __init__(
        self,
        backend: OrchestrationBackend,
        *,
        expected_service: str = "argentum-gym-server",
        expected_schema_hash: str | None = None,
        expected_build_revision: str | None = None,
    ) -> None:
        if not expected_service:
            raise OrchestrationCompatibilityError("expected_service must not be blank")
        self.backend = backend
        self.expected_service = expected_service
        self.expected_schema_hash = expected_schema_hash
        self.expected_build_revision = expected_build_revision

    def inspect(self) -> OrchestrationInspection:
        """Verify health plus service/schema/build compatibility before mutations."""

        health = self.backend.health()
        status = self.backend.status()
        schema = self.backend.schema_hash()

        health_status = _required_string(health, "status", "health")
        if health_status != "ok":
            raise OrchestrationCompatibilityError(
                f"Argentum health status must be 'ok', got {health_status!r}"
            )

        service = _required_string(status, "service", "status")
        status_schema = _required_string(status, "schemaHash", "status")
        build_revision = _required_string(status, "buildRevision", "status")
        endpoint_schema = _required_string(schema, "schemaHash", "schema-hash")

        if status_schema != endpoint_schema:
            raise OrchestrationCompatibilityError(
                "Argentum status/schema-hash endpoints disagree: "
                f"{status_schema!r} != {endpoint_schema!r}"
            )
        if service != self.expected_service:
            raise OrchestrationCompatibilityError(
                f"unexpected Argentum service {service!r}; expected {self.expected_service!r}"
            )
        if self.expected_schema_hash is not None and status_schema != self.expected_schema_hash:
            raise OrchestrationCompatibilityError(
                f"unexpected Argentum schema {status_schema!r}; "
                f"expected {self.expected_schema_hash!r}"
            )
        if (
            self.expected_build_revision is not None
            and build_revision != self.expected_build_revision
        ):
            raise OrchestrationCompatibilityError(
                f"unexpected Argentum build {build_revision!r}; "
                f"expected {self.expected_build_revision!r}"
            )

        return OrchestrationInspection(
            identity=ServerIdentity(
                service=service,
                schema_hash=status_schema,
                build_revision=build_revision,
            ),
            health_status=health_status,
        )

    def list_environments(self) -> tuple[str, ...]:
        env_ids = self.backend.list_envs()
        if not all(isinstance(env_id, str) and env_id for env_id in env_ids):
            raise OrchestrationError("backend returned an invalid environment id list")
        return tuple(env_ids)

    def create_environment(self, config: Mapping[str, Any]) -> Mapping[str, Any]:
        created = self.backend.create_env(config)
        _required_string(created, "envId", "create")
        return created

    def observe_environment(
        self, env_id: str, *, reveal_all: bool | None = None
    ) -> Mapping[str, Any]:
        return self.backend.observe_env(env_id, reveal_all=reveal_all)

    def step_environment(
        self,
        env_id: str,
        action_id: int,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        return self.backend.step_env(env_id, action_id, params=params)

    def submit_decision(
        self, env_id: str, response: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        return self.backend.submit_decision(env_id, response)

    def dispose_environment(self, env_id: str) -> None:
        self.backend.dispose_envs([env_id])

    def reconcile_environment(self, env_id: str) -> EnvironmentReconciliation:
        """Re-read authoritative state after an uncertain step/decision/dispose.

        This method never retries the mutation.  It only asks Argentum what exists
        now, so callers can decide their next action from authoritative state.
        """

        env_ids = self.list_environments()
        if env_id not in env_ids:
            return EnvironmentReconciliation(env_id=env_id, exists=False, observation=None)
        return EnvironmentReconciliation(
            env_id=env_id,
            exists=True,
            observation=self.observe_environment(env_id),
        )

    def reconcile_creation(self, before_env_ids: Sequence[str]) -> CreationReconciliation:
        """Resolve an uncertain create only when exactly one new env is identifiable.

        Callers should snapshot ``list_environments()`` before creation.  If delivery
        becomes unknown, this helper compares current authoritative ids with that
        snapshot.  Zero or multiple new ids fail closed rather than guessing.
        """

        before = set(before_env_ids)
        after = set(self.list_environments())
        new_ids = sorted(after - before)
        if len(new_ids) != 1:
            raise OrchestrationReconciliationError(
                "uncertain create could not be uniquely reconciled; "
                f"found {len(new_ids)} new environments"
            )
        env_id = new_ids[0]
        return CreationReconciliation(
            env_id=env_id,
            observation=self.observe_environment(env_id),
        )


def _required_string(payload: Mapping[str, Any], key: str, source: str) -> str:
    if not isinstance(payload, Mapping):
        raise OrchestrationCompatibilityError(f"{source} response must be an object")
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise OrchestrationCompatibilityError(
            f"{source} response must contain non-empty string {key!r}"
        )
    return value
