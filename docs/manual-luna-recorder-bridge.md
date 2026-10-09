# Manual Luna recorder connection

The matched Gym JVM adapter and Binding sidecar can share the existing native lifecycle
recorder without granting the provider process access to native journals or historical
captures. This is optional source support, not an installer or activation approval.
No service here starts games, loads credentials, calls providers or changes network ingress.

## Composition and exact settings

The existing recorder process constructs its existing `NativeGameCapture` and
`RecordingHealthReporter`. Before starting its reporter, compose **one** bridge with that
same capture (never another scanner/finalizer):

```python
from commander_gym.recorder_bridge import RecorderBridge, recording_registry

registry = recording_registry(approved_catalog_path, approved_instance_root)
bridge = RecorderBridge(
    capture, registry,
    native_socket=approved_ipc_directory / "native.sock",
    sidecar_socket=approved_ipc_directory / "sidecar.sock",
    native_uid=approved_native_recorder_uid,
    sidecar_uid=approved_distinct_sidecar_uid,
    sidecar_gid=approved_sidecar_group,
)
bridge.start()
```

`recording_registry` resolves the exact existing Binding/Deck/Pilot identity and deck
payload, with a recording-only Pilot that cannot choose. It does not import the OpenAI SDK
or need a key. The operator's protected runtime profile must match the approved catalog,
release closure and all existing recording pins. Actual game engine/deck/RNG evidence still
comes from native initialization. The bridge does not certify dataset coverage or replay.

The IPC directory must already exist, be nonsymlink, owned by the native recorder UID,
mode 0750 (or stricter), and traversable by the approved sidecar group. All parents must be
nonsymlink. The recorder needs permission to assign that socket group. Native registration
uses an owner-only 0600 socket; sidecar receipts use 0660. Peer UID checks enforce the roles
on Linux and macOS; unsupported platforms fail closed. Native and sidecar UIDs must differ.
`allow_same_uid_fixture=True` exists solely for isolated same-user unit fixtures and must
never appear in a deployment profile or launcher.

Native properties add this exact approved path:

```properties
commander-gym.recorder.socket=<approved IPC directory>/native.sock
```

Retain `commander-gym.manual-only=true` and the existing exact matched adapter packaging.
The adapter registers each masked request before policy HTTP and closes its registration
after HTTP returns/fails. Registration checks do not acquire an authoritative snapshot.
The private HTTP callback carries `X-Commander-Gym-Callback-Id`; it adds no GUI field.
Native recorder exchanges have a three-second deadline and close/reap their worker on
failure. A failed registration prevents policy dispatch; failed close prevents action return.

The provider sidecar replaces its root/pins/lifecycle variables with:

```text
COMMANDER_GYM_RECORDER_SOCKET=<approved IPC directory>/sidecar.sock
COMMANDER_GYM_RECORDER_UID=<approved native recorder UID>
```

Do not supply `COMMANDER_GYM_RECORDING_ROOT`, `COMMANDER_GYM_RECORDING_PINS`, or
`COMMANDER_GYM_RECORDING_LIFECYCLE_SOCKET` in this mode; startup rejects that combination.
The Binding entry point injects `RemoteCaptureSink` into its existing per-game/seat factory
and starts no local capture or recording-health thread. Provider key loading remains solely
in the separately approved sidecar service. The bridge receives no bearer/provider key.

## Receipt and failure contract

The native UID registers an explicit manual game/seat/Binding/callback and the exact masked
request. The recorder requires an existing verified native initialization and matching seat,
then reuses the same sidecar transport validation and seat conversion to derive expected
observation, evidence and canonical Binding lineage. Its start hook stops before any Pilot
can choose. The sidecar UID cannot register or close native requests.

Sidecar start/finish must match that registration, observation, lineage and decision evidence.
Unknown games/seats/Bindings, stale or closed starts, overlapping same-seat callbacks,
completion without a durable start, unexpected fields and credential-shaped payloads are
rejected. Registrations expire after 130 seconds. Already-started callbacks may record a
bounded late completion after native cancellation; this captures charges/failure evidence
but grants no native action authority. Native correlation IDs remain the journal decision IDs
when native enumerated evidence exists, preserving current accepted-choice conversion.

Each receipt is the hash of its fsynced journal row. Exact duplicate phase+ID+content returns
that original receipt without appending. Changed duplicates reject. Recovery verifies durable
journal rows rather than trusting an external receipt file. Registration/closure metadata is
admin-only runtime context; seat start/finish retain existing projection boundaries and actual
model-I/O/usage/error receipts. Replies contain only protocol, ID and receipt hash, never state
or journal content. The existing recorder alone imports native sources and seals manifests.

Messages are capped at 4 MiB, framing has a two-second absolute deadline, listeners use a
bounded backlog and one serial worker per role, and the sidecar retries a lost receipt once
with identical content/ID. Native HTTP and existing 90/110/120-second provider/callback/JVM
bounds remain. The two sidecar receipt exchanges add at most eight seconds including retries;
the native registration/close exchanges each have their separate three-second bound. No new
spending/game-duration cap or automatic game start is added.

Bridge startup/stop/listener failure makes the existing capture health unhealthy; journal
write failure latches unhealthy. Native admission still uses its existing recorder heartbeat
and expiry, while an individual missing receipt fails the callback immediately. Shutdown
closes accepted sockets and joins bridge workers before capture closure; stop reporter first,
then bridge, then capture. Never force-kill or invent terminals. A restart reclaims only verified
owned stale sockets whose connect fails with ECONNREFUSED, rejects active/unrelated paths,
and checks the inode before unlinking. Histories and acknowledged incomplete captures stay intact.

Qualification uses literal fake credentials and temporary synthetic evidence only. The
ordinary Python/JVM suites cover masking, callbacks, duplicate/restart/error/cancel handling,
peer rejection, framing bounds and failure-before-policy. A separate root Linux CI fixture
uses a real distinct temporary process UID to prove native socket/journal denial and matching
sidecar receipts; that fixture creates no persistent host users, services or credentials.
Actual systemd identities/group access, matched protected runtime/health/idle rollout and
rollback still require the runtime owner's separate synthetic qualification before activation.
Canonical recording completeness and exact replay retain their existing documented gaps.
