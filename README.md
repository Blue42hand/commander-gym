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

### Upstream-first dependency

The intended long-term dependency is:

```text
Commander Gym → vanilla upstream Argentum
```

`Blue42hand/argentum-engine` is a contribution and integration staging fork, not a permanent product dependency. When Commander Gym reveals a missing engine, card, Gym, multiplayer, transport, provenance, or controller capability, the default is to implement it as a generally useful Argentum improvement and upstream it.

A persistent fork-only delta is an exception that should be justified explicitly. The less Argentum code this project owns long term, the better.

This does **not** move player intelligence into Argentum. Upstreamability is subordinate to the architectural boundary: game/environment capability belongs in Argentum; player reasoning, learning, deck building, and policy belong in Commander Gym.

## Repository boundary

Commander Gym is intentionally split across three repositories:

- **`Blue42hand/argentum-engine`** — a temporary staging fork of the authoritative game/environment. Rules and state, multiplayer lifecycle, Commander rules, card implementations, legal actions and decisions, observations, hidden-information projection, game-server player/controller seams, Gym APIs, replay/snapshot/fork support, batching, and other generally useful engine/environment capabilities belong in Argentum. Changes in this fork should be designed for upstream contribution by default.
- **`Blue42hand/commander-gym`** — the artificial-player and learning framework. It owns pilots, deck builders, deck/pilot co-optimization, model/provider adapters, LLM/RL/other ML experiments, training loops, self-play, benchmarks, replay/data pipelines, experiment orchestration, evaluation, and the adapters that connect those systems to Argentum.
- **`Blue42hand/commander-gym-private`** — private project assets and migration staging. Private decklists, primers, deck-specific policies/specialists, model artifacts/checkpoints, private trajectories/datasets, and private experiment outputs live there.

## Placement rule

If the work changes **what Magic is, what information a player can legally observe, what actions are legal, what happens after an action, or how an external player connects to a game**, it probably belongs in Argentum and should normally be prepared for upstream.

If the work changes **how an artificial player thinks, learns, builds decks, chooses among legal actions, specializes, evaluates choices, or improves itself**, it belongs in Commander Gym even if the idea could be generalized.

Commander Gym may contain thin adapters for Argentum's Gym and game-server interfaces, but it should not recreate rules, legality, state transition, multiplayer, or transport authority.

## Argentum contribution rule

When work touches Argentum, follow upstream Argentum's contribution guidance rather than treating the fork as a private patch stack.

For cards in particular:

- use Scryfall Oracle text and rulings as the source of card wording/errata;
- compose existing `Effects.*` / `Patterns.*` primitives first;
- keep one scenario-test file per card;
- manually exercise the card and player-facing flow;
- batch cards only when they reuse existing primitives;
- isolate cards that require new engine/SDK vocabulary into focused changes with primitive-level tests.

Commander Gym's card-coverage work should therefore produce upstream-quality Argentum contributions, prioritized first by the active deck roster and then by Commander usage/popularity and mechanic leverage.

## Development priorities

1. **Competence first.** Build a reliable generalist artificial Magic player before splitting into casual-vs-competitive behavior profiles.
2. **Train through Argentum Gym.** Use the Gym path for self-play, large-scale evaluation, search, RL, dataset generation, and other machine-oriented work.
3. **Play humans through Argentum game-server.** A production pilot should ultimately occupy a normal player seat and interact through the same authoritative server boundary as human players.
4. **Connect deck building and piloting.** Deck construction and specialized piloting are related competencies and should eventually be optimized together on top of a strong generalist substrate.
5. **Minimize fork ownership.** Prefer upstream Argentum improvements over permanent fork-specific infrastructure.
6. **Measure what matters.** Automated strength and reliability benchmarks are development tools; human play quality is the eventual product metric.

## Current status

The durable public player/learning layer is being extracted from `Blue42hand/commander-gym-private` while the Argentum migration continues.

The immediate rules for migration are:

- **do not copy platform infrastructure here merely because it existed in the old Commander Gym codebase;**
- **do not make the Argentum fork a hidden fourth product layer;**
- prefer vanilla/upstream Argentum capabilities and upstream missing generic capabilities;
- keep Commander Gym focused on the artificial player.


## Development host

The current persistent development/test architecture uses a dedicated Linux host with:

- Argentum Gym bound to loopback and supervised by systemd;
- the Commander Gym authenticated gateway bound to loopback;
- Tailscale for administrator SSH access;
- an HTTPS tunnel only to the narrow gateway, never directly to raw Argentum;
- pinned clean runtime checkouts, with development performed in separate worktrees.

The direct `health → create → observe → step/decision → observe → dispose` path was proven live without the GitHub relay on 2026-09-20 and remained healthy after host reboot through `https://gym.commander-gym.com`. The obsolete GitHub Actions/control-branch relay has been removed from the normal repository path so direct authenticated orchestration is the single first-class control plane.

See `docs/linode-development-host.md` for the reproducible host layout and service installation.

## Four-seat pilot qualification

`commander_gym.pilot_session.run_four_seat_pilot_session` is the bounded integration
surface for the current Argentum migration proof. It binds four independent
`ArtificialPlayer` instances to the exact four-player roster returned by Argentum,
routes only the current `agentToAct`, and converts every successful native action or
structured decision into the existing durable record schemas.

The runner deliberately provides no heuristic fallback, automatic pass, concession,
or mutation retry. A pilot/provider failure propagates and the environment is disposed.
A successful bounded return is marked `stopped`; only an authoritative terminal
Argentum observation is marked `completed`. `write_pilot_session_artifact` atomically
writes the validated run and its decision records as one JSON artifact.

Real qualification artifacts can contain private deck identities and seat-visible game
state. Persist them in `commander-gym-private` or another private experiment store, not
in this public repository.

The terminal, serial runner is `python -m commander_gym.full_game`. It requests a fresh
acting-seat projection for every decision, classifies failed runs out of the training
pool, preserves partial trajectories, and verifies disposal plus post-game health.
Configuration and current engine/card blockers are documented in
`docs/full-game-runner.md`.

## Game-server seat policy boundary

`commander_gym.game_server_seat.GameServerSeatAdapter` is the Commander Gym side of
the human-play adapter. It feeds the existing `ArtificialPlayer` only the masked
`AiPlayerController` callback values, returns an exact native legal action or native
structured decision, and uses the same pilot for mulligan/bottom-card callbacks. It
has no trusted-runtime-snapshot input and propagates invalid, stale, and provider
failures without selecting a fallback strategy.

The Kotlin edge remains deliberately mechanical: serialize the native masked callback,
call this replaceable boundary, and deserialize the selected native response. It must
not pass `AiControllerContext.snapshot` to policy. The bounded adapter currently rejects
non-empty `ActionParams` instead of duplicating Argentum's native template completion;
the native edge should own that translation when it is added.
