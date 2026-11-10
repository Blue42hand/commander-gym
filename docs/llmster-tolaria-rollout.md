# Tolaria local inference rollout — November 10, 2026

Managed batch selection is active at the existing private `/gym/` panel. The
installed release is `9f2499bf7dc3e8d048ff92a155e15253bd0f2e04`, with a matching
adapter built against engine `12ab2f0ed5bd018334521dc52497e6444f26f5a9`. The isolated
server embeds that adapter; every other server archive member is byte-identical
to the installed base server. The catalog offers six local Bindings alongside
paid and synthetic execution. Local plans cannot use cloud Bindings, have a zero
provider spending fence, and retain request/token/decision/time limits.

The panel and batch supervisor select the new immutable release. Application
checks returned 200 for the owner and 403 without identity. Served batch UI bytes
match the release. The local batch unit's D-Bus `LoadCredential` array is empty;
network access is restricted to localhost and the configured inference IP.
No new paid or local game was started during this deployment.

Human local play is **staged, not activated**. The existing installed native/Gym
pair already includes the local provider. Six local Bindings and separate sealed
configuration artifacts were prepared, and their exact identity and file checks
passed. The live human service has not been restarted and its original runtime
seals and healthy recorder were rechecked.

Two conditions remain before human activation:

- The owner-confirmed inference endpoint currently refuses connections. Startup
  requires discovery of the exact configured model and sufficient loaded context;
  these checks were preserved.
- The isolated whole-runtime qualification failed because a native-UID QA
  readiness check tried to read the private local catalog. The guest reported
  `rollback_unconfirmed`; it was shut down, and its failure evidence was retained.
  This was a disposable guest with no production filesystem shares or external
  network. Source commit `0e95e72990bdc2e1ca4181c04650656031a044b6` corrects discovery
  checks to use sealed profile IDs without granting catalog access. Its 15 local
  QA regression tests pass. The corrected candidate still needs fresh isolated
  qualification and the normal exact promotion tuple. No successful human
  qualification or activation receipt was issued.

Deployment checks passed 137 focused Python tests and 13 JVM adapter tests.
The initial Java 21 build invocation was rejected and rerun using sealed Java 25.
An unsuitable combined test working directory was corrected; the suites passed
from their required directories. These checks establish source behavior and
packaging, not a completed native game or human acceptance.

Private backups, exact unit/configuration observations, SQLite backups, test
status, and staging scripts are retained in
`/var/lib/commander-gym-local-rollout/20261110-9f2499b/`. Isolated guest evidence
and the held human candidate are under
`/srv/argentum-luna/prepared/llmster-20261110-9f2499b/`.

To roll back only the batch/panel deployment, first resolve all active or queued
batches, then run as root:

```sh
python3 -B /var/lib/commander-gym-local-rollout/20261110-9f2499b/rollback-batches.py
```

The script refuses an unresolved active batch, restores the previous units,
configuration and panel selector, and retains databases, annotations and captures.
Human play is unaffected by that rollback.
