# Tolaria cadence preparation

The cadence is disabled. Source merging does not enable a timer, publisher,
provider, or production update. The current release owner retains activation.

`python -m commander_gym.release_cadence --help` provides a read-only preflight
over the existing private `release.json` bundle. Supply the separately reviewed
release digest, approved baseline profile and its digest, source evidence and its
digest, current runtime ID and next sequence. Successful output remains
`held-disabled`; there is no activation flag or installer invocation.

Keep the bundle, baseline, source evidence and review reports in the existing
private deployment evidence area. Retain installed immutable artifacts and prior
runtime profiles/qualification/approval files under the existing runtime bank,
plus catalog snapshots and authoritative recordings. This adds no second release
manifest store, retention policy or garbage collector. Never upload private bundle
content or recording data to the public triage workflow.

The existing bundle inspector verifies exact artifact bytes and all nine fresh
qualification gates. Preflight additionally freezes the approved operational
settings, configuration files, pilot identity and binding names. Separate approved
latency changes must be represented in the reviewed baseline. Merged local
inference source never enables llmster. Native/catalog qualification may update
data for existing bindings, but it cannot bypass installed pilot identity.

Source evidence schema1 has `releaseSha256`, `sources` and `catalogValidation`.
Each source role (`engine`, `gym`, `private`) records its fixed repository, exact
SHA, independent review (`sha`, `verdict`, `evidenceSha256`) and successful checks.
Each check records `workflow`, `name`, `event`, `branch`, `headSha`, `testedSha`,
`runId`, `runAttempt`, `conclusion`. Require final-main push checks, including
engine backend matrix/aggregator/frontend/coverage/web tests, all four Gym jobs,
and private integrity. PR merge checks, dispatch checks, skipped checks, different
SHAs, duplicate jobs and missing evidence are not substitutes. `catalogValidation`
binds `engineSha`, `gymSha`, `privateSha`, `catalogFilesSha256` and passing
`collectionIntegrity`/`nativeDeckValidation` to the exact tuple.

These records are qualification-owner attestations whose digest is reviewed
separately. They do not authenticate themselves. An unattended collector must
verify GitHub repository/workflow identities, final source/attempt/check results,
review evidence, catalog contents and native proof before issuing them. Preflight
does not implement that collector and cannot turn arbitrary JSON into activation
authority. Existing exact profile/qualification approval, previous-runtime and
monotonic-sequence checks remain mandatory. Refresh the host and repeat idle,
recording, health and rollback checks inside the existing locked installer before
any future promotion.

`python -m commander_gym.upstream_triage --since FULL_UPSTREAM_SHA` reads public
upstream changes at one pinned main snapshot. It emits source links and heuristic
review categories. Unknown changes still need review; categories never authorize
a merge. Truncated comparisons or nonlinear history fail closed without advancing
the cursor. The tool does not write a cursor. The caller should retain last-seen,
last-reviewed and last-integrated SHAs separately in existing private evidence;
`cursorAdvanceAllowed` permits review-cursor advancement only after a reviewer
accepts the complete report, not merely after collection.

The workflow is manual-dispatch only with contents-read permission. It produces a
public-source report artifact, sends no external messages, and preserves the
existing `integration/upstream-2026-10-15` owner. Weekly sync remains held while
that integration is owned. Completing it requires an isolated tested PR against
then-current main, matching Gym/catalog checks and independent review. This tool
does not start or merge upstream work.

After prerequisites are met, proposed schedules are03:45 nightly promotion,
08:17 daily upstream triage and Tuesday09:23 weekly sync preparation, all in
America/Los_Angeles with DST. No schedule is installed by this change. Alerts
remain concise actionable status reports to Noah through the existing parent
conversation; no external recipient or notification integration is added.

The remaining unattended boundary is publication under existing authorization:
protected root feeder/catalog units cannot access the operator's GitHub home
configuration. Do not weaken those sandboxes, move credentials, mint tokens or
widen workflow permissions. Prepare and review a narrowly scoped operator-owned
fetch/publication invocation before enabling it; transfer only immutable data
through the existing validation boundary. Until that boundary, full exact-source
qualification and the release-owner handoff are complete, promotion stays disabled.
