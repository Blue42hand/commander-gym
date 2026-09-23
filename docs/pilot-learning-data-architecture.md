# Pilot learning, identity, evidence, and storage architecture

This document defines the long-term Commander Gym architecture agreed on 2026-09-23. It complements `ARCHITECTURE.md`: Argentum remains the authoritative game/environment; Commander Gym owns the artificial player and the machinery that improves it.

## Long-term pilot objective

The mature Commander Gym pilot should be primarily local.

Frontier/cloud models such as Luna are valuable as teachers, evaluators, and escalation resources, but the steady-state runtime should progressively move recurring decisions into cheaper reusable competence.

Conceptually:

```text
Argentum
  -> seat-safe observation + native legal actions/decisions
  -> Commander Gym perception/routing
       -> certified mechanical handler
       -> executable Python skill
       -> learned specialist/value policy
       -> Magic-specialized local generalist
       -> frontier-model escalation
  -> native Argentum action/decision response
```

The router must preserve meaningful strategic decisions. A decision moves to a cheaper layer only after replay/held-out/shadow evidence shows that doing so is safe and useful.

A **pilot** is therefore the complete decision-making system that occupies a seat. It is not synonymous with an LLM.

## Progressive compilation of experience

Commander Gym should learn in more than one way.

Repeated general reasoning can crystallize into:

- deterministic/mechanical automation;
- executable Python skills/macros;
- learned specialist policies;
- learned value/critic models;
- improved routing;
- a fine-tuned Magic-specialized local generalist model.

The desired loop is:

```text
game experience
  -> immutable decision/trajectory evidence
  -> annotation/adjudication/counterfactual evaluation
  -> candidate rule / skill / policy / training example
  -> replay + held-out evaluation
  -> shadow mode
  -> promote, revise, or reject
```

A live LLM must not directly rewrite production automation. LLM-proposed automation is a versioned candidate artifact that must pass tests and evaluation before promotion.

Initial specialization should favor high-quality imitation/distillation from strong teacher decisions and adjudication. RL/value learning is expected to become useful for long-horizon behavior, critics, specialists, routing, and eventually the local generalist, but sparse game win/loss is not the only or necessarily first training signal.

## Magic-specialized local generalist

The intended long-term neural substrate is a fine-tuned open model small enough to run locally.

It should internalize transferable Magic competence such as:

- sequencing and resource management;
- tempo/card-advantage/resource conversion;
- combat and interaction timing;
- threat assessment;
- long-horizon planning;
- multiplayer incentives;
- recognizing engines, combo structures, and strategic roles.

Argentum remains authoritative for rules and legal actions. Current card text and mutable card/rules knowledge should remain externally retrievable/versioned rather than becoming the sole responsibility of model weights.

Deck-specific knowledge remains separately versioned from general Magic competence.

## Canonical identity model

The public schemas must keep the following concepts distinct.

### Deck

A deck is only the playable deck artifact.

It has a stable lineage ID plus immutable revisions/digests containing its exact list, format metadata, commander(s) where applicable, and source/import provenance.

Changing the pilot must not change deck identity.

### DeckKnowledge

Deck-specific knowledge is separately versioned:

- primer and strategic objectives;
- mulligan guidance;
- known lines/combos/interactions;
- deck-specific annotations;
- compatibility with deck revisions.

Changing a primer must not create a new deck revision.

### Pilot

A pilot revision describes the whole player-side decision system:

- harness/router/skill-set revisions;
- deterministic handlers;
- generalist model and inference configuration;
- specialist policy/value refs;
- prompts/policy/retrieval configuration;
- escalation policy;
- immutable digest.

Changing the deck must not change pilot identity.

### Binding

A Binding is the immutable configuration that occupied a seat:

```text
Deck revision
+ DeckKnowledge revision
+ Pilot revision
+ binding-specific specialist/overrides, if any
= Binding
```

Runs and decisions should reference the Binding as their canonical seat configuration while retaining readable denormalized IDs where useful.

## Evidence and learning lineage

Use the lineage:

```text
Deck -> DeckKnowledge -> Pilot -> Binding
                               |
                               v
                              Run
                               |
                               v
                            Decision
                               |
                               v
                           Annotation
                               |
                               v
                            Dataset
                               |
                               v
                             Model
```

Given any training example, Commander Gym must be able to trace backward to:

- exact game/run and seat;
- exact Deck, DeckKnowledge, Pilot, and Binding revisions;
- engine revision/schema;
- original seat-visible observation;
- semantic legal action/decision space;
- subsystem that actually selected the response;
- routing/escalation metadata.

A trained model/checkpoint must trace to its dataset manifests and ultimately to source evidence.

## Three evidence layers

### Raw evidence

Immutable record of what happened. Do not rewrite it because a later evaluator disagrees with an old action.

