# Seat observation cache layout v2

`argentum-seat-cache-v2` extends the existing opt-in cache-friendly observation
layout. The first explicit cache boundary now contains the current observation's
`deckKnowledge`, `knownDeck`, `perspectivePlayerId`, and `gameId` when present.
The Binding resolver supplies immutable, seat-authorized `deckKnowledge`; the
encoder never loads a catalog or remembers an earlier seat's contents. Missing,
null, and empty values remain distinct. Changing the seat, supplied game identity,
deck list, or knowledge changes this first cache boundary.

Full masked history still follows at fixed 32/128/512-event boundaries, before
the live state, legal menu and pending decision. A shorter/replaced history is
encoded from scratch. No prior provider response, conversation state, or hidden
engine information is used. The offline decoder restores the exact input.
Routing handles are removed by the same existing sanitizer and reattached to
the selected live action/decision locally. Retries retain the original view and
append the existing correction message.

The existing `argentum-seat-sparse-cards-v1` encoding is retained. It only omits
explicit common empty card fields and verifies exact reconstruction. Irregular
card shapes retain their full contents. No action, mana ability, legal target,
pending decision, semantic ID, state field, or strategic primer is summarized.
Cache mode also avoids serializing an unused monolithic input before constructing
the multipart input.

## Offline evidence

Run `python3 scripts/measure_observation_layout.py`. The frozen project-owned
held-out fixtures cover a priority menu with a mana ability, a structured target
decision, and combat with heterogeneous card shapes. They contain no production
game data or policy labels. Results are UTF-8 JSON payload bytes:

| Fixture | Native sanitized | Existing sparse v1 | Stable prefix before / v2 | Live payload before / v2 |
| --- | ---: | ---: | ---: | ---: |
| Priority | 4662 | 4482 | 179 / 669 | 1695 / 1205 |
| Targets | 4707 | 4527 | 179 / 669 | 1740 / 1250 |
| Combat | 4738 | 4813 | 179 / 669 | 2026 / 1536 |

Each fixture moves 490 bytes from the live payload into the stable payload.
This is relocation, not a reduction in total information. Sparse v1 already
saved 180 bytes in the first two fixtures and adds its 75-byte wrapper when
heterogeneous cards cannot be compacted. Cache mode eliminates one redundant
4482–4813-byte JSON serialization in these fixtures; CPU wall-time savings have
not been measured. Tests cover exact JSON/menu reconstruction, all history
boundaries, current-seat-only content, changed/missing knowledge, native routing,
unchanged request schemas/settings, and correction retries.

These synthetic counts do not measure provider tokenization, cache hits, cache
write charges, strategic quality, or provider latency. Request-local output
schemas can also limit actual prefix reuse. No provider experiment or paid game
was run. No provider-time savings are claimed; the historical game's opportunity
ceiling is not a measured saving from this change.

## Activation handoff

On a reviewed source revision containing this change, the binding sidecar option
`COMMANDER_GYM_CACHE_FRIENDLY_HISTORY=true` selects this layout when supplied
in the sidecar's actual process environment.
The default remains `false`. Existing built-in compact Pilot components,
including explicit-wait v8, already accept this transport option through
`OpenAIBindingPilotComponentResolver`; existing immutable Pilot/Binding artifacts
need no mutation. Pin the Gym revision and environment value in the release
receipt. `manual_runtime_qualification` forwards the option from its resulting
configuration into its no-provider resolver qualification.

The current manual-runtime launcher builds an explicit environment allowlist in
`manual_runtime_launch.sidecar_environment` and does **not** include this option.
Neither a shell export nor an ambient systemd environment variable activates it.
The release owner must include the following entry in that function's returned
environment in a separately reviewed release source revision, then rebuild and
reseal the launcher/source manifests through the existing release process:

```python
"COMMANDER_GYM_CACHE_FRIENDLY_HISTORY": "true",
```

Qualification must assert this entry in the generated sidecar environment and
resolve the same immutable Pilot/Binding with cache mode enabled using a rejecting
or fake provider. The existing launcher allowlist and sealed-profile schema are
not changed by this PR. Until the release source change is incorporated and
qualified, the manual runtime continues to use the original non-cache layout.

Historical human-GUI/experimental-prefix/sequential-v7 launchers deliberately
force cache mode off; this change does not relax their qualification contracts.
Activation belongs to the separate runtime release owner. No environment,
deployment, model identity, reasoning setting, output cap, budget, primer,
named-wait, guarded-cast, or continuation policy changes are included here.
Reasoning/output reductions were not selected: there is no offline evidence that
a smaller budget preserves strategic quality.
