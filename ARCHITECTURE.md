# Architecture

## Purpose

Commander Gym is the artificial-player and learning layer above Argentum Engine.

Its purpose is to create increasingly capable artificial Magic players whose eventual job is to play real games with humans. It should not become a second Magic rules engine, multiplayer server, or authoritative game-state implementation.

A useful shorthand is:

> **Argentum is the game. Commander Gym is the player.**

## Product objective

The final product is a player, not a trainer.

Self-play, reinforcement learning, LLM prompting, search, benchmarks, deck optimization, datasets, and experiment infrastructure are R&D machinery used to produce a better artificial Magic player.

For now, competitive strength and enjoyable casual competence are sufficiently aligned that the project should optimize a single broad objective: **become a better Magic player**. Do not create separate casual and competitive pilot architectures until pilot strength makes the distinction useful.

## Dependency objective

Commander Gym should ultimately run against **vanilla upstream Argentum**.

```text
Commander Gym
    ↓
upstream Argentum
```

The `Blue42hand/argentum-engine` fork is a staging area for integration and contributions while required capabilities are being developed. It is not intended to become a permanently divergent engine distribution owned by Commander Gym.

For each Argentum-side change, classify it as:

- **upstream candidate** — the default for generally useful game/environment work;
- **downstream extension** — exceptional work that truly should not live in Argentum;
- **transitional infrastructure** — temporary integration machinery with an explicit retirement path.

The desired steady state is no required fork delta.

This objective does not supersede the product boundary. A change does not belong in Argentum merely because it could be made generic. Strategic reasoning, learning, policy, deck construction, and player improvement remain Commander Gym responsibilities.

## The two Argentum interfaces

Commander Gym has two primary ways to interact with Argentum.

### Training interface: Argentum Gym

Use `:gym` / `:gym-server` for machine-oriented work:

- reset / step / observe loops;
- batch execution;
- snapshot / restore / fork;
- self-play;
- search and counterfactual evaluation;
- RL and other ML training;
- dataset collection;
- large-scale benchmark runs;
- privileged debug/diagnostic modes when explicitly appropriate.

This interface is optimized for learning and throughput rather than presentation to a human.

### Human-play interface: game-server

Use Argentum's `game-server` player/controller boundary when a Commander Gym pilot participates in a real game:

- the pilot occupies a player seat;
- Argentum remains authoritative for legality and state;
- hidden information is seat-safe;
- observations are those the player is entitled to receive;
- the pilot returns player intent/actions through the normal server protocol;
- humans may participate through the web client or other clients.

A pilot proven only against Gym is not yet the finished product. It must eventually work through the human-play boundary.

## Ownership

### Argentum

Prefer Argentum for capabilities that define the game or the environment in which a player acts:

- rules and authoritative state transitions;
- card implementations and format rules, including Commander;
- multiplayer lifecycle and elimination;
- legal-action enumeration and structured decisions;
- observation/state projection;
- hidden-information boundaries;
- state/schema identity;
- replay, snapshot, restore and fork;
- batch/vector execution;
- game-server player/controller seams;
- Gym APIs and transport;
- generic engine telemetry needed to understand game execution;
- generally useful search/self-play primitives when they are environment-level capabilities;
- generic card-semantics/tooling support.

Changes made in the Commander Gym Argentum fork should be designed for upstream contribution by default. The fork should not accumulate a permanent private API or alternate rules-engine surface.

### Public Commander Gym

Own the artificial player and the machinery that improves it:

- generalist and specialist pilots;
- deck construction and optimization;
- deck/pilot co-optimization;
- LLM, RL, imitation, search, evolutionary, and other learning approaches;
- local-model/Ollama and remote-model adapters;
- prompt/context/action representations;
- player memory and planning;
- policy/value models and learned evaluators;
- training loops and curricula;
- self-play experiment policy;
- benchmark definitions and evaluation;
- tournaments and metapools;
- replay/data collection and training export;
- model/pilot comparison;
- experiment tracking, provenance, and reporting;
- orchestration/job control;
- thin adapters to Argentum Gym and game-server;
- public package/schema contracts for decks, pilots, experiments, and results.

### Private Commander Gym

Treat `commander-gym-private` as one **instance repository** for the owner's use of the public product:

- exact deck lineages/revisions;
- deck knowledge/primers;
- pilot manifests/configurations;
- deck ↔ pilot bindings and rosters;
- instance preferences and small private experiment definitions/references.

Generic Commander Gym implementation belongs in the public repo. Bulk generated trajectories, annotations, datasets and model/checkpoint bytes belong in a configurable external data store rather than Git.

## Decision rule

Ask three questions:

1. **Is this about the game/environment?**  
   If it changes legality, rules, authoritative state, player-visible information, multiplayer lifecycle, cards, or the generic interface through which an external player acts, it belongs in Argentum.

2. **Is this about the player?**  
   If it changes reasoning, learning, deck construction, planning, action selection, specialization, evaluation, or improvement, it belongs in Commander Gym.

3. **If it belongs in Argentum, can another Argentum user benefit without knowing Commander Gym exists?**  
   If yes, it should normally be shaped as an upstream contribution rather than a permanent fork-only feature.

