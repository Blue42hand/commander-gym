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

## Canonical recording decision still required

These entries intentionally preserve the existing incomplete recording outcome.
Complete authenticated callback conversion needs a paired native evidence
protocol extension, not merely a new recorder pin:

- `GameSession.beginAiDecisionEvidence` currently excludes pending structured
  decisions. Extend its native input/result receipts to carry one correlation ID,
  exact masked pending decision/response contract, authoritative accepted response
  or explicit disposition, and the resulting masked seat observation/digest.
- Parameterized choices need receipts for the offered template plus exact supplied
  parameters and the action actually parameterized/validated/applied by Argentum.
  Current exact offered-action equality cannot authenticate these choices.
- Mulligan/bottom callbacks need the same native correlation/application/result
  evidence. Their existing offers/submissions do not authenticate the bridge's
  UUID or provide a native masked result digest. Gym-created semantic labels are
  not a substitute for native legal choice identity.
- The engine controller/provider and JVM adapter must transport this evidence
  for all callback kinds. A new engine pin and matched adapter/server packages are
  therefore essential, in addition to a new Gym capture/converter pin.

Existing `DecisionRecord`, `StructuredDecisionRecord`, `RunRecord` and raw-envelope
schemas can represent successful choices; no parallel training schema is needed.
However `PrivateGameJournal.raw_evidence/finish` currently require canonical IDs
to equal every callback ID, including technical failures. A failed, stale or
cancelled callback cannot truthfully have a chosen action. The conservative
existing-contract option is to retain completeness gaps for those runs. Making
such diagnostic-only runs complete would require an explicit coverage-contract
decision separating executed decisions from diagnostic callbacks while retaining
every raw receipt. This change is not made here. Native restart/resume eligibility
also needs explicit correlated receipt continuity rather than assuming replay.

The bounded next decision is whether to extend the native producer/controller
evidence protocol for all successful callback kinds while keeping failed/stale/
cancelled runs incomplete. Until that paired source change is reviewed, a genuine
complete-recording interruption/recovery acceptance driver is blocked. Removing
gaps, assigning fabricated digests/choices or using admin state as pilot input is
not an implementation option. The host owner alone consumes explicit new pins
and performs guest qualification; no frozen artifact is silently substituted.
