# 1v1-to-Commander transfer benchmark

Issue #114 measures whether already learned 1v1 Magic competence reduces the amount of
Commander-specific training Commander Gym needs.

This document describes **preparation only** while foundation milestone #77 remains
open. It does not authorize ordinary gameplay testing, self-play, model training, or
claims about transfer quality.

## Gate

The substantive four-cohort comparison starts only after:

1. #77 closes; and
2. #72 establishes a stable full-game Pilot path whose results are not dominated by
   transport/schema failures.

Before then it is appropriate to freeze benchmark fixtures, define capability tags,
build leakage-safe grouping identities, and prepare model adapters.

## Canonical cohorts

Every substantive #114 report compares the exact same held-out cases across:

- `baseline` — local/general model with no additional Magic gameplay training;
- `one_v_one_enriched` — the comparable candidate enriched with reusable 1v1 Magic
  competence;
- `commander_adapted` — that candidate plus Commander-specific supervision and/or
  DeckKnowledge;
- `frontier_reference` — Luna/frontier policy on the same cases, as a reference and
  **not** assumed ground truth.

Imported gameplay models remain behind the normal Commander Gym Pilot boundary.
Forge/XMage origin does not make those engines runtime dependencies.

## Capability sidecar

A held-out case can exercise more than one capability, but transfer classification is
research metadata rather than immutable benchmark evidence. Store it in the
versioned/fingerprinted `commander-gym-transfer-capabilities@v1` sidecar keyed by
`case_id`; do not mutate `BenchmarkCase.tags` or `BenchmarkScenario.tags` merely
to reclassify transfer capability.

The initial frozen taxonomy is:

- `mulligan`
- `sequencing`
- `combat_attack`
- `combat_block`
- `resource_use`
- `interaction_timing`
- `threat_assessment`
- `deck_plan`
- `recovery`
- `multiplayer_interaction_allocation`
- `commander_engine`
- `long_game`

This is deliberately orthogonal to the existing single primary `category` field so a
case can count in multiple strategic slices without copying the decision. Qualification
readiness fails closed until all twelve canonical slices are represented. The sidecar
must also carry/bind the exact benchmark-suite identity it classifies: reclassifying a
case changes the capability-sidecar fingerprint, while changing benchmark content
invalidates the old sidecar even when case IDs are reused.

## Leakage and provenance

#113 owns external model/dataset provenance and leakage-group semantics. #114 must not
invent a competing external-source schema.

Cohort/report provenance must use typed canonical identities rather than a loose
bag of syntactically valid hashes. #114 binds an immutable benchmark report to the
exact canonical Pilot/run identity that produced it; #113 remains authoritative for
model/dataset derivation lineage.

Leakage provenance is likewise typed. Recorded native evidence consumes the canonical
native-game leakage identity from admitted #53 evidence; imported/reconstructed
evidence consumes #113's canonical external source/group identity plus any derivative
aliases; pure project-synthetic scenarios use their local policy-input fingerprint.
#114 verifies exact case coverage and split exclusion against those identities but does
not relabel native evidence as "external" or invent another source ontology.

The leakage sidecar/report records those canonical identities and is itself bound to
the exact benchmark revision. #113 remains responsible for proving that one source
game/reconstruction and all of its derivatives cannot straddle
train/validation/frozen-test boundaries. Group membership also drives inference:
multiple decisions from one source game/reconstruction are correlated evidence and
must not be counted as independent samples.

The benchmark runner accepts both legacy recorded `BenchmarkCase` rows and explicit
input-only `BenchmarkScenario` rows. Legacy-only suites preserve the exact
`commander-gym-benchmark-suite@v1` identity; any suite containing an input scenario
uses typed `commander-gym-benchmark-suite@v2` identity.

Input-only rows require `case_kind=input_scenario` and contain only decision type,
seat, observation schema, seat-safe observation, explicit legal actions, judgment,
held-out marker, and research tags/category. They must not claim a game/decision ID,
observed action, Pilot provenance, deck identity, outcome, or post-decision metadata.

The runner projects either case kind through the same `BenchmarkInput`, so category,
tags, judgments, recorded choices, outcomes, deck identity, leakage groups, and
provenance do not enter the evaluated policy input.

For project-synthetic leakage protection, Commander Gym separately fingerprints the
policy input with `commander-gym-benchmark-scenario-input@v1` from decision type,
seat, observation schema, canonical seat-safe observation, and semantic legal actions.
That fingerprint excludes case ID, judgment, category/tags, and capability
classification so copying or relabeling the same held-out input remains blocked from
training.

## Measurements

