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
catalog, images, or administrative fields. Requests aim for a 32 KiB soft target after compaction. Larger complete requests
proceed up to the configured `max_prompt_bytes` hard ceiling, which must fit the
model context with output and template reserves. Output is capped at 768 tokens and
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
validation remain unchanged. The 32 KiB target does not reject larger requests;
the configured hard ceiling and context checks remain in force. Receipts record
`promptTargetBytes`, `promptBytes`, `promptTargetExceeded`, and the actual hard
`maxPromptBytes` limit, so target overruns remain visible without ending games.

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

## Competency bounds and decision explanations

The shared `policy_action_bounds.py` applies reviewed restrictions to a copy of
current native offers before either policy selection or bounded inference. It
preserves the original recorder observation and native routing handles. Ordinary
harmful single-slot targets exclude the acting player's permanents; unknown
ownership and complex target contracts defer to native inference. Trusted deck
code can explicitly approve a rescue/combo effect with `self_target_exceptions`;
that approval is passed to bounded inference through policy metadata. An unknown
card or mechanic does not become a fabricated pass.

Costs and targets are independent: paying Wirewood Symbiote's bounce cost does
not satisfy its required untap target. Flat and single-group target bounds are
constrained in the grammar and checked again at runtime. Native execution still
owns complex legality. Quiet main-phase Symbiote activations with no tapped target
are suppressed; stack rescue windows remain available.

Known empty Green Sun's Zenith searches are excluded using exact deck definitions,
with a conservative creature mana-value lower bound. Known green payoffs supply
an intended card and X recommendation. Sterling Grove plans an enchantment
payoff before sacrifice and reconstructs that plan against fresh native search
options. Repeatable engines supersede cantrips, and deck engine roles supersede
general card scores. These plans contain names and goals, never queued entity IDs
or inferred library order. They are stateless across callbacks and seats.

An affordable native AutoPay cast/cycle removes manual mana setup from that
fallback menu. Explicit payment decisions and mana needed to enable an otherwise
unaffordable spell retain their native offers. Native land definitions distinguish
commander-color mana, painful colored modes, subtype-based entry conditions, and
life-paid untapped entry. Painful modes are excluded from conservative supply
estimates at two life or less. Land scoring considers colored pips and an
immediately payable development spell; an affordable Felidar Retreat precedes the
land drop. Empty native blocker domains and ordinary Felidar modes are local.

Set `"decision_explanations": true` in the LM Studio inference configuration and
prepare a fresh catalog. Existing configurations default to false. This changes
the provider identity, so it requires freshly prepared, matching manifests. The
bounded model emits a wrapper with `decision` and `explanation`. The explanation
contains `goal`, up to three `keyFacts`, up to six visible `evidenceIds`,
`uncertainty` (`low`, `medium`, `high`), and up to three `missingInformation` items.
Each text is limited to 180 characters, with 720 total text characters. Evidence
IDs must exist in the current masked context. Invalid summaries use the same
bounded retry path as invalid actions. The 32 KiB prompt target is advisory;
the configured hard prompt ceiling and 768 output-token cap remain in force.

Only the native decision is executed. The explanation is recorded separately in
`metadata.boundedFallback.decisionExplanation`, marked as a model report, and is
never appended to later prompts. It is a brief account of intended benefit and
information needs, not a request for a private reasoning trace. Grounded IDs do
not establish that every reported fact or explanation is faithful. Compare the
summary against the masked position, offered candidates, and actual outcome when
diagnosing missing inputs.

The November 12 regression corpus contains 891 authenticated callbacks from game
`d22fb035-9394-4fc3-9ef9-944ec5c49499`; eleven selected masked positions and pinned
public facts are included in `tests/fixtures/competence-*`. The source journal and
native observation digests accompany each selected position. Extraction verifies
the complete Gym hash chain and native observation provenance. Same-state
comparisons use the immediately preceding `d0f95de4` implementation and its facts.
Simulated answers test routing, prompt budgets and response contracts; they do not
measure live wall time, strategic strength, win rate, or native game completion.

