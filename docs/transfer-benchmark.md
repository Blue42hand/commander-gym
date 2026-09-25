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
readiness fails closed until all twelve canonical slices are represented. Reclassifying
a case changes only the capability-sidecar fingerprint, not the benchmark-suite
fingerprint.

## Leakage and provenance

#113 owns external model/dataset provenance and leakage-group semantics. #114 must not
invent a competing external-source schema.

The transfer reporter consumes canonical `sha256:<64 lowercase hex>` provenance
artifact IDs for each cohort. External-derived benchmark evidence additionally consumes
an exact `case_id -> (deduplication_identity, leakage_group_id)` mapping supplied by
the #113 external-source record index/dataset layer. Pure project-synthetic scenarios
remain Commander Gym-owned evidence and must not be mislabeled as external provenance.

The mapping is fingerprinted into the transfer report. #113 remains responsible for
proving that one source game/reconstruction and all of its derivatives cannot straddle
train/validation/frozen-test boundaries.

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
- explicit escalation rate and reason;
- latency, input/output tokens, and cost when available;
- preferred-action delta versus the baseline;
- action-selection disagreement across all four cohorts.

The same metrics are reported overall and per capability slice. Disagreement is
evidence for adjudication/review; agreement with the frontier reference is not
automatically correctness.

## Dependencies

- #112 supplies candidate pretrained models/representations and their concrete adapter
  plans.
- #113 supplies external model/data provenance, licensing, source grouping, and
  leakage-safe manifests.
- #115 supplies the curated Commander-specific supervision/curriculum.
- #53/#73 provide the existing frozen benchmark, lineage, and model-independent Pilot
  foundations.

No #114 code should duplicate those workstreams.