Do not reduce transfer to win rate. Each cohort records:

- legal/preferred/invalid-output/error rates;
- adjudication coverage and valid strategic-selection coverage;
- explicit escalation rate, reason, and canonical producer/subsystem identity;
- latency plus raw usage counters with explicit telemetry coverage;
- cost only as a derived value under an explicit compatible accounting method;
- paired preferred-action deltas versus the baseline;
- strategic disagreement across all four cohorts only where every compared cohort
  produced a legal, non-error selection;
- raw failure/invalid divergence separately from strategic disagreement;
- independent leakage-group counts and group-aware uncertainty overall/per capability;
- replicate/run variance for stochastic inference separately from source-group
  uncertainty.

Case-level diagnostics remain available, but decision-relevant effect estimates are
paired and group-aware so a game with many recorded decisions cannot dominate by row
count. Disagreement is evidence for adjudication/review; agreement with the frontier
reference is not automatically correctness.

## Frozen report validation contract

Benchmark reports are untrusted execution evidence until validated against the exact
frozen cases. Both `compare_benchmark_reports()` and the four-cohort transfer summary
must consume one canonical validator/scorer rather than maintain separate safety
semantics.

That validator must:

- require exact benchmark-suite identity and exact case membership/category;
- recompute `legal`, `preferred`, rank, and `invalid_output` from the frozen case
  plus `selected_action_id` via `score_action()`;
- reject forged/stale report scoring fields;
- require finite non-negative latency on every row;
- require optional token counts to be non-negative integers and optional cost values to
  be finite/non-negative;
- expose telemetry coverage and keep aggregates null when optional telemetry is
  incomplete rather than silently undercounting;
- preserve provider/error rows as failures rather than treating their empty/invalid
  selections as strategic changes;
- mark strategic selection/disagreement as valid only for legal, non-error selections;
- distinguish unadjudicated cases (for example an empty preferred-action set) from
  strategic misses.

The same immutable report must validate identically through generic pairwise comparison
and transfer comparison. Runtime telemetry/report identity is separate from the
deterministic frozen-suite identity; content-addressing freezes the run evidence, while
the shared validator proves its semantic consistency.

## Qualification and attribution contract

A transfer result is decision-relevant only when the comparison can attribute the
observed difference to the intended factor rather than to benchmark bookkeeping,
runtime assistance, or correlated samples.

Qualification-ready reporting therefore needs all of the following:

- both pairwise and four-cohort comparison paths use one frozen-case validator that
  recomputes legality/preference/invalid-output from the benchmark cases instead of
  trusting report booleans;
- immutable run/report evidence is content-addressed and bound to the exact canonical
  Pilot identity and runner/runtime revision that produced it;
- benchmark observation bytes carry recognized seat-visible projection/boundary
  provenance, while #113/#53 own imported/native evidence admission and leakage
  identities;
- capability classification is fingerprinted separately but bound to the exact
  benchmark-suite revision it classifies;
- source-game/reconstruction leakage groups affect inference, not only admission:
  decision-level diagnostics remain available, but transfer effects and uncertainty
  are group-aware so repeated decisions from one game do not count as independent
  evidence;
- the experiment declares whether each contrast is causal or observational. A causal
  baseline -> 1v1 contrast must hold the Pilot scaffold fixed except for the admitted
  1v1-enrichment derivation; Commander adaptation must descend from that exact
  evaluated 1v1 checkpoint and vary only by declared Commander factors;
- stochastic cohorts use a predeclared replicate/seed policy (or empirical repeated
  runs when a provider has no controllable seed), while deterministic cohorts bind
  exact decoding/runtime configuration and prove reproducible strategic selections;
- per-decision producer/subsystem identity is recorded from canonical Pilot routing
  provenance so composite seat-level quality is reported separately from autonomous
  candidate quality/coverage. Frontier-assisted selections are not evidence that the
  underlying local candidate produced the action;
- immutable raw usage telemetry is kept separate from dollar valuation. Dollar cost is
  derived only under an explicit compatible accounting method/tariff with sufficient
  token/usage coverage.

`qualification_ready` must fail closed when any required evidence above is missing.
Coverage thresholds and uncertainty rules are versioned comparison-design metadata,
not universal Commander constants.

## Dependencies

- #112 supplies candidate pretrained models/representations and their concrete adapter
  plans.
- #113 supplies external model/data provenance, licensing, source grouping, and
  leakage-safe manifests.
- #115 supplies the curated Commander-specific supervision/curriculum.
- #53/#73 provide the existing frozen benchmark, lineage, and model-independent Pilot
  foundations.

No #114 code should duplicate those workstreams.
