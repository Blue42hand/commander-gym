# Analysis report adapter profile v1

This is the explicit annotation adapter consumed by the improvement ledger, not a
recording/trajectory schema or change to the canonical learning architecture.
Identifier: `commander-gym.analysis-report.v1`. Code:
`commander_gym.analysis_report`. Unknown/missing profiles fail closed.

The installed `analyze-commander-games` 1.0.0 skill supplies the semantic requirements
below. Its example specifies a hash object but describes finding fields in prose;
that does not establish the actual producer's field names. This profile makes the
serialization explicit for review. Parent must align the personal skill and an
actual private producer report before merge/adoption. Synthetic round-trip tests
prove recorder compatibility; they do not prove live producer adoption.

## Hash mapping and recorder compatibility

The recorder's `finish_analysis` joins the outer `run_id`, string `artifact_hash`,
and `analysis_version` to the verified manifest. It verifies bytes and writes a
report receipt, but does not certify report completeness. The improvement adapter
therefore independently validates this full profile after verifying that receipt.

`recorder_profile_from_skill(skill_report, verified_manifest)` is a pure helper.
It does not write files, infer aliases, or modify a source report. It accepts the
skill's hash object only when its SHA-256 value and ordered scope match the existing
verified manifest exactly. It preserves that object as `artifact_hash_provenance`,
sets its `basis` to `canonical_manifest_artifact_descriptors_v1`, and puts the same
hex digest into outer `artifact_hash`. It adds `report_profile` and validates all
other fields. The manifest's hash is over canonical JSON of its ordered artifact
descriptors, not a hash of concatenated raw bytes. Scope is exactly
`[artifact['path'] for artifact in manifest['artifacts']]`; report/receipt paths and
`manifest.json` are not added to that artifact scope. Algorithm, value and ordered
scope are preserved; hash basis is explicitly recorded.

Thus the producer may construct the complete skill report with a hash object,
apply the pure conversion after verifying the manifest, then call the existing
`finish_analysis` with the resulting profile. Already saved reports are never
silently normalized or rewritten. Reports with another field map need an explicit
reviewed adapter/profile version; there is no best-effort guessing.

## Complete field map

| Installed skill concept | Explicit profile field and required semantics |
|---|---|
| Analysis/run/artifact identity | `analysis_version`, `run_id`, scalar `artifact_hash`, `artifact_hash_provenance`, `idempotency_key` exactly join manifest/receipt |
| Terminal classification | `classification`, boolean `native_completed`, explicit string/null `terminal_reason`; qualified/native outcome cannot be invented from an operator stop |
| Outcome | `outcome.recorded_result` explicit, boolean `qualified`, array `evidence` of structured references |
| Pins | `pins` object with all seven recorder sections (`engine`, `gym`, `models`, `decks`, `bindings`, `config`, `rng`); unknown revisions stay explicit null, never guessed |
| Integrity | `integrity.checks`, `gaps` arrays and `stable_after_review=true`; adapter independently verifies finalized source/report stability |
| Coverage | `coverage.total_decisions`, `usable_seat_observations`, `known_legal_sets`, `reviewed_decisions`: explicit nonnegative integer/null; `selection_method`, `exclusions` |
| Telemetry | `telemetry.by_seat_model`, `calls`, `retries`, `invalid_actions`, `fallbacks`, `timeouts`, `tokens`, `latency`, `wall_time`, `cost`, `missing_fields`; unavailable values remain explicit null |
| Cost provenance | `cost.amount`, `currency`, `basis` (`recorded`, `estimated`, `unknown`), `pricing_source`; no computed or inferred pricing here |
| Stable finding and attribution | Each `findings[]` has `finding_id`, `category`, `severity`, `confidence`, `confidence_rationale`, `alternative_explanations` |
| Time-local locus | `seat`, `turn`, `phase` explicit value/null; `decision_ids`, `event_ids`, `row_ids`, `relevant_cards_actions` explicit arrays |
| Evidence links | `evidence_refs[]`: exactly one `path` or `link`, plus `pointer`, `line`, `line_span`, `row_id` or `event_id` |
| Observation available then | `observation_available_then`: `{summary, evidence_refs}` or explicit null |
| Selected native action | `selected_action`: `{summary, evidence_refs}` or explicit null; no invented observed choice |
| Verified legal alternatives | `legal_alternatives`: array of `{action, feasibility, evidence_refs}` or null; feasibility describes timing/targets/resources supported by referenced evidence |
| Ex-ante comparison | `ex_ante_comparison`: supported comparison text or explicit null |
| Impact/uncertainty/counterevidence | `impact`, `counterevidence`, `missing_information`; unavailable decision evidence requires explicit missing-information reasons |
| Outcome separation | `outcome_used_for_decision_quality=false`; human review must still inspect prose for hindsight/hidden-information leakage |
| Recommended next step/permission | `recommended_next_step`, boolean `needs_permission` |
| Learning candidate | `learning_candidates[]`: `hypothesis`, `supporting_finding_ids`, `competing_explanations`, `required_evidence`, `bounded_validation_idea`, `status=unvalidated`; IDs join report findings |
| Privacy/summary | `privacy_retention_gaps`, `summary` |

Categories are exactly the installed skill's eight categories. Severity and
confidence remain separate. Empty arrays explicitly record no entries; null
observation/action/legal/comparison fields retain unavailable evidence without
inventing choices. Such a candidate may be ingested in `insufficient_evidence` and
planned as a hypothesis, but cannot qualify as validated strategic evidence.

References with `path` must be relative portable paths present in the verified
source artifact manifest. References with `link` must be HTTPS locators without
embedded credentials; this module does not fetch or certify access to that link.
Every reference also needs a valid nonempty RFC6901-style pointer, positive line,
ordered positive line span, or stable row/event ID. `{}`, raw strings, root-only
locators and unmanifested local paths are rejected. Report and receipt files must
be private regular files without symlinks; permission checks use open descriptors.

## Verification and remaining boundary

`tests/_analysis_report_fixture.py` builds a full project-owned skill-compatible
aborted-run diagnostic with unavailable pilot choices explicitly null.
`tests/test_analysis_report.py` converts it without changing source values, saves
it through the actual recorder claim/completion API, and ingests the verified
receipt into the improvement ledger. Tests also show the recorder can complete a
malformed report while the stricter adapter rejects it. Missing profile fields,
wrong hash scope, unresolved learning findings, imprecise references, invented
native completion and public-readable report/receipt files fail.

This establishes the code-level mapping and recorder-compatible output, including
all finding semantics rather than three field names. It does not assert that an
existing private report already uses the profile. Parent/recorder producer review
and an actual-output check remain required before merge. Private reports and any
personal skill edits remain outside this public repository and this task's writes.
