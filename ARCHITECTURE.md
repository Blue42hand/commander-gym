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

Development on the Commander Gym Argentum fork should primarily make Argentum a better environment for training and hosting artificial players. Changes should remain generally useful and suitable for upstream contribution whenever practical.

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

Keep private or deck-specific artifacts out of the public repo:

- private decklists and exact revisions;
- primers and deck-specific policy;
- specialist adapters/models/checkpoints;
- private trajectories and training datasets;
- private experiment outputs.

## Decision rule

Ask two questions:

1. **Is this about the game/environment?**  
   If it changes legality, rules, authoritative state, player-visible information, multiplayer lifecycle, or the generic interface through which an external player acts, it belongs in Argentum.

2. **Is this about the player?**  
   If it changes reasoning, learning, deck construction, planning, action selection, specialization, evaluation, or improvement, it belongs in Commander Gym.

The boundary adapter belongs in Commander Gym, but should be thin. Commander Gym must not maintain a competing canonical rules/state model merely to drive Argentum.

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

Use **move, not duplicate**:

1. identify durable player/research functionality in `commander-gym-private`;
2. determine whether it is actually a game/environment capability;
3. move generic game/environment capability to Argentum;
4. move player/learning/evaluation code to this repository;
5. leave private deck/model/data assets in `commander-gym-private`;
6. retire obsolete Forge-era layers rather than preserving compatibility indefinitely.
