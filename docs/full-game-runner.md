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
    "format": {
      "type": "com.wingedsheep.sdk.core.Format.Commander",
      "commanderDamageThreshold": 21,
      "deckSize": 100,
      "startingLife": 40,
      "startingHandSize": 7,
      "alwaysDivertToCommand": false
    },
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

For transport and lifecycle qualification without private deck disclosure, the public
`fixtures/full_game_krenko_mountains.json` pod uses four legal, fully supported
Krenko-plus-99-Mountains Commander decks. It is intentionally synthetic evidence, not
a benchmark deck or a substitute for current-deck coverage qualification. It explicitly
selects the narrow `qualification_aggro` pilot, which plays lands, casts and activates
the commander, attacks all legal attackers, and declines optional blocks. That backend
fails closed for any decision outside this public fixture's small action surface.

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

## First terminal-game milestone and remaining blocker (2026-09-20)

The persistent Linode now runs Argentum
`320ab35a6ce4a32f53b93f7aa11bce1469467a2f`, schema
`argentum-gym-contract@v1.8-multi-seat-seed`. Run
`linode-first-terminal-four-seat-local-v3` completed the public synthetic pod with
`valid_complete`: all four seats acted, Argentum declared seat `e3` the winner after
1,298 decisions, the environment was disposed, and the service remained healthy.
The checked-in evidence summary is `docs/first-terminal-game.json`; the complete
private artifact has SHA-256
`60d25ad4ff9f5907baadd9781db56b724546117bafb162c7d2b678795e84860f`.

This closes the transport, multi-seat projection, deterministic seed, routing,
trajectory, cleanup, and terminal-result lifecycle milestone. It does not qualify a
current user deck. The latest authoritative coverage report, generated against the
same Argentum revision, still has no complete four-deck pod. The highest-coverage
Alela/Krenko/Meren/Rocco candidates and their exact missing cards are recorded in
`docs/first-pod-blockers.json`. Card implementation belongs in Argentum; the runner
must not silently replace unsupported cards or rules.