The boundary adapter belongs in Commander Gym, but should be thin. Commander Gym must not maintain a competing canonical rules/state model merely to drive Argentum.

## Argentum contribution discipline

Work in the fork should follow upstream Argentum's own contribution and architecture guidance.

For card-database work:

- Scryfall Oracle text and rulings are authoritative for implementation evidence;
- use existing SDK primitives and `Effects.*` / `Patterns.*` composition before adding engine vocabulary;
- give every card its own scenario-test file;
- manually exercise meaningful interactions and player-facing UX;
- batch only cards that compose existing primitives;
- isolate a card that requires a new effect/condition/keyword/decision primitive so the engine addition can be reviewed and tested independently.

Roster coverage and EDHREC popularity may determine **what to implement next**, but they do not lower upstream correctness or review standards.

For non-card work, prefer small generic changes that improve Argentum's external-player, Gym, multiplayer, provenance, replay, performance, or server/controller surfaces without importing Commander Gym policy into the engine.

## Deck building and piloting

Deck construction and piloting are distinct competencies, but they are tightly coupled.

Near term:

- build a strong generalist pilot substrate;
- build deck-construction and deck-understanding capability;
- evaluate both independently enough to diagnose failures.

Longer term:

- specialize pilots to decks or archetypes;
- optimize decklists and pilots together;
- retain the generalist as the transferable substrate beneath specialization.

## Competence before style branching

Do not prematurely create separate "casual" and "competitive" learning stacks.

The current shared competency target includes:

- legal, reliable play;
- good sequencing;
- resource management;
- tactical calculation;
- long-horizon planning;
- threat assessment;
- multiplayer awareness;
- deck-plan understanding;
- adaptation to opponents and table state;
- reasonable speed and robustness.

Once pilots exceed ordinary casual competence, the project may introduce configurable objectives or behavior profiles for power level, risk appetite, politics, combo tolerance, pacing, and other human-play qualities. Those should ideally be strong players with different objectives, not intentionally incompetent models.

## Migration principle

Use **move, upstream, or retire — not duplicate**:

1. identify durable player/research functionality in `commander-gym-private`;
2. determine whether it is actually a game/environment capability;
3. move player/learning/evaluation code to this repository;
4. for generic game/environment capability, implement it cleanly in the Argentum fork and prepare it for upstream;
5. consume the upstream capability from Commander Gym once available and remove any transitional fork-only dependency;
6. leave private deck/model/data assets in `commander-gym-private`;
7. retire obsolete Forge-era layers rather than preserving compatibility indefinitely.

The migration is successful when public Commander Gym is a focused player/research project that can plug into ordinary upstream Argentum.


## Pilot learning architecture

A pilot is the complete decision-making system occupying a seat, not one model adapter.

The intended long-term hierarchy is:

1. certified deterministic/mechanical handlers;
2. executable Python skills for solved recurring behavior;
3. learned specialist policies/value models;
4. a Magic-specialized local generalist model;
5. frontier/cloud escalation for novel, uncertain or exceptional decisions.

Commander Gym should progressively compile repeated reasoning into cheaper validated competence while preserving a generalist fallback. The local Magic model is the intended steady-state neural substrate; frontier models such as Luna are primarily teachers, evaluators and escalation resources.

Candidate automation/policies must be versioned and pass replay, held-out evaluation and shadow-mode qualification before production promotion. Live models do not directly rewrite trusted automation.

See #73 and `docs/pilot-learning-data-architecture.md`.

## Canonical artifact identities

Keep Deck, DeckKnowledge, Pilot and Binding separate.

- **Deck** is the playable artifact and exact revision.
- **DeckKnowledge** is the versioned primer/strategy/known-lines layer.
- **Pilot** is the whole player-side decision system.
- **Binding** is the immutable combination used at one seat.

Changing a pilot must not change deck identity. Changing a primer must not change the deck. Runs and decisions reference the exact Binding used.

The broader lineage is:

```text
Deck -> DeckKnowledge -> Pilot -> Binding -> Run -> Decision -> Annotation -> Dataset -> Model
```

Raw game evidence is immutable. Later evaluation is append-only annotation. Datasets are versioned selections/transforms over evidence rather than the authoritative history themselves.

See #5 and #53.

## Portable deployment and storage

The current Linode is an operational development host, not an architectural dependency.

Durable artifact identity must be independent of filesystem path or host. Commander Gym should obtain durable/ephemeral storage routes from configuration and support multi-terabyte local archives without requiring Git or cloud storage for bulk trajectories.

A portable node may colocate Argentum, Commander Gym, local inference and archival storage. Remote access/tunnel choices remain replaceable deployment infrastructure.

See #74 and `docs/pilot-learning-data-architecture.md`.


## Active implementation milestone

The active next goal is **#77 — pilot/data foundation before further game testing**.

#77 collects the identity, Binding-first seat loading, durable evidence/data lineage, progressive local-pilot learning, portable storage/deployment, private-instance cleanup, and generic Argentum per-seat controller-profile work into one qualification gate.

Ordinary gameplay/skill testing resumes with #72 only after #77 closes. Narrow implementation smoke tests are allowed where needed to prove the foundation itself.