Shared payment rules can complete a small ordinary casting payment from the
native `availableSources` domain, including explicitly offered sacrifice sources.
The policy minimizes sacrifices and source count; the native engine assigns the
colors and executes the actual mana abilities. Restricted pools, unsupported
production, additional source costs and large searches defer. A covered floating
pool uses a manual empty selection when native `canAutoPayNow` is false. Colored
coverage uses exact matching over the six mana types, avoiding permutations of
identical mana units.

`modal_policies.py` handles reviewed independent removal modes. To extend it,
register a card and its reviewed descriptions in `REVIEWED_REMOVAL_MODALS`, and
add recorded-state and native acceptance coverage. The native `modalEnumeration`
preview supplies candidates and counts. Extra costs, repetition, composite target
constraints and unknown effects defer. A plan is scoped to the acting seat,
source, turn and step; each subsequent target must still occur in the current
native pending domain. A missing or changed preview returns control to the model.
Casualties of War uses each useful independent mode, avoids targeting its owner's
permanents, and prioritizes freeing a disabled commander. This casting-local plan
is separate from the stateless search payoff recommendations described above.

Safe attack lanes are evaluated independently. First strike, double strike and
trample elsewhere on the battlefield do not force all combat into inference;
double-strike retaliation is counted conservatively. Unsupported evasion still
defers when no covered lane exists. The model remains responsible for uncovered
combat choices. Deck code can set `counter_target_exceptions` to override the
shared rule that preserves costly or Treasure-refunding counters against routine
mana rocks; that approval survives the second bounding pass in the model fallback.

Reaction scheduling uses native removal/neutralization tags and harmful or
flexible polarity, alongside reviewed card rules. This covers ordinary bounce
without adding every card name to a timing list. The live native end step is
`END`; `END_STEP` remains accepted for older inputs. Opponent end steps, actual
combat, live stacks, own main phases and native payment corrections retain their
relevant decisions. Quiet windows can pass locally.

Public native facts optionally include `removesAttachedAbilities`. It is exported
from an unconditional typed `LoseAllAbilities` static ability affecting the
attached permanent, excluding variants that preserve mana abilities. This makes
suppression detection work for cards such as Utter Insignificance, Eaten by
Piranhas and Witness Protection. Prompt annotations, engine valuation, removal
ranking and commander rescue share this fact; Arrest and Faith's Fetters remain
distinct. Older facts retain reviewed compatibility rules.

An unopposed own counter attempt now waits for resolution before another counter
can target the same original spell. Native counterspell tags cover conditional
counters such as Spell Pierce too; this rule does not assume that the tax is paid
or declined. A visible response threatening the first counter restores model
control immediately, and the original target becomes available again if it remains
after resolution. Unknown effects and ability copies still defer.

`selection_contracts.py` reads native `conditionalMinimums` without parsing card
text. Small selection menus enumerate only complete legal sets, and both bounded
and general provider validators reject an unmet conditional minimum before native
submission. Multiple alternatives follow the native rule that any applicable
lower minimum may satisfy the selection. Known discard spells compare the valid
sets locally, including Thirst's one-matching-card discount, while preserving the
next land drop. Low-priority cards retain positive opportunity value, preventing
an unnecessary extra discard merely because its development score is negative.
Larger or otherwise unsupported selections retain model control and validation.

Public facts also expose three optional, conservative primitive contracts:
`developmentMana`, `manaOnlySelfUntapAbilities`, and `fixedStatReductions`.
Development accepts ordinary fixed tap mana, including unrestricted any-color and
commander-identity choices, with no triggers or material entry costs. A free
creature-type choice and a positive own-tribe anthem are understood. This covers
Patchwork Banner without changing `simpleMana` or authorizing manual activation.
In an empty own main phase, the default `_commander_mana_setup` hook prioritizes an
affordable safe artifact that makes the absent commander's printed mana cost
reachable after untapping. It yields to land drops, an available commander cast,
native payment corrections and visible survival questions. It does not promise a
future cast or estimate commander tax. Deck code can override this hook.

Exact fixed negative-stat trigger effects can shortlist killable opposing threats
using their current native toughness and shared suppression-aware valuation. The
SDK description is matched with capitalization and terminal-period normalization;
unknown or mixed effects retain model control. Small target domains still use
native legality. Copies of ability-removing Auras avoid targets already covered
by an unopposed own Aura, preferring distinct live threats. A visible counter to
the original restores uncertainty. When one useful target remains the shared
handler selects it; several alternatives retain bounded inference.

