"""Idle-only manual transactions through a protected host implementation.

The keyless updater remains a separate delegate. A manual-active runtime cannot
be implicitly replaced by that delegate or by an unapproved newer candidate.
All filesystem/service/credential effects belong to host methods and are called
only after exact precredential validation and an idle recording barrier.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import Protocol

from .manual_runtime_profile import (
    ManualRuntimeError, ValidatedRuntime, precredential_validate, require,
)

COUNTERS = ("activeGames", "pendingActivities", "pendingRecordWrites", "inFlightAdmissions")


@dataclass(frozen=True)
class RuntimeSnapshot:
    runtime_id: str
    sequence: int
    kind: str
    engine_sha: str
    gym_sha: str
    recording_schema: int


class RuntimeHost(Protocol):
    def exclusive_lock(self): ...
    def current(self) -> RuntimeSnapshot: ...
    def assert_no_unfinished_transaction(self) -> None: ...
    def verify_snapshot(self, snapshot: RuntimeSnapshot) -> None: ...
    def status(self) -> dict: ...
    def drain(self, boot_id: str) -> dict: ...
    def begin(self, previous: RuntimeSnapshot, candidate: ValidatedRuntime) -> None: ...
    def stop_closure(self) -> None: ...
    def quiescence_barrier(self) -> bool: ...
    def select(self, runtime: ValidatedRuntime | RuntimeSnapshot) -> None: ...
    def start_closed(self) -> None: ...
    def complete(self, runtime: ValidatedRuntime) -> None: ...
    def complete_rollback(self, previous: RuntimeSnapshot, consumed_sequence: int) -> None: ...
    def resume(self, boot_id: str, runtime_id: str) -> None: ...
    def held(self, code: str) -> None: ...
    def delegate_keyless_update(self): ...


def idle_state(state: dict, snapshot: RuntimeSnapshot, *, now: float, closed: bool) -> str:
    require(type(state) is dict and state.get("releaseId") == snapshot.runtime_id
            and state.get("engineSha") == snapshot.engine_sha and state.get("gymSha") == snapshot.gym_sha
            and type(state.get("recordingSchemaVersion")) is int and state["recordingSchemaVersion"] == snapshot.recording_schema,
            "state_identity")
    require(type(state.get("bootId")) is str and state["bootId"], "state_boot")
    observed = state.get("observedUnix")
    require(type(observed) in (int, float) and math.isfinite(observed) and 0 <= now - observed <= 10, "state_stale")
    require(state.get("recordingHealthy") is True and state.get("recoveryComplete") is True, "recording_health")
    require(all(type(state.get(key)) is int and state[key] == 0 for key in COUNTERS), "not_idle")
    require(type(state.get("acceptingNewGames")) is bool, "admission_state")
    if closed:
        require(state["acceptingNewGames"] is False and state.get("drainAcknowledged") is True
                and state.get("recordingDrainComplete") is True, "drain_barrier")
    return state["bootId"]


def manual_snapshot(runtime: ValidatedRuntime) -> RuntimeSnapshot:
    profile = runtime.profile
    return RuntimeSnapshot(runtime.runtime_id, runtime.sequence, "manual-luna-v1", profile["engineSha"], profile["gymSha"], profile["recordingSchemaVersion"])


def promote(host: RuntimeHost, profile: dict, qualification: dict, approval: dict, *, clock=time.time) -> dict:
    def validate(previous_id, sequence, now):
        return precredential_validate(profile, qualification, approval,
            previous_id=previous_id, sequence=sequence, now=now)
    return _promote(host, validate, clock=clock)


def _promote(host: RuntimeHost, validate, *, clock=time.time) -> dict:
    """Shared transaction ordering; production entry always uses its full validator."""
    with host.exclusive_lock():
        host.assert_no_unfinished_transaction()
        previous = host.current()
        require(previous.kind in ("keyless", "manual-luna-v1"), "unknown_active_profile")
        # Validate the exact approval before even invoking the previous-runtime
        # host validator; that boundary must itself remain read-only.
        candidate = validate(previous.runtime_id, previous.sequence + 1, clock())
        host.verify_snapshot(previous)
        before = host.status()
        boot = idle_state(before, previous, now=clock(), closed=False)
        drained = host.drain(boot)
        require(idle_state(drained, previous, now=clock(), closed=True) == boot, "drain_boot_changed")
        # Repeat the exact current identity and immutable seals under the same
        # exclusive lock after drain, closing the admission/check race.
        require(host.current() == previous, "current_changed")
        host.verify_snapshot(previous)
        candidate = validate(previous.runtime_id, previous.sequence + 1, clock())
        host.begin(previous, candidate)
        committed = False
        try:
            host.stop_closure()
            require(host.quiescence_barrier() is True, "stop_barrier")
            host.select(candidate)
            host.start_closed()
            target = manual_snapshot(candidate)
            new_boot = idle_state(host.status(), target, now=clock(), closed=True)
            host.complete(candidate)  # Durable publication before admission.
            committed = True
            host.resume(new_boot, candidate.runtime_id)
            return {"state": "promoted", "runtimeId": candidate.runtime_id, "sequence": candidate.sequence}
        except Exception:
            if committed:
                # Resume may have succeeded despite an ambiguous reply. Never
                # force a rollback across a potentially admitted real game.
                host.held("committed_resume_unconfirmed")
                raise ManualRuntimeError("committed_resume_unconfirmed") from None
            try:
                host.stop_closure()
                require(host.quiescence_barrier() is True, "rollback_stop_barrier")
                host.verify_snapshot(previous)
                host.select(previous)
                host.start_closed()
                rollback_boot = idle_state(host.status(), previous, now=clock(), closed=True)
                # Preserve monotonic attempt sequence to prevent approval replay.
                host.complete_rollback(previous, candidate.sequence)
                host.resume(rollback_boot, previous.runtime_id)
            except Exception:
                host.held("rollback_unconfirmed")
                raise ManualRuntimeError("rollback_unconfirmed") from None
            raise ManualRuntimeError("candidate_failed_previous_restored") from None


def dispatch_update(host: RuntimeHost, *, manual_candidate: tuple | None = None, clock=time.time):
    # Inspect under the same rollout lock as the protected delegate. Its execution
    # must retain that lock; no unlock/relock window to race a manual activation.
    with host.exclusive_lock():
        host.assert_no_unfinished_transaction()
        current = host.current()
        host.verify_snapshot(current)
        if current.kind == "keyless":
            require(manual_candidate is None, "manual_requires_explicit_promote")
            return host.delegate_keyless_update()
        require(current.kind == "manual-luna-v1", "unknown_active_profile")
        # Existing automated build/QA can continue; active manual runtime is held
        # until an explicit profile-aware promotion invocation with exact approval.
        return {"state": "held-manual-profile", "runtimeId": current.runtime_id}
