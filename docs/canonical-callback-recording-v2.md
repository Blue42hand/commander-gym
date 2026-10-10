# Canonical callback recording v2

An action template does not identify the selected targets, X or payments. Logical
action-record v2 requires `chosen_action_id` and an explicit object
`chosen_action_params`. The selected native offer's `parameterSpec` constrains
named fields and wire types; Argentum remains authoritative for legality and
application. Empty `{}` is valid only as an explicit submitted, native-accepted
unparameterized choice.

`DecisionRecord.from_dict` reads v1 and v2. V1 writers omit the new field and v1
targets remain template-only. No reader infers parameters from historical metadata.
`decision_record_from_execution_trace(..., schema_version=2)` opts into v2 from
actual submitted parameters. Missing parameters fail closed. Existing callers
retain v1. Structured-response records and RunRecord stay at their existing schema.

Raw envelope and dataset export schema v2 carry the same explicit action target.
Their builders accept `schema_version=1` or `2`; omission selects the version of
the action records. Mixed action versions, inferred upgrades and parameter-dropping
downgrades fail. Structured-only/empty legacy calls remain v1 unless v2 is explicit.
Raw readers accept both envelope versions. The raw catalog records each envelope's
actual version; catalog schema, identity epochs, physical recordCodec and storage
layout remain unchanged. Old raw artifacts and fixtures are never rewritten.

The optional native `RecordedAiCallbackController` preserves ordinary/v1 APIs.
Argentum mints v2 input contexts from its own masked callback values. Each issued
correlation receives native acceptance during authoritative application or an
explicit disposition. Action targets retain the submitted parameters separately
from the concrete native action. Structured targets retain the typed response;
mulligan/bottom callbacks are authored by the native server. Seat-specific
mulligan offers permit another seat's independent mulligan without making the
first offer stale. Epoch/state/question checks still constrain asynchronous action
and payment correction. Cancellation, supersession and action-gate override cannot
produce acceptance. Accepted receipts precede sealed terminals.

`canonical_capture.build_capture_envelope` verifies the terminal native source,
exact imported prefix, immutable pins, transition continuity and all callback
joins before `NativeGameCapture` attaches raw evidence and seals the journal.
Every completion needs one start/input/accepted-result with matching seat, kind,
Binding, exact choice and result digest. Administrative native states are used
only to verify application; they never enter model input or targets. Timing across
different recorder clocks stays unavailable. The recorded scope is native
transitions and recorded AI callbacks, not an invented human-decision dataset or
an exact-replay certification.

Recorder restart preserves first-run pins. An envelope appended before interrupted
sealing is reverified and reused identically. A closed journal recovers its manifest
without reopening. Native source resume requires a matching committed-state digest
and a new native restored-state receipt before callbacks or transitions continue.
The native state setter may emit one administrative checkpoint while restoring an
empty session, immediately before that matching receipt; it contributes no model
input and cannot replace the required restored-state identity. Missing prior
acceptance, failed/stale/cancelled/unmatched callbacks, source gaps, changed revisions
or discontinuous recovery preserve recording incompleteness. Native v1 sources
retain the unavailable-canonical-adapter gap and the closed-only v1 accepted-subset
export retains its previous behavior.

Conversion is bounded independently of compressed storage: at most 4,096 callbacks
and 16 MiB of aggregate decoded join bytes and record bytes. Exceeding either limit
preserves explicit recording incompleteness.

Qualification uses deterministic keyless protocol and native unit fixtures only.
`CanonicalCallbackFixtureTest` exports a real native writer, actual native application
and committed-state persistence restore plus five fake answers written before each
application. `scripts/qualify_canonical_v2_fixture.py` validates exact correlations
and preapplication markers, reopens the recorder between callbacks, and checks the
complete v2 envelope. This offline replay complements the shared-lobby/provider
and JVM callback transport tests; it does not prove production host readiness or
power-loss durability of the fake answer file. Python optimization cannot disable
its qualification checks.
This source change does not activate services, issue a profile or authority, load a
real credential, call a provider or authorize a game. A new reviewed matched native/
Gym build and explicit artifact/profile proposal are required before owner QA.
