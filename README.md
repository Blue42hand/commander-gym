# Commander Gym

Commander Gym is a project for building **artificial Magic players that humans actually want to play with**.

Commander is the primary format and research target. Core interfaces should remain usable by other Magic formats when doing so costs little or nothing; this is not a mandate for speculative abstraction.

The project's job is to reproduce and improve the competencies a human brings to Magic as a player: deck construction, strategic planning, tactical piloting, adaptation, multiplayer judgment, and the connection between how a deck is built and how it is played.

## North star

The end product is not a benchmark score or a self-play system. It is an artificial player that can sit in a real game with humans and provide a worthwhile opponent or tablemate, whether the game is casual or competitive.

For now, **general Magic competence is the unified development objective**. Better sequencing, planning, threat assessment, resource management, deck understanding, and multiplayer judgment improve both competitive strength and casual play. Separate casual/competitive policy branches should wait until pilots become strong enough that those objectives meaningfully diverge.

## Argentum relationship

A useful shorthand is:

> **Argentum is the game. Commander Gym is the player.**

Commander Gym uses two Argentum-facing paths:

```text
Training / research
Commander Gym
    ↓
Argentum Gym / gym-server
    ↓
rules engine

Human play
Commander Gym pilot
    ↓
Argentum game-server player/controller interface
    ↓
human players + web client
```

The training path may use batching, reset, snapshot/fork, privileged debug tooling, and other machine-oriented affordances. The human-play path should obey the same information and interaction constraints as a human seat.

## Repository boundary

Commander Gym is intentionally split across three repositories:

- **`Blue42hand/argentum-engine`** — the authoritative game/environment. Rules and state, multiplayer lifecycle, Commander rules, legal actions and decisions, observations, hidden-information projection, game-server player/controller seams, Gym APIs, replay/snapshot/fork support, batching, and other generally useful engine/environment capabilities belong here. Development on this fork should primarily improve Argentum in ways that make it a better environment for Commander Gym, while remaining generally useful and upstreamable where practical.
- **`Blue42hand/commander-gym`** — the artificial-player and learning framework. It owns pilots, deck builders, deck/pilot co-optimization, model/provider adapters, LLM/RL/other ML experiments, training loops, self-play, benchmarks, replay/data pipelines, experiment orchestration, evaluation, and the adapters that connect those systems to Argentum.
- **`Blue42hand/commander-gym-private`** — private project assets and migration staging. Private decklists, primers, deck-specific policies/specialists, model artifacts/checkpoints, private trajectories/datasets, and private experiment outputs live there.

## Placement rule

If the work changes **what Magic is, what information a player can legally observe, what actions are legal, what happens after an action, or how an external player connects to a game**, it probably belongs in Argentum.

If the work changes **how an artificial player thinks, learns, builds decks, chooses among legal actions, specializes, evaluates choices, or improves itself**, it belongs in Commander Gym.

Commander Gym may contain thin adapters for Argentum's Gym and game-server interfaces, but it should not recreate rules, legality, state transition, multiplayer, or transport authority.

## Development priorities

1. **Competence first.** Build a reliable generalist artificial Magic player before splitting into casual-vs-competitive behavior profiles.
2. **Train through Argentum Gym.** Use the Gym path for self-play, large-scale evaluation, search, RL, dataset generation, and other machine-oriented work.
3. **Play humans through Argentum game-server.** A production pilot should ultimately occupy a normal player seat and interact through the same authoritative server boundary as human players.
4. **Connect deck building and piloting.** Deck construction and specialized piloting are related competencies and should eventually be optimized together on top of a strong generalist substrate.
5. **Measure what matters.** Automated strength and reliability benchmarks are development tools; human play quality is the eventual product metric.

## Current status

The durable public player/learning layer is being extracted from `Blue42hand/commander-gym-private` while the Argentum migration continues.

The immediate rule for migration is: **do not copy platform infrastructure here merely because it existed in the old Commander Gym codebase.** Prefer Argentum-native game/environment capabilities and keep Commander Gym focused on the artificial player.
