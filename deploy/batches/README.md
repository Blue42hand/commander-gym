# Private managed batches on Tolaria

The owner-approved batch extension uses a durable root-owned SQLite command/attempt ledger, one isolated native JVM, one recorder, and a distinct provider UID. The panel holds no provider credential and cannot address arbitrary units or execute commands. Native and recorder see only the attempt's capture root. The provider sees its budget and masked callback socket, without raw capture or lifecycle mounts. The existing human service, recording roots, admission policy, and Tailscale routes are unchanged.

A draft freezes four approved catalog Binding snapshots, engine/Gym/server/adapter versions, game count, wall time and decision limits, cumulative USD estimate/request/token fences, failure policy, and execution mode. Start requires that draft's fingerprint, version and a durable idempotency key. Pause/finish drain the current attempt. Interrupt stops only the isolated fixed units. Unknown create responses are never retried. Restart closes those units, preserves prefix evidence and unsettled budget reservations, reconciles interrupted attempts, and requires explicit Resume. A retry is a linked new draft.

Managed recording admission is explicit on both JVM adapter and Python bridge/sidecar. Its marker is `managedBatchGame`; it never claims `manualHumanGame`. Existing human admission remains the default. The batch copy of the pinned server replaces only the embedded matched Gym adapter; every other archive member is checked byte-identical. No engine rules or native producer instrumentation change.

## Preparation

From the batch branch, run the focused Python tests with `PYTHONPATH=tests:.`. Build the adapter using the pinned engine checkout through its `just gradle` wrapper, a Java 25 runtime, `ARGENTUM_ENGINE_DIR`, and `GRADLE_REAP=0` to avoid touching unrelated long-running JVM processes. Run `jar test` with the recorder callback, provider profile and auto-configuration tests. The adapter manifest must name the exact Gym commit and pinned engine commit.

Install that commit as a root-owned immutable `/opt/commander-gym-web/<commit>` tree. As root, run:

```
python3 deploy/batches/package_runtime.py --adapter jvm-adapter/build/libs/commander-gym-argentum-adapter.jar --gym-sha <commit>
python3 /opt/commander-gym-web/<commit>/deploy/batches/install.py --release /opt/commander-gym-web/<commit> --gym-sha <commit>
systemctl daemon-reload
```

Preparation verifies the existing sealed inputs. It creates new identities and fixed units/configuration; it does not read the provider key, enable services, or launch games. Stop the batch supervisor before replacing its configuration. Preserve its databases, budgets, captures and annotations across every update.

The approved existing credential is loaded only by `commander-gym-batch-provider.service` via systemd `LoadCredential`. The synthetic `commander-gym-batch-fixture.service` has no credential directive and makes no API calls. Native, recorder, panel and supervisor never load the provider key. All child processes die with the fixed provider service's control group.

## Panel wiring and activation

First back up the panel unit/configuration/current release and capture Tailscale Serve status. Keep all existing mounts and append only:

```
BindReadOnlyPaths=/var/lib/commander-gym-batches/captures:/evidence/batches
BindReadOnlyPaths=/run/commander-gym-batches/panel:/control
```

Set panel `batch_socket` to `/control/control.sock` and append `/evidence/batches` to `grouped_capture_roots`. This bounded two-level inventory recognizes per-attempt capture roots. Do not mount the supervisor database, provider credential, native IPC directory or lifecycle socket into the panel. Keep the original masked status API unchanged.

After qualification, enable the supervisor, select the immutable panel release and restart the panel. Keep `/gym/` at its existing HTTPS endpoint; no Tailscale routing change is required. Confirm missing/other identities are denied, CSRF is enforced, desktop/phone controls work, old-tab commands conflict and same-key retries do not duplicate work. GET creates no games. No paid batch should be started as part of deployment testing.

Costs are conservative estimates/reservations, not billing guarantees. Missing ledgers fail closed; ambiguous responses retain reservations. Per-game interruption is a supervisor disposition, not a native terminal result. A manifest's existence is not an integrity claim. Recording completeness/verification/replay/triage are shown separately in game inspection. Retained synthetic captures are explicitly marked as fixtures and excluded from claims about paid or strategic game performance.

## Rollback

Stop/disable only `commander-gym-batches.service`; its `BindsTo` units stop with it. Confirm all five fixed worker units are inactive/failed. Restore the backed-up panel unit/config and previous `/opt/commander-gym-web/current` target, daemon-reload and restart only the panel. Leave the Tailscale Serve configuration alone. Preserve `/var/lib/commander-gym-batches`, the existing review database and all original evidence. No host reboot is required.

## Local inference

The optional `--local-catalog` preparation flag adds credential-free llmster
execution and local Binding choices to the batch panel. See
[llmster play](../../docs/llmster-play.md) for catalog preparation, human-play
integration, limits and validation boundaries.
