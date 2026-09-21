# Argentum-native qualification roster v1

This is the first real four-deck Commander Gym roster whose exact card lists are
restricted to card definitions present in `Blue42hand/argentum-engine`.

The roster is intended to unblock end-to-end Gym work. It is not a claim that every
card interaction has exhaustive scenario coverage. `manifest.json` pins the Argentum
revision used for source-level card-presence validation, while each deck's primer
describes its pilot priorities and important interaction surfaces.

Run the local validation with:

```bash
python scripts/validate_argentum_roster.py \
  --argentum ../argentum-engine \
  rosters/argentum-native-v1/manifest.json
```

The validator fails closed on missing card definitions, duplicate nonbasic cards,
card-count errors, commander-count errors, or manifest digest drift.

Build a directly runnable four-seat `full_game` manifest (including the exact lists and
primer text as pilot strategy) with:

```bash
python scripts/build_argentum_roster_game.py \
  rosters/argentum-native-v1/manifest.json \
  --output /path/to/private/run-manifest.json
```

The generated run manifest is intentionally not committed because complete run inputs
and trajectories may belong in the private experiment store.
