# Credential-free QA role entries

This source supplies four executable, fixed root entry scripts, one for each
nondeployable guest unit. It does not install units, issue authority, run games,
change production access, or turn observations into qualification gates. Historical
Gym `4d30514717efa3bdc15704ff50ebcf28f387a59e` and its sealed receipts stay unchanged.

Each reviewed guest unit runs the sealed dependency Python with `-I -S -B` and
`/opt/commander-gym-qa/implementation/commander_gym/qa_<role>_entry.py`, where role
is exactly `recorder`, `sidecar`, `native`, or `proxy`. Entries authenticate fresh
authority, implementation/loaded-unit inventory, artifact closure, selected
profile/selector and the exact closed fake start-policy. They deliberately do not
acquire the controller's transaction lock. Unknown identities, pending writes,
production profiles and ambient credential imports fail closed.

Root passes an authenticated message in an anonymous root-owned 0600 descriptor,
drops supplemental groups/GID/UID to the fixed profile identity, clears the
environment and execs the sealed dependency Python and new launcher artifact
`qa_role_child_entry.py`. That bootstrap imports only sealed role/dependency roots.
The new launcher, recorder and sidecar inventories must contain the exact new
`manual_runtime_qa_roles.py` bytes. These are new matched artifacts, not replacement
modules hidden behind the old recorder pin. No root-private state copy persists.

The operator prepares native-owned 0700 `qa/native/configtree`, native/recorder
membership in the guest sidecar primary group, group-traversable native IPC
custody and separate native-owned 0700 lifecycle custody. The native entry writes
only the named literal placeholder token; it rejects extra configtree files and
preserves existing native heap, authority, recording and admission settings.
The sidecar factory receives `SchemaFixtureClient` explicitly plus a remote seat
capture sink; it never calls the SDK/default-provider constructor. Deterministic
schema values still pass through existing policy/native validators; unsupported
or illegal choices are real errors, not fallbacks or acceptance evidence.
Recorder execution pins identify the actual QA fake provider separately from the
catalog's configured production identity. No second capture scanner is created.

The proxy entry requires a **new explicitly proposed guest dependency closure**:
`qa-proxy-lock.json` with exactly schemaVersion=1 and relative program,
certificate, fixtureKey paths `qa-proxy/nginx`,
`qa-proxy/literal-fixture-certificate.pem`, and
`qa-proxy/literal-fixture-key.pem`. Every file must occur in the sealed dependency
inventory; all nginx runtime libraries must also belong to the operator-reviewed
guest image/closure. No installed host nginx or real certificate is consulted.
Prepare a proxy-owned 0700 `qa/proxy` directory. The generated config serves the
sealed frontend and proxies `/game` over **127.0.0.1:18443 only**, with fixture TLS.
It makes no LAN/tailnet/public-access or production HTTPS claim. The guest owner
must verify its proposed nginx ABI/runtime and literal fixture TLS bytes before
execution. This repository does not ship that OS-specific binary or TLS fixture.

## Paired canonical v2 source qualification

The bounded canonical v2 extension supplies the missing native callback and
parameter receipts in a new matched native/Gym pair. The ordinary/v1 controller
interfaces and their historical recordings remain unchanged. See
[canonical callback recording v2](canonical-callback-recording-v2.md) for the
explicit action parameters, typed structured/mulligan/bottom receipts, exact
native acceptance and masking, and restored-state continuity requirements.
Action-record/raw-envelope/dataset-export v2 preserve submitted parameters;
structured-response records and RunRecord retain their existing schema.

The deterministic `CanonicalCallbackFixtureTest` uses the real native writer,
native application and committed-state persistence restore, with fake answers
recorded before native application. The offline Gym qualification driver reopens
the recorder between callbacks and validates the complete canonical envelope.
This complements shared-lobby/provider and JVM callback transport acceptance. It
is not guest, real-provider, model-quality or production qualification.

Failed, stale, cancelled, rejected, overridden, unmatched or uncertain callbacks
still preserve recording incompleteness. No fabricated choices, inferred parameter
migration, administrative model input or gap removal can certify such a run.

The host owner needs an explicit new matched source/artifact/profile proposal and
successful exact CI before reviewing bounded guest QA. These four credential-free
roles and their closed fake start-policy remain the seam; this change neither
extends executable inventory nor grants host execution. Existing historical pins,
failed captures and authority remain unchanged. No frozen artifact is silently
substituted, and no guest boot, service activation or credential loading follows
from source qualification.
