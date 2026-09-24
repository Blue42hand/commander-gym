# 1v1-to-Commander transfer benchmark

Tracking: #114. Candidate audit: #112. External provenance/import contracts: #113. Commander curriculum: #115.

## Gate

This document defines the frozen benchmark contract only. While #77 is open, do not run ordinary gameplay, mass training, or substantive transfer comparisons. Once #77 closes, #72 must establish the stable full-game pilot path before model-vs-model Commander comparisons are treated as qualification evidence.

Offline fixture/schema preparation and representation-only probes are allowed before that gate because they do not mutate games or generate gameplay claims.

## Experimental cohorts

Every substantive comparison must use the exact same fingerprinted held-out suite and legal-action surface.

1. **baseline** — locally runnable general model/policy without added Magic gameplay training.
2. **one_v_one_enriched** — same target family or nearest valid comparison initialized/enriched with audited 1v1 Magic competence.
3. **commander_adapted** — the 1v1-enriched candidate plus Commander-specific supervision/DeckKnowledge.
4. **frontier_reference** — Luna/frontier teacher on the same cases. This is a reference, not assumed ground truth.

Structurally different policies may participate through the common benchmark Pilot contract. Do not force imported gameplay networks into language-model semantics.

## Required capability tags

A benchmark case may exercise multiple capabilities. Keep the existing single `category` for backward compatibility, and add one or more canonical tags using `capability:<name>`.

Canonical #114 capabilities:

- `capability:mulligan`
- `capability:sequencing`
- `capability:combat_attack`
- `capability:combat_block`
- `capability:resource_use`
- `capability:interaction_timing`
- `capability:threat_assessment`
- `capability:deck_plan`
- `capability:recovery`
- `capability:multiplayer_interaction_allocation`
- `capability:commander_engine`
- `capability:long_game`

Reports should aggregate each canonical capability independently so a multi-tag case contributes to every relevant slice.

## Benchmark identity and leakage safety

The existing benchmark-suite fingerprint remains the authority for exact-case equivalence. #114 must not create a second evidence identity system.

Every case must additionally be traceable to its source/derivation family through #113/#53 lineage. Frozen-test material must be grouped so that the same native game, external game, reconstruction family, source document derivative, or synthetic scenario family cannot cross into train/validation.

For #115 curriculum material, consume the named dataset lineage rather than scraping/reconstructing the source again in #114.

## Metrics

Do not collapse evaluation into win rate.

Per case retain:

- selected action;
- legal / invalid output;
- preferred/adjudicated status and rank where available;
- pilot/provider failure;
- latency;
- input/output tokens and cost when applicable;
- escalation status/path/reason when the pilot exposes it;
- cohort disagreement on selected action.

Aggregate:

- legality, invalid-output, preferred/adjudicated and failure rates;
- latency/token/cost totals and means;
- escalation rate by capability;
- pairwise improvements/regressions;
- multi-cohort disagreement rate and cases where disagreement changes adjudicated quality;
- every metric by canonical capability tag as well as overall/category views.

## Talor/Austinio candidate contract

#112 identified the obtainable Talor/Austinio Forge checkpoint as the first concrete transfer target, but its exported feature representation is not safe for direct Commander use:

- opponent face-down cards can leak paper-card identity through the original card hash features;
- the state encoder is intrinsically 1v1 and represents only one opponent context.

Therefore the first #114 experiment is **representation-only**, not a policy win-rate test:

1. derive features solely from an acting-seat-safe Argentum fixture;
2. zero/fix any feature whose original semantics expose hidden identity;
3. fail closed when an original feature cannot be mapped exactly from seat-visible information;
4. run only the frozen state encoder;
5. compare its embedding on frozen general-Magic capability probes against an untrained/control encoder;
6. do not map action heads or claim Commander competence until the representation probe passes.

A later multiplayer experiment may evaluate three explicit opponent projections plus a separately versioned aggregation layer. Never silently squeeze a four-player state into the original single-opponent slot.

Any imported checkpoint must be registered through #113. A model with privileged training inputs may still be studied diagnostically, but that provenance must remain explicit and it must not be treated as clean expert supervision.

## Readiness gates

The benchmark is ready for substantive comparison only when:

- #77 is closed;
- #72 has a stable full-game Pilot path;
- #113 provenance/import contracts are merged and the compared model/dataset artifacts are registered;
- the frozen suite contains all canonical capability slices with leakage-safe source grouping;
- #115 supplies at least one training-eligible Commander curriculum revision for the Commander-adapted cohort;
- baseline and candidate adapters consume only `BenchmarkInput`/seat-safe Argentum data;
- reports expose capability aggregation, escalation/failure telemetry, and cohort disagreement.

Until those gates pass, benchmark work is limited to design, fixtures, adapters, provenance wiring, and offline representation probes.
