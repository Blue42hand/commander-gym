# Four-seat full-game runner

`python -m commander_gym.full_game` runs one or more serial four-seat Commander
games through the authenticated Argentum Gym path. Argentum owns all game state,
rules, legal actions, hidden-information projection, elimination, and terminal
results. Commander Gym only selects among the current native choices and records
the trajectory.

## Required Argentum capabilities

The runner deliberately fails closed unless the connected service provides:

- a four-player Commander `EnvConfig`, including an optional deterministic `seed`;
- `GET /envs/{id}?perspectivePlayerId={acting-player-id}`, returning a projection
  whose `perspectivePlayerId` and `agentToAct` both identify the acting seat;
- semantic identities on every legal action and pending structured decision;
- native structured-decision submission;
- authoritative `terminated` and nullable `winnerId` fields.

It never uses `revealAll=true`. An older fixed-perspective Gym service is therefore
reported as `technical_censored`, not worked around with leaked information.

## Manifest

Keep real decklists and private pilot settings outside this public repository. The
manifest shape is:

```json
{
  "argentum_config": {
    "format": "Commander",
    "skipMulligans": false,
    "useHandSmoother": false,
    "startingPlayerIndex": 0,
    "revealAll": false,
    "players": [
      {
        "name": "Seat 0",
        "deck": {"type": "Explicit", "cards": {"...": 1}},
        "startingLife": 40,
        "commanderCardName": "..."
      }
    ]
  },
  "seats": [
    {
      "player_name": "Seat 0",
      "deck_id": "deck-package-id",
      "deck_version": "deck-fingerprint",
      "primer_version": "optional-primer-version",
      "pilot": {"backend": "openai_responses", "model": "gpt-5.6-luna"}
    }
  ]
}
```

Supply exactly four `players` and four matching `seats`, in seating order. Build
the `Explicit.cards` maps from validated DeckPackage v2 artifacts; do not commit
private deck contents to this repository.

## Run against Linode

Load the local API credential and the runtime-only gateway values, then run:

```bash
python3 -m pip install -r requirements-openai.txt

set -a
source .env.local
set +a
export COMMANDER_GYM_ARGENTUM_URL='https://<gateway-hostname>'
export COMMANDER_GYM_ARGENTUM_TOKEN='<gateway bearer token>'

python3 -m commander_gym.full_game \
  --manifest /path/to/private/full-game.json \
  --output-dir /path/to/private/runs \
  --run-id linode-qualification \
  --game-count 1 \
  --seed 20260920 \
  --max-choices 100000 \
  --expected-schema-hash '<deployed schema hash>' \
  --expected-build-revision '<deployed Argentum commit>'
```

For a serial repeatability check, increase `--game-count`. Seeds increment once per
game and run IDs receive a four-digit suffix. Parallel rollout is intentionally not
implemented here.

Every attempted game produces one atomic JSON artifact, including partial successful
decisions and cleanup/postflight evidence when the run is censored. The process exits
nonzero if any run is not `valid_complete`.

## Qualification

- `valid_complete`: authoritative terminal winner/draw, clean disposal, stable healthy service;
- `technical_censored`: synchronization, transport, timeout/choice bound, loop, projection, or cleanup failure;
- `pilot_failure`: model/provider failure or malformed pilot response;
- `engine_failure`: explicit engine/orchestration rejection or compatibility failure;
- `unsupported_decision`: a native decision cannot be represented without invention.

Only `valid_complete` artifacts set `training_eligible=true`.

## Current first-game blockers (2026-09-20)

The persistent Linode runtime is still Argentum
`dbb3e0577c7dd9e297dfc3bf52b42079c18db557`, schema
`argentum-gym-contract@v1.7-semantic-state-provenance`. It has a fixed default
perspective and no per-observe acting-seat projection, so it cannot safely drive four
pilots. The current `EnvConfig` also does not expose its underlying game seed.

The latest authoritative coverage report at Argentum
`168b8d508ecc4d69f32b4abc9a7778b96df32d55` has no complete four-deck pod. The
highest-coverage Alela/Krenko/Meren/Kadena pod is still missing the exact cards listed
in `docs/first-pod-blockers.json` (copied from the authoritative private coverage
artifact). Card work belongs in Argentum; the runner must not replace those cards or
rules.
