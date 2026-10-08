# Private game capture and finalized analysis contract

Status: capture/analysis code implemented; runtime native producer adoption and deployment
are not complete. No production traces are fixtures. This change does not certify the
historical supervised game or modify its `operator_stop` receipt.

## Placement and identity

Use the existing `StorageLayout` durable `runs` route. Proposed Tolaria placement is
`/var/lib/commander-gym/runs/<run_id>/`; configure `storage.root=/var/lib/commander-gym`
(or override `runs`). New runs use a fresh private 0700 directory. Individual files are
0600. Existing historical directories and records remain where they are; this change
never moves, deletes, rewrites, or automatically promotes them.

One finalized `manifest.json` indexes that game's source evidence and derived projections.
Run IDs/game IDs/artifact IDs, not host paths, establish identity. Journal JSONL segments
are the immutable capture source. Seat projections and canonical raw-evidence files are
derived artifacts with `sha256:` IDs from `storage.py`. Analysis reports are independently
versioned annotations in the same run directory, never competing game truth.

## Documentation and schema mapping

The authoritative design is [pilot-learning-data-architecture.md](pilot-learning-data-architecture.md),
especially Evidence and learning lineage, Three evidence layers, and Location-independent
storage. [ARCHITECTURE.md](../ARCHITECTURE.md) keeps Argentum authoritative.

| Required evidence | Existing contracts / source | Implemented capture | Remaining production gap |
|---|---|---|---|
| Run/game/seat identity; engine/Gym/schema pins | `RunRecord`, `EngineProvenance`, `evidence.py` | Journal envelope joins run/game IDs; manifest pins/digest; canonical raw envelope validation | Launcher must supply exact immutable pins and game ID |
| Deck → DeckKnowledge → Pilot → Binding lineage | `identity.py`, Binding catalog, `RunParticipant.binding` | Existing canonical raw-run-evidence envelope retained unchanged; no second identity schema | Native controller callbacks need join to canonical decision IDs/results |
| Own observation, legal semantic options, selected native response | `SeatProvenance`, `DecisionRecord`, `StructuredDecisionRecord`, `pilot_records.py` | Durable start before pilot invocation; durable completion after choice; original schema copied | Human seat observations/legal menus need a native seat-safe capture hook |
| Actual routing subsystem/model/skill | Existing choice `metadata`, `modelIo`, routing | Preserved verbatim subject to forbidden transport/credential fields | Every runtime producer must retain complete metadata |
| Actual model response/rationale, usage/cost/retries/errors | Existing model-I/O/provider-call/budget receipts | Preserved when provided; rationale never synthesized; pending start survives crashes | In-flight provider attempt settling still belongs to existing budget ledger; capture alone cannot recover a lost response |
| Native human + AI actions/events/results and ordering | Argentum `GameSession` / engine `ExecutionResult` | Versioned `native_transition` payload with producer sequence, native schema, action, events, result, before/after digests | Authoritative producer hook must feed these values; no such hook installed by this PR |
| RNG/deck/card pins and replay | Native `CompactReplay` v1–v4 | Admin-only verbatim compact replay ingestion, including setup seed and pinned definitions | Runtime must supply private native replay; observation-only capture cannot prove exact replay |
| Native terminal vs supervisor classification | Gym #205 `GuiNativeTerminalWatch`, `RunTermination` | Reuses exact three-field receipt; run-local path binding; supervisor termination preserved separately | Orchestrator must set a fresh receipt path for its own session before launch; receipt has no game ID |
| Input / target / provenance separation | Existing `raw-run-evidence`, `DatasetManifest`, `manifest_export.py` | Only validated canonical raw envelopes are dataset-compatible; callback journal is not a dataset | Missing semantic identity/result evidence blocks conversion, rather than inventing it |
| Immutable raw evidence; annotations; versioned datasets | `RawEvidenceStore`, `DatasetManifestStore` | Journal root/artifact hashes; versioned local analysis receipts; source never edited | Dataset generation remains the existing explicit manifest workflow |