A pure mana-cost self-untap ability is held while its source is already untapped
in a quiet window. Tapped sources and stack interaction retain their decisions.
Trusted `self_target_exceptions` can approve activation-dependent deck plans.
Costly counters are also conserved against a reviewed set of ordinary filtering
spells; tutors, engines, unknown draw spells and deck counter exceptions remain
available.

The model view preserves `linkedExile` references only to cards already visible
to the acting seat. Linked public cards retain their rules instead of being
summarized as inactive history, so imprint decisions have their actual card
context. The 32 KiB prompt target remains advisory; larger current decisions
remain valid within the configured hard runtime ceiling.

The reviewed Isochron Scepter copy-and-cast handler holds activation on an empty
stack when its public imprint is a known counter that requires a spell target.
Unknown imprints, modal effects and live stacks retain inference. This removes
repeated quiet-window model passes and avoids paying to copy an uncastable counter.

At the initial seven-card opening decision, a profile's `opening_colors` can
require access to at least one primary colour. If ordinary known lands supply
none, and all other known definitions lack possible ramp/fixing, the shared
policy takes an offered mulligan. An unclassified strategic role does not
invalidate this known mana deficiency. Unknown definitions, utility lands,
possible fixing, smaller hands and later mulligans retain their existing logic.
Profiles can omit or clear `opening_colors`, or override `_opening`.

The compact `actingCommander` summary repeats its current visible zone and, when
applicable, the existing printed-ability suppression annotation. This grounds
commander availability and recovery plans without making the model reconstruct
those facts from several card-table columns. Zones come from the acting seat's
visible zone membership; unknown zones are omitted.

Reviewed single-mode destruction offers can restore a disabled commander before
optional development. Golgari Charm's native enchantment-destruction mode is the
first registered example. Its fixed mode index, description, single native target
contract and ordinary cost must all match. The shared policy selects a
non-indestructible hostile Aura whose destruction removes the commander's last
ability-suppression attachment. Unknown modes or extra costs defer. Trusted deck
`self_target_exceptions` can leave the source's plan with the deck/model.

Public facts optionally mark `friendlyStaticPermanent` for ordinary permanents
whose only scripted behavior consists of positive own-board stat/keyword grants
through reviewed friendly group filters. Triggers, activated abilities, entry
replacements, spell effects, hostile filters and unknown static effects exclude
this flag. It lets a protection tag on a passive permanent participate in ordinary
development; reactive protection spells still defer. Engine profiles can classify
an otherwise unlabelled friendly permanent as a payoff after establishing the
commander or an engine. Existing explicit deck roles take precedence.

Complete cast choices also state whether their native offer uses the mana cost or
an alternative cost. An ordinary affordable Sephara offer needs seven mana, not
her separate optional tap-four-flyers payment. Native affordability and complete
payment contracts remain authoritative.

`simpleTargetUntapAbilities` is exported from a whole targeted untap effect with
unrestricted instant timing and only mana/tap costs. Return, sacrifice, life or
mixed-effect abilities do not qualify. On quiet noncombat windows, the shared
bounds remove already untapped or opposing targets and hold these resource
abilities until an own main phase or an end step. Live stacks and combat retain
their choices; unknown target state stays uncertain. Native payment corrections
bypass scheduling. `reaction_timing_exceptions` can approve another timing, while
`self_target_exceptions` can approve an unusual source/target plan. This covers
Wirewood Lodge, Staff of Domination and Thousand-Year Elixir through the same
native primitive.

A native `CastSpellMode` offer with one fixed mode and an ordinary representable
payment/target contract can be enumerated as complete numbered choices. The
original semantic ID and native template retain the mode; the model supplies no
mode indices or free-form parameters. Unresolved modes, multiple selected modes,
prebound mode targets, mode-override parameters and genuinely combinatorial
contracts retain the native schema.
Explicitly empty opponent combats do not create an untap/blocker decision;
unknown combat metadata retains model control.
