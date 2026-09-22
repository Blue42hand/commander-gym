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

## Current integration boundary

This change only replaces the scripted acceptance policy with a real Luna-backed
policy process. It does not broaden the bounded game-server adapter.

Two compatibility gates remain before a real human-vs-Luna game can run through all
normal callbacks:

1. the game-server mulligan shim still needs stable semantic identities suitable for
   `OpenAIResponsesPilot`;
2. non-empty native `ActionParams` (combat, targets, X values, and similar choices)
   still fail closed rather than being applied at the Kotlin/native edge.

Those are intentionally separate from the provider launcher so model/runtime wiring
does not become coupled to rules/action-template completion.
