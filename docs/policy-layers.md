# Default policies, deck overlays and bounded inference

The local Binding runtime uses three layers for every deck:

1. **Shared defaults.** `DefaultPolicySubsystem` owns opening-hand castability,
   land sequencing, resource preservation, bottoming, cleanup discard, Scry 1,
   and the existing conservative development/combat rules. `SharedCardPolicies`
   owns reviewed card mechanics, including optional tokens, native draw payment,
   shock-land payment, reveal-to-hand effects and ordinary searches. A shared
   card rule applies to every deck containing that card.
2. **Optional deck overlay.** A trusted registration matches an exact deck and
   primer fingerprint. The overlay inherits shared rules and may modify scores,
   supersede a rule, or extend coverage. Lathril supplies the Elf game plan,
   opening/development/tutor values, tribe choice, commander connections, drain
   activation and combat decisions. There are no model calls inside heuristics.
3. **Bounded local inference.** Every expected coverage gap or uncertain choice
   defers to this layer. A deck policy cannot accidentally reopen the old full
   history/generalist prompt. Missing custom policies use the defaults plus this
   same fallback.

The certified native forced-pass optimization still precedes these strategy
layers. Native offers, response types, parameter specs and masked observations
remain authoritative. Every choice binds to the current callback and uses the
existing native wire validator. Expected gaps use `SubsystemDecision(reason=...)`
or `PolicyNotApplicable`; malformed native contracts, implementation errors,
provider failures, cancellation and invalid output remain visible errors.

## Extending to another deck

For priorities and weights alone, add a reviewed JSON profile and construct
`ProfiledDeckPolicy(facts, profile)`. This merges unspecified fields with defaults:

```python
profile = {
    "policy_id": "example-control-v1", "version": "1",
    "roles": {"Example Draw Engine": "draw"},
    "weights": {"draw": 110},
    "opening_weights": {"draw": 85},
    "color_priorities": {"U": 9, "W": 7},
    "rules": {"plan": "Develop mana, establish card advantage, preserve interaction."},
}
```

For special actions, subclass `ProfiledDeckPolicy` or `DefaultPolicySubsystem`.
Supported hooks include `_opening`, `_opening_value`, `_development_score`,
`_land_score`, `_search_score`, `_combat` and `_deck_pending`. Returning `None`
from `_deck_pending` inherits the shared pending-decision rules; returning a
handled result supersedes them; returning an explicit defer routes to inference.
Use `_action`/`_decision` to construct a normal native choice. Keep all callback
IDs and candidate sets request-local.

Add a `DeckPolicyRegistration` to `registered_deck_policies()` with the exact
reviewed fingerprints, a factory and a component-reference function. The helper
`profile_component_ref(facts, profile, sources=...)` pins defaults, profile data
and custom hook files. Include every custom implementation file in `sources`.
Profiles and factories live in the trusted code release; a pulled catalog cannot
install Python code or arbitrary plugins. Duplicate matching registrations fail
validation. A changed or unmatched primer receives shared defaults, while an
explicitly pinned mismatched overlay is rejected.

## Bounded model contract

Small decisions are enumerated into at most 128 complete choices and validated
against the current native wire contract. The model returns only an option index;
the harness binds the corresponding action/targets/parameters. Deck rules may
provide a smaller approved shortlist, such as Lathril's tied removal targets.

For large or combinatorial choices, inference uses the current native constrained
JSON schema. Offered choices are preserved rather than silently truncating the
menu. Selection counts, option ranges, target slots and available IDs are bounded
where the native contract supplies them. Local checks additionally reject duplicate
selections, invalid cardinalities and invalid reorderings. Complex cross-selection
constraints remain in the context and are ultimately enforced by native execution.
The fallback does not simulate Magic or claim to prove all native legality locally.

The model sees a decision-focused view of masked live cards, visible zones,
players, current options, deck subtype counts and compact policy objectives.
Card definitions are sent once and options refer back to them; null, false and
empty metadata is omitted from the model view. The original native observation
and validators retain all values and constraints. It receives no accumulated game log, conversation, full primer, full deck card
catalog, images, or administrative fields. Requests are limited to the smaller
of the configured prompt ceiling and 32 KiB; output is capped at 768 tokens and
existing timeout, cancellation, retry and batch budgets apply. If a complete
request exceeds its budget, or a native schema cannot be represented, the callback
reports that explicit error. It does not remove constraints or invent an action.

Routing records the heuristic layer, defer reason, selected provider, fallback
mode and complete model-I/O receipt. Covered heuristic decisions make no provider
requests. An invalid model answer or provider failure never selects the first
option or silently passes.

