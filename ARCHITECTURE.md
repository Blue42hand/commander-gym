# Architecture

## Purpose

Commander Gym is the research and evaluation layer above Argentum Engine. It should not become a second Magic engine, multiplayer server, or general agent platform.

## Ownership

### Argentum

Prefer Argentum for capabilities that are generally useful to Magic games or agents:

- rules and authoritative state transitions;
- format rules, including Commander;
- multiplayer lifecycle;
- legal-action enumeration and structured decisions;
- observation/state projection;
- hidden-information boundaries;
- state/schema identity;
- replay, snapshot, restore and fork;
- batch/vector execution;
- generic human/AI controller seams;
- generic agent telemetry;
- search, MCTS and self-play substrate;
- generic card-semantics/tooling support.

When Commander Gym needs one of these capabilities and Argentum lacks it, improve the Commander Gym Argentum fork first and design the change for upstream contribution.

### Public Commander Gym

Own research policy and durable experiment semantics:

- direct/API/local/learned pilot configuration;
- deterministic research policies;
- fail-closed experiment behavior;
- run manifests and provenance;
- benchmark definitions;
- evaluation and adjudication;
- model/pilot comparisons;
- training/dataset export;
- generalist and specialist research;
- deck/pilot co-optimization;
- metapools and experiment definitions;
- orchestration, analysis and reporting;
- public package/schema contracts;
- research-workflow integrations.

### Private Commander Gym

Keep private or deck-specific artifacts out of the public repo:

- private decklists and exact revisions;
- primers and deck-specific policy;
- specialist adapters/models/checkpoints;
- private trajectories and training datasets;
- private experiment outputs.

## Commander focus without needless lock-in

Commander drives priorities, benchmarks, and initial production workflows.

However, core concepts such as `Pilot`, `Experiment`, `GamePackage`, `DecisionRecord`, `Evaluation`, and `RunManifest` should not require Commander-only fields when a format-neutral representation is equally simple.

Format-specific policy belongs at the package/configuration edge rather than in generic research infrastructure.

## Migration principle

Use **move, not duplicate**:

1. identify durable research functionality in `commander-gym-private`;
2. determine whether it is actually a generic Argentum capability;
3. move generic capability to Argentum;
4. move only research/evaluation code to this repository;
5. leave private deck/model/data assets in `commander-gym-private`;
6. retire obsolete Forge-era layers rather than preserving compatibility indefinitely.
