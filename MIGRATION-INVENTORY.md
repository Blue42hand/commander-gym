# Migration inventory from `commander-gym-private`

This document classifies the durable contents of `Blue42hand/commander-gym-private` under the three-repository architecture:

- `Blue42hand/argentum-engine`: generally reusable Magic/agent platform capabilities.
- `Blue42hand/commander-gym`: public Commander-focused research/evaluation harness.
- `Blue42hand/commander-gym-private`: private deck/pilot/model/data assets and migration evidence.

Commander remains the primary focus. Core contracts should avoid unnecessary Commander-only assumptions when a format-neutral design costs essentially nothing.

## Inventory snapshot

At the start of this inventory, `commander-gym-private` contains 503 tracked files. The largest areas are:

- `forge-python-llm/`: 304 files
- `decks/`: 136 files
- `test-game/`: 18 files
- `integrations/`: 13 files
- `.github/workflows/`: 11 files
- `scripts/`: 9 files

The migration should be selective. Do not copy the old repository wholesale.

## 1. Migrate to public Commander Gym

These are genuinely research/evaluation-layer capabilities.

### Training/evaluation record schemas

Direct migration candidates:

- `forge-python-llm/gym_training_records.py`
- `forge-python-llm/gym_benchmark.py`
- `forge-python-llm/gym_benchmark_runner.py`
- `forge-python-llm/gym_training_guard.py`
- corresponding engine-independent tests

Why: these define experiment records, benchmark judgments, held-out protections, and pilot evaluation. They do not need to own game rules or engine state.

Required cleanup:
- rename legacy `gym_*` module names into the new public package structure;
- replace Forge-era wording with Argentum provenance where relevant;
- preserve explicit schema versioning.

### Package / deck-policy experimental identity

Migrate after a small schema revision:

- `forge-python-llm/gym_deck_package.py`
- `forge-python-llm/DECK-PACKAGE.md`
- `forge-python-llm/tests/test_deck_package.py`

Current blocker: `DeckPackage.commander` is mandatory. The public contract should make format identity explicit and make commander/commander-like roles format-specific rather than mandatory core fields.

Recommended public direction:

```text
GamePackage / DeckPackage
  format
  deck artifact
  primer/policy artifact
  format-specific metadata
  deterministic policy
  generalist base
  specialist
  training provenance
  evaluation/metapool refs
```

Commander packages can still require commander metadata through Commander-specific validation.

### Benchmark / training documentation and roadmap

Migrate and update:

- `AI-DECK-OPTIMIZATION-ROADMAP.md`
- `forge-python-llm/BENCHMARK-TRAINING-DATA.md`
- `forge-python-llm/GENERALIST-BENCHMARK-V1.md`

The roadmap belongs in the public research project, but references to Forge and the old network architecture should be removed as migration occurs.

### Sparse ML feature prototype

Migrate as experimental research code:

- `forge-python-llm/gym_sparse_features.py`
- `forge-python-llm/tests/test_sparse_features.py`

Why: this is a research representation over decision records, not engine functionality.

Keep it explicitly experimental. Do not make it the authoritative Argentum observation format.

### Provider/model interfaces — migrate by extraction, not direct copy

Public Commander Gym should eventually own provider/pilot configuration and model-comparison plumbing, but the current files are heavily Forge-packet-specific:

- `gym_model_gateway.py`
- `gym_model_gateway_limited.py`
- `gym_compact_gateway.py`
- `gym_ollama_codec.py`
- `gym_ollama_transport.py`
- `gym_rate_limit.py`
- provider telemetry/cost helpers

Disposition:
- extract generic provider request/response, rate-limit, telemetry, structured-output, and retry/fail-closed behavior;
- do not migrate Forge packet reconstruction, Forge-specific prompt guidance, or old state-patch assumptions;
- bind the new public pilot interface directly to Argentum observations/decisions.

### Durable experiment/run records — migrate the concept, refactor implementation

Relevant current files:

- `gym_records.py`
- related report/ledger helpers

Public Commander Gym should own immutable experiment/run provenance and terminal classification.

Do not directly carry forward:
- retired Google Drive sync behavior;
- Forge-specific report fields;
- legacy work-directory assumptions.