## Catalog preparation and scope

`python3 -m commander_gym.local_catalog` now installs defaults on every prepared
local deck and automatically selects matching registered overlays. `--card-facts`
adds a pinned native public-facts export; without it, unknown card facts defer to
inference and basic rules remain available. The old `--lathril-policy` argument is
a compatibility alias and is no longer required. Provider component version 2
selects this routing contract; new skill digests cover shared, deck and fallback
implementation sources. Build a fresh immutable catalog for changed code/data.

Legacy local manifests without a skill also receive defaults before the bounded
fallback. Public facts require an explicitly pinned skill. This document describes
the local Binding runtime; preparing a catalog does not activate production.

## Verification

`tests/test_policy_layers.py` covers default behavior outside Lathril, data-only
weight changes, rule extension/override, exact registration, unknown-action and
unknown-decision fallback, prompt isolation, frozen choices, native-schema fallback,
selection validation, mixed payments, budgets, cancellation and provider failures.
The recorded Bojuka Bog callback is included: its four offered player targets now
reach bounded inference instead of terminating at a heuristic coverage gap.
Recorded-state tests with model responses supplied by a test transport establish
routing/contract coverage; they do not establish live inference quality or win rate.

## Talrand, Sythis and Alela overlays

The trusted registry also pins `talrand_v1.json`, `sythis_v1.json` and
`alela_v1.json` to the exact reviewed deck and primer identities. Their common
implementation is `engine_deck_policy.py`; future engine decks can reuse its
hooks and add a reviewed profile without changing provider routing.

* Talrand prefers mana development, then the commander before expendable cantrips.
  It reserves the total mana and colored pips for the cheapest known counterspell;
  casting the commander also reserves at least one mana. Extra turns and mass
  bounce remain closing decisions for inference.
* Sythis prefers enchantment mana, the commander and enchantresses before payoffs.
  Land Auras spread across own legal lands; redundant draw engines stay in hand
  once two are established. Creature Auras prefer a ready attacker with fewer
  existing attachments. Prison pieces and unsupported interactions defer.
* Alela establishes a usable draw engine before commander commitment, develops
  Faerie payoffs as the board grows, names Faerie for reviewed tribal payoffs and
  Enchantment for Cloud Key. Discovery near deck-out, repeatable Aura loops,
  Tempest Technique and alternate wins require inference.

Opening hands use the shared castability check plus the profile's plan. Unknown
cards that might supply the missing plan defer instead of causing an automatic
mulligan. Resource estimates deliberately undercount uncertain/variable mana;
only native affordability and native automatic payment authorize a cast. An
unrepresented utility activation is never hidden by a resource-reservation pass.

For unconstrained combat with a reviewed open attack lane, the overlay can offer
at most three complete plans: hold, apply pressure, or retain a flying blocker.
The model selects a plan index. Mandatory assignments, taxes, teams, problematic
draw budgets and unsupported combat retain native-schema inference. These are
simplified plans, not a multiplayer combat solver or a lethal guarantee.

## Compact fallback representation and land facts

Fallback views factor identical card characteristics into definitions and use
column tables for card instances and actions. Repeated structured values have
explicit references; common action values are defaults. Expansion preserves the
entire decision view, including every affordable offer and its constraints.
Native action-schema branches share a semantic-ID enum only when their complete
parameter grammars are identical. Original observations and final native
validation remain unchanged. The 32 KiB ceiling remains in force.

The native public-facts exporter reads each independent ordinary mana mode rather
than requiring every ability on a land to be ordinary mana. Myriad Landscape and
Arcane Lighthouse therefore expose colorless mana without granting automatic
permission to use their utility abilities. Underground River exposes its ordinary
colorless mode; its damaging colored modes remain outside simple-mana policy.
Optional backwards-compatible fields describe exact land-subtype entry tests
(`untappedIfLandSubtypes`) and variable mana (`variableMana`). Sunpetal Grove's
entry timing uses the current visible battlefield's land subtypes. Variable mana
such as Serra's Sanctum is excluded from guaranteed resource counts and opening
keeps defer for assessment.

`tests/test_engine_deck_policy.py` checks profile matching, sequencing, resource
reservation, Aura distribution, draw safety, combat shortlists, lossless context
expansion, equivalent grouped action grammars and a large recorded Alela request.
The native exporter tests distinguish utility mana, damaging mana, check-land
conditions and variable production. Recorded-state qualification remains
counterfactual; a fresh immutable catalog and matching exporter output are needed
for these changes. Preparing that catalog does not activate production.