Verified bases: Gym main `598a60a4120ff2d7ba69fd90b16eccc85e5e30fa` after #205;
Argentum main/tested revision `f9ba3d07c9894ffedb62697a60389e7902b4bbc1`.
At that engine pin, native `ReplayStore` defaults to an in-memory bounded store when accounts
are disabled; JDBC persistence requires accounts/database configuration. Compact replays
contain setup/seed, ordered actions, yields, engine/card pins and checkpoints, but do not
contain a full per-seat human choice trace with original legal menus/model context or every
emitted native event and its timing. No account/network/security configuration is changed.

## Producer API and boundaries

Create `PrivateGameJournal` in a new run directory with all seven pin sections:
`engine`, `gym`, `models`, `decks`, `bindings`, `config`, `rng`. Use `null` for unavailable
sections; never substitute guessed versions. Pin config is credential-free exact effective
configuration or canonical digest/reference, not environment dumps. Provide `game_id` and
`required_sources=('native', 'seat:<seat-id>', ...)` for every human and AI seat.

The Binding sidecar builder accepts `game_journal=`. It wires `RecorderSeatSink` start and
completion callbacks while preserving the existing legacy provenance sink. This option is
not activated in deployment/launchers by this PR. Alternative orchestration can pass
`decision_start_sink=recording.started` and `provenance_sink=recording.finished` to
`GameServerSeatAdapter`. The recorder neither reads nor provides any pilot context.

The native orchestration producer calls `append('native_transition', ...)` with source
`native` and a contiguous zero-based producer sequence. Required payload fields are
`game_id`, `native_schema`, `action`, `events`, `result`, `before_state_digest`,
`after_state_digest`. Native timestamps/RNG evidence may be included when actually available.
This is preservation of authoritative evidence, not rules reconstruction. Producer expected
counts are supplied independently at terminal flush; a local sequence detects dropped or
repeated received events, but cannot detect an event the producer never emits without an
independent counter. Native compact replays go through `native_replay(...)` and remain admin-only.

Use existing `build_raw_evidence_envelope`/`pilot_records.py` when native schema, semantic
identity, results, and Binding lineage permit exact conversion, then `raw_evidence(...)`.
The canonical envelope's decision IDs must exactly match the captured callbacks in order.
Do not normalize missing human choices or route raw admin observations into dataset inputs.
A canonical envelope is checked by existing validators; later dataset membership/splits and
qualification still use `DatasetManifestStore`. Recording completeness does not authorize
training-set promotion.

Finalize through `finish(outcome, expected_sources=..., gaps=...)`, or reuse #205 via
`finish_from_native_receipt(...)`. Abort/operator_stop/storage_limit/error are finalized for
analysis with incomplete coverage. Missing pins, canonical training evidence, required source
counts, pending choices or native transitions prevent `recording_complete=true`. Declare
additional gaps explicitly (for example `human_legal_menus_unavailable`). Producer completeness
is an assertion checked against its declared contract, not proof that native emitted every
possible piece of game evidence. Exact replay remains false until separately verified by the
pinned native reconstructor; this capture layer does not perform reconstruction.

No transport credentials, auth/reconnect tokens, raw headers or environment dumps are accepted.
Known credential fields and credential-shaped values are rejected, including nested fields.
Only semantic observations/model metadata should be submitted: arbitrary free-form text cannot
be universally classified as secret. Referee data stays in private journal files. The offline
`seat_projection` contains only matching seat capture events; it excludes all admin rows and
other seats. Masking itself stays native-owned and the submitted observation must identify the
same viewing/perspective seat. No UI/public endpoint is added.

## Finalized manifest / automated review contract (schema 1)

`manifest.json` includes:

- `kind: commander-gym.game-capture-manifest`, `schema_version: 1`, `run_id`, `game_id`;
- `ready_for_analysis: true` only after terminal journal fsync and verified publication;
- `recording_complete` independently reports full recording coverage;
- `journal_root_sha256`, `artifact_hash` (canonical ordered artifact descriptor digest);
- `artifacts`: relative `path`, `role`, `sha256`, `artifact_id`, `size_bytes`, optional `seat_id`;
- `outcome` (native terminal evidence and unchanged supervisor classification), `gaps`;
- `storage_bytes`, `analysis.report_path_template` and `analysis.idempotency_fields`.