Raw evidence includes the exact seat-visible input, legal choices, selected response, resulting authoritative state identity, run/participant provenance, actual decision path, inference telemetry, and terminal/failure classification.

### Annotations

Append-only, independently versioned judgments attached to raw run/decision IDs.

Examples:

- decision class;
- teacher/critic adjudication;
- material-error flag;
- preferred alternative;
- value estimate;
- counterfactual result;
- human review.

Changing an annotation never changes raw evidence.

### Datasets

A dataset is a versioned manifest over evidence, not merely an exported file.

Record purpose, source population/query, transforms, exclusions, required annotation versions, split policy, source digests, generator revision, and dataset digest.

JSONL/Parquet/etc. are derived products that may be regenerated.

Keep model-facing `input`, supervision `target`, and `provenance` separate so future outcomes or privileged information cannot silently leak into training inputs.

## Decision-path provenance

The nominal pilot and the actual decision maker are not always the same thing.

For example one pilot may route consecutive decisions through:

```text
mechanical Python
local combat policy
local Magic model
local Magic model -> Luna escalation
deterministic combo skill
```

Every durable decision record must therefore identify both:

- the nominal Pilot revision; and
- the actual routing path/subsystem/model/skill that selected the action.

This enables queries such as “all Luna teacher decisions,” “all local-model escalations,” or “all automated decisions later contradicted by an evaluator.”

## Repository and storage boundary

### Public `commander-gym`

The reusable product: code, schemas, loaders, packaging, synthetic fixtures, generic pilots/skills/training/evaluation machinery, and storage interfaces.

Another user should be able to clone/install it, provide their own instance data, and run Commander Gym without access to Blue42hand private assets.

### Private `commander-gym-private`

One user instance: human-maintained deck revisions, deck knowledge, pilot manifests, bindings, rosters, preferences, and small private experiment definitions/references.

It should not retain generic product implementation after migration.

### External data store

Bulk generated artifacts do not belong in Git:

- runs and trajectories;
- annotations;
- derived datasets;
- model/checkpoint artifacts;
- large experiment outputs.

The data-store location is configurable.

## Location-independent storage

Durable identities must not contain host-specific paths.

A deployment should support a configurable root such as:

```text
storage.root/
  artifacts/
  runs/
  annotations/
  datasets/
  models/
  catalog/
  logs/
  cache/
```

Individual routes may be overridden. The host may implement the filesystem using local disks, ZFS, NFS, SMB, or another mount without Commander Gym depending on those technologies.

Initially prefer a filesystem/object-store abstraction rather than NAS-specific application behavior.

Separate durable data from regenerable caches/build/temp material. Record per-run raw/compressed storage accounting so capacity planning is based on measured MB/game.

## Portable node

The current Linode is a development host, not part of Commander Gym's identity.

The intended deployment can colocate:

- Argentum game/gym services;
- Commander Gym;
- local model serving;
- fast working storage;
- large local archival storage.

Packaging should make moving that node between hosts an operational migration rather than an architecture change. Remote access/tunnels/VPNs are replaceable deployment infrastructure.

See issues #5, #53, #73, and #74 for implementation tracking.


## Loading a Binding into a game seat

A Binding is not merely provenance recorded after a game. It is the **pre-game source of truth** for configuring an artificial seat.

### Argentum Gym

Commander Gym resolves each Binding before environment creation and derives both:

- the exact Argentum player deck/commander configuration; and
- the exact Commander Gym Pilot instance/configuration for that seat.

A Gym pod should therefore be launchable from Binding IDs plus game-level settings such as seed/format. The runner must not accept an unrelated deck list and pilot configuration that happen to be assigned to the same seat.

### Argentum game-server / GUI

Argentum already treats AI deck choice as per-seat data. Its external AI controller selection is currently more global, so Commander Gym cannot yet carry one Binding cleanly into a GUI seat.

The intended boundary is:

```text
Commander Gym Binding
      |
      +-- exact Deck ----------> Argentum per-seat deck configuration
      |
      +-- Pilot/Profile ID ----> Argentum per-seat external-controller profile
      |
      +-- DeckKnowledge -------> stays inside Commander Gym
```

Argentum should expose only a generic per-seat controller/profile seam. The profile identifier is opaque to Argentum; for the Commander Gym provider it is the Binding ID.

When the host selects a Commander Gym Binding in the Argentum GUI, the user-facing operation should configure the bound deck and controller profile together. Argentum may retain deck and controller as separate generic internal axes, but a complete provider preset must not leave a startable half-updated seat.

The Commander Gym provider/sidecar resolves the Binding, constructs the Pilot, supplies/validates the expected deck, and records the same Binding provenance used by Gym runs.

Unknown/stale bindings or a deck mismatch fail closed rather than falling back to a generic pilot.

Tracked by Commander Gym #76 and Argentum fork #196.
