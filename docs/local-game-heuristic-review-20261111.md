# Latest local game: heuristic review

Reviewed game `e21d6595-71be-4649-be54-1d4f1592e0ae`, attempt
`attempt-0a0353ee04cf4a06a9402336d84499ea`, through its final recorded callback.
The game failed on turn 24 after 3h 26m 20.8s; it did not reach a native terminal
result. Original capture segments remain unchanged. Private evidence and hashes
are under `/home/commander/heuristic-first-evidence-20261111`.

## Wall time

| Measurement | Entire game | Remainder after journal sequence 3403 |
|---|---:|---:|
| Completed callbacks | 311 | 112 |
| Model requests | 141 | 41 |
| Aggregate model request time | 197.44 min | 75.10 min |
| Median request | 81.71 s | 106.18 s |
| 95th percentile request | 124.24 s | 132.14 s |
| Input tokens | 3,305,205 | 1,244,763 |
| Output tokens | 10,303 | 2,790 |
| Request time before first content | 96.12% | 97.43% |

All 141 model responses were valid on their first attempt. Output repair is not
the dominant opportunity. Avoiding routine inference and reducing model-facing
input are the strongest next steps. Remainder prompts contained 24,483–36,721
input tokens.

The last callback, sequence 5176, has no completion. Reproducing the installed
baseline pilot against that seat's masked observation and its own deck knowledge
raises `LMStudioPilotError: local_prompt_byte_limit_exceeded` before transport.
The serialized request exceeds the 122,880-byte fence. This is a local prompt
construction failure, not an observed GPU timeout or a completed game outcome.
The candidate preserves this fence: supported heuristic choices bypass prompt
construction; oversized unsupported positions still report an explicit error.

## Gameplay findings in the remainder

* Lathril passed with Swamp/Path of Ancestry available at sequences 3739/3821
  (turn 19) and 5053/5135 (turn 23). Talrand similarly left Island available at
  4193/4275 (turn 21). These are repeated decisions within three turns, not six
  independent missed turns. The candidate chooses a land at each position.
* Mesa/Verduran Enchantress free-draw decisions at 4367, 4417, 4517, 4567,
  4691 and 4741 consumed about 10.25 minutes. Each answered yes. The new native
  fact exporter recognizes the exact free optional draw ability, and the policy
  answers these six without inference. It refuses paid draws, short libraries,
  and unknown or draw-sensitive visible boards.
* Alela's commander return after Stasis Snare (3473) remains strategic. Arcane
  Lighthouse's nonmana activation (3863, 139.4 seconds) also defers. Neither is
  classified by generic yes/no or mana shortcuts.
* Sythis did show useful strategic play: building Verduran Enchantress with
  Unflinching Courage, All That Glitters and Angelic Destiny produced an
  11-damage, 11-life-gain attack on turn 22. Broadly replacing strategic choices
  with shallow rules is not justified by this recording.

Earlier regression positions are also covered: cast Elvish Archdruid at 1075,
play Swamp at 1907, and select **Elf**, native option 97, for Vanquisher's Banner
at 2789. The original choice was option 10, Archon. Choice labels and fresh native
IDs matter more than guessing stable numeric indices.

## Candidate coverage and limits

The pinned policy handles **76 of 141 previously model-driven positions** (53.9%).
Those positions account for **105.02 minutes** of historical aggregate model time
(53.2%). In the remainder it handles **27 of 41**, associated with **49.01 minutes**.
Across all 311 completed callbacks it handles 246, including 170 callbacks that
already avoided model inference. Offline policy evaluation took about 0.026s.

These are counterfactual position-coverage measurements against recorded masked
observations, checked against native seat hashes. They are not an end-to-end
replay, a measured full-game speedup, or a win-rate result: earlier changed
choices alter future states. See `full-game-review-final.json` in the private
evidence directory for per-position outcomes.

## Next implementation and qualification priorities

1. Qualify the prepared opt-in four-deck hybrid in new isolated Commander games,
   comparing duration, model calls, land drops, wasted mana actions, terminal
   results and visible gameplay mistakes against the baseline. Retain failures.
2. Bound model-facing history while preserving full recording evidence and all
   current legal offers. Add tests for oversized unsupported states before
   changing prompt construction; increasing the byte fence alone is insufficient.
3. Expand native structural facts for common mana sources and interaction only
   after card-specific tests. Preserve model escalation for close choices,
   multiplayer combat, utility abilities and unknown interactions.

Implementation, regression scope, artifacts and rollback are documented in
[the handoff](heuristic-first-handoff.md).
