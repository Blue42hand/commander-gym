# Luna-backed game-server sidecar

The normal human-play policy process is:

```text
Argentum game-server
    |
vanilla AiControllerProvider / AiPlayerController
    |
Commander Gym JVM adapter
    |
loopback HTTP + bearer token
    |
GameServerSeatAdapter
    |
RoutingPilot
    |
OpenAIResponsesPilot (default: gpt-5.6-luna)
```

This is deliberately separate from the scripted sidecar used by the JVM acceptance
test. Argentum still owns the game, hidden-information projection, legal actions,
structured decisions, and multiplayer lifecycle. Commander Gym owns only player
policy.

## Install

The OpenAI SDK is an optional dependency:

```bash
python3 -m pip install -r requirements-openai.txt
```

## Configure

Secrets are runtime-only and must not be committed:

```bash
export OPENAI_API_KEY='<openai-api-key>'
export COMMANDER_GYM_SIDECAR_TOKEN="$(openssl rand -hex 32)"
```

Defaults:

- `COMMANDER_GYM_OPENAI_MODEL=gpt-5.6-luna`
- `COMMANDER_GYM_SIDECAR_HOST=127.0.0.1`
- `COMMANDER_GYM_SIDECAR_PORT=8083`
- `COMMANDER_GYM_OPENAI_TIMEOUT=60`
- `COMMANDER_GYM_OPENAI_MAX_ATTEMPTS=2`

The bind host remains loopback-only. Attempts to bind the policy process to
`0.0.0.0` or another non-loopback address fail closed.

To retain masked policy provenance, set a private JSONL destination whose parent
directory already exists:

```bash
export COMMANDER_GYM_SIDECAR_PROVENANCE=/var/lib/commander-gym/game-server-policy.jsonl
```

These records include seat-visible game state, including private hand information for
the controlled seat. Keep them in private experiment/runtime storage, not the public
repository.

## Run

```bash
python3 -m commander_gym.game_server_openai_sidecar
```

The launcher creates one stable `GameServerSeatAdapter` per Argentum-generated AI
player id. Each seat gets its own `RoutingPilot -> OpenAIResponsesPilot` policy while
sharing the provider client. Certified forced/mechanical choices can bypass the model;
strategic choices go to the configured model. Provider, validation, stale-choice, and
transport failures remain failures rather than silently switching to another strategy.

## Argentum side

Load the Commander Gym JVM adapter into the normal Argentum game-server process and
configure the already-proven vanilla provider seam:

```text
game.ai.enabled=true
game.ai.mode=commander-gym
commander-gym.sidecar.url=http://127.0.0.1:8083
commander-gym.sidecar.token=<same value as COMMANDER_GYM_SIDECAR_TOKEN>
```

The sidecar bearer token is separate from `OPENAI_API_KEY`. Argentum never receives
the OpenAI credential.

## Live-game compatibility

The game-server policy bridge now covers the two compatibility seams needed by a real
Luna seat:

- mulligan keep/take choices carry stable callback-local semantic identities, so the
  same `OpenAIResponsesPilot` can choose them without inventing live routing ids;
- native `ActionParams` for attackers, blockers, targets, and X values cross the
  loopback boundary unchanged and are applied only at the JVM/native edge through
  Argentum's authoritative `ActionParameterizer`.

Parameterized actions may require Argentum's trusted runtime snapshot to resolve a bare
target entity id into its native target variant. That snapshot is consumed only inside
the native parameterizer. It is never serialized to the sidecar, included in policy
provenance, or exposed to Luna.

The next gate is an end-to-end normal multiplayer acceptance game with one human and
three Luna-controlled seats, followed by packaging the same stack for the persistent
server.


## Automated qualification

Manual browser play is no longer the primary way to discover basic policy-contract bugs.
The current audit and qualification gate are documented in
[`luna-game-server-contract-audit.md`](luna-game-server-contract-audit.md).

For a bounded two-seat Luna debug run:

```bash
export OPENAI_API_KEY='...'
bash scripts/run_two_luna_debug_game.sh
```

The script launches the normal game server/provider stack on free local ports, creates one
fixed-deck AI-only game through Argentum's dev endpoint, and retains the server log plus
private policy provenance. Its final `TWO_LUNA_DEBUG_RESULT` reports provider calls,
validation retries, token/cache usage, certified mechanical wakes avoided, avoidable
strategic wakes, communication failures, and conservative gameplay-review flags.

The runner is only orchestration and reporting. Argentum remains responsible for game
lifecycle, legal actions, decisions, state projection, action execution, and the terminal
result.