### Archidekt / external deck-source integration — public code, private inputs

Likely public after refactor:

- generic Archidekt read/import client code under `integrations/mtg-mcp/`;
- `scripts/import_archidekt_collection.py`;
- `scripts/fetch_archidekt_sources.py`;
- reusable deck-source normalization helpers.

Keep private:
- registered private deck IDs/source lists;
- imported snapshots;
- generated primers;
- local installation/login state;
- machine-specific configuration.

The public interface should produce versioned package artifacts without requiring private deck content in the repository.

## 2. Prefer Argentum / upstream

These capabilities should not become permanent Commander Gym platform code.

### Durable semantic action/decision identity

Current prototype:

- `forge-python-llm/argentum_canonical.py`
- `forge-python-llm/tests/test_argentum_canonical.py`

The prototype currently hashes Argentum legal-action semantics to compensate for ephemeral numeric action IDs.

This is generally useful to:
- training;
- replay;
- debugging;
- decision logging;
- transposition/evaluation tooling.

Preferred disposition: add an Argentum-native stable semantic action/decision fingerprint or equivalent reusable provenance primitive. Commander Gym records should consume it rather than maintaining a competing canonical action ontology.

If Argentum intentionally declines to own a stable semantic ID, keep only the smallest research-record projection in public Commander Gym.

### Generic agent/controller API improvements

Any future work involving:
- agent/controller registration;
- provider-neutral game-controller seams;
- legal-action representation;
- hidden-information observation projection;
- state/schema identity;
- decision lifecycle;
- replay/snapshot/fork/restore;
- batch/vector execution;
- generic telemetry hooks;
- MCTS/self-play infrastructure

belongs in `argentum-engine` and should be designed for upstream contribution.

### Card/rules semantics

Do not recreate a separate Commander Gym card ontology from Forge-era code.

Argentum already owns:
- rules execution;
- its SDK/card definitions;
- Assay/mtgish tooling;
- card capability coverage.

Public Commander Gym may consume card semantics for experiment features, but generic semantic representation improvements should prefer Argentum.

### Generic deck legality / engine capability checks

Where old code tries to independently decide whether a deck or card interaction is legal/playable, prefer Argentum's authoritative registry/format/rules facilities.

Scryfall remains appropriate for source metadata and Oracle evidence, but not as a second rules engine.

## 3. Keep private

### All actual deck libraries and revisions

Keep private by default:

- `decks/**`
- `test-game/**`

This includes:
- decklists;
- Archidekt IDs and exact revisions;
- source snapshots;
- descriptions;
- Oracle snapshots tied to private deck revisions;
- generated references;
- validation results tied to those decks.

### Primers and deck-specific pilot policy

Keep private:

- `decks/**/primers/**`
- `*-Gym-Primer.md`
- `pilot.md`
- `pilot.json`
- deck-specific deterministic policy files
- combo/execution contracts tied to a particular deck

Public Commander Gym should define package schemas and loader interfaces, not publish private package contents.

### Specialized models and future learned deck artifacts

Keep private:
- deck-specialist LoRAs/adapters;
- checkpoints;
- deck-local policy/value heads;
- deck-specific training corpora;
- private teacher/adjudication data;
- private trajectories.

### Existing benchmark corpus derived from private decks

Keep private for now:

- `forge-python-llm/benchmark/generalist-v1.jsonl`
- deck-derived benchmark results and recorded packets

The benchmark *framework* is public. The current corpus references private deck/primer material and should not be copied automatically.

Public Commander Gym should eventually ship:
- synthetic fixtures, and/or
- deliberately public sample packages,
for tests and examples.

### Argentum qualification evidence against private decks

Keep private:

- `argentum-qualification/**`
- `decks/argentum-coverage*.json`
- `scripts/run_argentum_coverage.py` while it is tied to the private corpus

Engine/card gaps discovered through this process may produce public/upstream Argentum issues without publishing the private deck corpus unnecessarily.

## 4. Archive / retire rather than migrate

The old Forge execution stack should not move into the new public repo.

### Forge Java bridge and network stack

