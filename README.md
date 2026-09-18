# Commander Gym

Commander Gym is a **Commander-focused Magic agent research, piloting, evaluation, and deck-optimization harness** built on [Argentum Engine](https://github.com/Blue42hand/argentum-engine).

Commander is the primary product and research focus. Core interfaces should remain usable by other Magic formats when doing so costs little or nothing; this is not a mandate to add speculative abstraction.

## Repository boundary

Commander Gym is intentionally split across three repositories:

- **`Blue42hand/argentum-engine`** — authoritative Magic/agent platform. Rules and state, multiplayer lifecycle, Commander rules, legal actions, observations, hidden-information projection, replay/snapshot/fork support, batching, generic controller/provider seams, search/self-play primitives, and other generally useful engine/agent capabilities belong here. Changes should be suitable for upstream contribution to `wingedsheep/argentum-engine` whenever practical.
- **`Blue42hand/commander-gym`** — this public research harness. It owns pilots/provider configuration, experiment policy, fail-closed evidentiary semantics, benchmark/evaluation definitions, dataset/training export, generalist/specialist research, deck+pilot optimization, experiment orchestration, analysis, reporting, and public package contracts.
- **`Blue42hand/commander-gym-private`** — private project assets and migration staging. Private decklists, primers, deck-specific policies/specialists, model artifacts/checkpoints, private trajectories/datasets, and private experiment outputs live there.

## Placement rule

If a capability still makes sense after removing the words **Commander Gym** from its requirement, first try to implement it in Argentum.

If it defines how experiments are configured, evaluated, compared, trained, censored, optimized, versioned, or reported, it belongs in Commander Gym.

Commander-specific **rules and game mechanics** belong in Argentum. Commander-specific **experimental content** such as decks, primers, specialist policies, and benchmark packages belongs in versioned packages and may be private.

## Current status

This repository has just been created. The durable public research layer is being extracted from `Blue42hand/commander-gym-private` while the Argentum migration continues.

The immediate rule for migration is: **do not copy platform infrastructure here merely because it existed in the old Commander Gym codebase.** Prefer Argentum-native capabilities and keep this repository thin.