`discover_finalized_manifests(runs_root)` exposes verified finalized metadata plus a portable
`relative_run_directory` locator beneath the configured runs route. It skips
legacy/unsealed/damaged entries; use `inspect_journal` for explicit diagnostics rather than
interpreting absence as a completed game. `verify_finalized_manifest(run_directory)` rechecks
all hashes, journal membership and outcome before a review claim. `publish_finalized_manifest`
recovers a crash after terminal fsync but before manifest publication, without altering raw data.

For analysis version `1.0.0`, call `claim_analysis(run_directory, '1.0.0')` from `game_analysis.py`.
The deduplication key is `(run_id, artifact_hash, analysis_version)`. Pass an explicit stable `worker_id` identifying the task/worker. The lock/atomic private
ledger `analysis/1.0.0/review.json` yields `claimed`, `busy`, or `already_completed`. A claimed
run has a unique `claim_id` and one-hour renewable-by-resumption lease. Expired work can be
claimed again; an obsolete worker cannot complete a newer claim. `renew_analysis` extends
only the same live owned claim; `fail_analysis` stores a stable failure code and releases
the lease for explicit resumption. Raw exception messages are not stored. A failed/stale
claim is resumed by `claim_analysis`, with a fresh claim ID and explicit worker ownership. The helper makes no calls,
starts no games and sends no data outside the local directory.

Create the report with the three identity fields, explicit evidence references, coverage
limits, observations/recommendations, and analysis version. Call `finish_analysis(...)`; it
fsyncs and verifies `analysis/1.0.0/report.json` before atomically marking `review.json`
completed with `report_sha256`. Completed claims reverify the report. A different report at
an existing version requires a new version. A crash after report write but before completion
can resume with identical report content. `prepare_notification` durably reserves one delivery attempt before any external send.
Its default state is `uncertain`, so a crash after reservation cannot cause an automatic
second send. Only the first reservation returns `send_allowed=true`. `record_notification`
records a verified delivery receipt, affirmative `not_sent` evidence, or `uncertain` status.
Only affirmative `not_sent` permits another reservation; `delivered` or `uncertain` never
automatically retry. These helpers do not send notifications themselves. Scheduling,
actual delivery and analysis content are parent-owned.

Finalized manifests/artifacts are immutable. Publication is idempotent only for identical
bytes; it refuses a changed finalized artifact. A correction uses a fresh run/revision
identity and a new private directory, with an explicit `RunRecord.metadata.supersedes`
reference to the old run/artifact hash. Preserve both records. This intentionally avoids
silently revising `artifact_hash` under the same stable finalized manifest; the review
ledger does not guess supersession or mark skipped legacy games analyzed.

## Durability, limits and retention

Journal records carry schema version, global and producer sequence, UTC timestamp,
process clock ID/monotonic elapsed time, and previous/current SHA-256. Process clock epochs
change on resume; monotonic values are not comparable across epochs. A single writer lock
excludes competing processes. Each event is flushed/fsynced; new directory entries and final
manifests are fsynced. An uncertain write failure blocks further writes. Crash prefixes remain
unsealed; damaged/truncated tails refuse resume and are preserved for explicit repair.

Defaults rotate at 16 MiB, with a 1 GiB raw-journal cap and 64 KiB reserved for partial terminal
sealing. A single event may exceed the segment target. Projection/evidence files and reports
are additional storage; the journal cap is not an archive-wide or disk quota. On capacity failure,
stop collection visibly, keep all history, and finalize as partial if possible. Measure manifest
`storage_bytes`, free disk and growth per game; alert before capacity pressure. Compression and
moving historical data require user-owned retention/archival decisions. No auto-delete exists.
Hash chains detect damage; they are not signatures against a malicious writer. Keep final roots
and backups under independent trusted custody. Filesystem/disk failure can still lose recent
writes; do not claim fsync is a substitute for verified backups.