Archive in `commander-gym-private` as migration history:

- `GymForgeMain.java`
- `GymNetwork*.java`
- `GymDesktop*.java`
- `GymForgeAiAdvisor.java`
- related Forge bridge checks

### Forge Python runtime/transport layers

Archive/retire:

- `gym_forge*.py`
- `gym_network*.py`
- Forge process/runtime/source identity helpers
- Forge-specific payment/combat/priority normalization whose responsibility now belongs to Argentum
- old Forge smoke/integration tests

Do not preserve these merely to maintain compatibility after Argentum parity is proven.

### Forge packaging/runtime infrastructure

Archive/retire:

- `scripts/setup_forge*.py`
- `scripts/build_forge_network_source.py`
- Forge runtime-bundle tooling;
- Forge runtime release workflows;
- Forge local-network architecture docs.

Forge can remain available as a targeted differential/reference oracle without keeping the old production architecture alive.

### Old chat/process lifecycle

Archive/retire:
- chat-runner snapshot/process-supervision code;
- old local table server/client layers;
- Drive-sync infrastructure;
- startup/cache work specific to the Forge process model.

Only reusable research concepts should survive.

## 5. Needs refactor before placement

These areas contain useful ideas mixed with obsolete platform assumptions.

### Pilot protocol

Current:
- `gym_pilot_protocol.py`
- `DIRECT-PILOT-TEST-PROMPT.md`
- related command/output contracts

The current protocol is deeply Forge-specific: it teaches Forge input classes, actionability metadata, priority leases, native payments, and Forge-specific automation.

Disposition:
- do not copy it directly;
- extract high-level research requirements into a new Argentum-native pilot contract:
  - seat-authorized information only;
  - model is strategic judgment, not rules authority;
  - fail closed on malformed/stale/unknown outcomes;
  - deterministic handling of truly mechanical choices;
  - preserve meaningful strategic choices;
  - provider-neutral output;
- let Argentum define execution/legal-action semantics.

### Imported deck helpers

Current:
- `gym_imported_decks.py`
- `gym_imported_oracle.py`
- `gym_imported_primers.py`

Split:
- source normalization and package provenance: public Commander Gym;
- private deck/source registries and generated artifacts: private;
- legality/rules capability checks: Argentum;
- exact Oracle/Scryfall evidence fetching: public integration utility if still useful.

### CI

Do not copy old workflows wholesale.

New public CI should initially cover:
- public Python package unit tests;
- schema/package compatibility tests;
- lint/type checks if adopted;
- later, a pinned Argentum integration smoke test.

Private workflows should continue to cover:
- private deck imports;
- primer generation;
- private package validation;
- private-corpus Argentum coverage.

## 6. First migration slices

Recommended implementation order:

1. **Public schema core**
   - training records;
   - benchmark framework/runner;
   - training leakage guard;
   - tests.

2. **Package identity v2**
   - migrate `DeckPackage`;
   - add explicit format identity;
   - make Commander metadata format-specific;
   - add public synthetic fixture package.

3. **Research docs**
   - migrate/update AI optimization roadmap;
   - benchmark/training contract;
   - package contract.

4. **Provider-neutral pilot layer**
   - define a small Argentum-native pilot interface;
   - extract generic provider/rate-limit/telemetry code from the old gateways;
   - no Forge packet compatibility.

5. **Argentum integration**
   - consume native Argentum observations/legal actions;
   - upstream generally reusable action/decision identity improvements rather than building a large adapter.

6. **Archidekt/package-source integration**
   - migrate generic code;
   - keep private source registries and deck contents private.

7. **Experiment/run orchestration**
   - build only the thin lifecycle needed to configure pilots, launch/drive Argentum Gym environments, record results, and run benchmarks/batches.

## 7. Explicit non-goals for migration

Do not migrate just because code exists.

Specifically do not rebuild in public Commander Gym:
- another rules engine;
- another multiplayer server;
- a parallel canonical game-state model;
- a Forge compatibility layer;
- an alternate snapshot/fork/search implementation;
- a second generic LLM game-controller framework if Argentum can expose the needed seam.

The target is a smaller codebase than the old project, not a renamed copy of it.
