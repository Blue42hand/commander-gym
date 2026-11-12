# Lathril primer policy v1

This is an explicit opt-in skill for the reviewed `lathril-argentum-native-v1`
deck and its exact primer. It does not activate production or change the other
five local Bindings. The owner requested that deck competence take precedence
over unrestricted model flexibility.

## Primer as executable policy

`commander_gym/deck_policies/lathril_v1.json` contains the reviewed objectives,
card roles, weights, land colors and exact deck/primer fingerprints. The skill
identity covers that data, implementation sources and the native fact digest.
Changing any of them produces a new Pilot and Binding identity.

| Primer requirement | Implemented behavior |
| --- | --- |
| Early Elf acceleration and colored mana | Opening-hand checks, colored land ranking, mana-Elf weights before lords on small boards |
| Develop mana before tribal payoffs | Board-sensitive role scores; creature-dependent engines lose priority without creatures |
| Build draw engines and an Elf board | Draw-engine, commander, token-engine and lord priorities; every callback re-evaluates fresh state |
| Use tribal payoffs | Exact `Elf` option for Horn, Banner and Etchings, independent of menu ordering |
| Connect with Lathril | Native attack pairs, conservative unblocked attacks and menace against one blocker |
| Ten-Elf activation | Require eleven untapped Elves including Lathril and the exact offered drain ability; select ten other native cost candidates when asked |
| Preserve recovery | Do not fire off recursion or sweepers merely because they are affordable |
| Tutors | Prefer engines, or interaction when visibly behind; reserve finishers unless a conservative unblocked lethal calculation succeeds |
| Avoid unplanned resource use | Select a productive cast with native automatic payment; otherwise preserve mana instead of arbitrary tap/utility activations |
| Bounded uncertainty | Model ranks at most three complete opponent-removal choices; cannot supply actions, targets or parameters |

Native legality and masked observations remain authoritative. The policy reuses
the existing response/parameter validator. It never executes an admin state,
reads opponent hidden cards, simulates an engine state or queues action IDs.
The existing certified forced-pass component still precedes the deck policy.
Unrepresented decisions fail with an explicit `lathril-unsupported` diagnostic;
they cannot escape into the unrestricted generalist.

## Small model contract

For an ambiguous supported removal choice, the policy freezes at most three
complete legal action/target pairs. The advisor receives only their labels and
relevant masked threat characteristics, with an 8 KiB serialized request limit.
Its response is a single enumerated integer. The selected complete choice is
then validated against the original native offer. Existing local transport,
model-identity, cancellation, deadline and batch-budget checks still apply.
Provider failures and invalid outputs never cause a silent fallback.

The full primer, history and legal-action menu are not sent to this advisor.
No empirical model latency or accuracy claim is made from mocked responses.

## Opt-in preparation

Use `python3 -m commander_gym.local_catalog` with the existing catalog arguments,
`--card-facts <pinned export>` and `--lathril-policy`. This writes a new immutable
catalog directory. Only the exact reviewed Lathril Binding receives the new skill;
other decks retain the generic heuristic skill. Runtime loading validates the
exact deck and primer reference before composing the policy.

## Recorded-position comparison

`scripts/evaluate_lathril_policy.py` accepts repeated `--capture` directories,
`--facts`, a new private `--corpus` gzip path, and a new `--output` JSON path.
It checks the complete journal hash chain, verifies native seat-observation body
hashes, and checks the recorded registered deck. It creates a private corpus of
Lathril callbacks and compares candidate choices with actual historical choices.
Extraction refuses a changing source. Outputs never overwrite captures.

The evaluator makes no network calls. It runs the same native choice validator,
checks that the policy does not mutate observations and reports failures instead
of counting them as coverage. Historical provider time on newly handled positions
is an opportunity estimate, not an observed speedup in a replayed game.

The two available games provide regression coverage, not a statistical holdout:
their mistakes informed the implementation. Different draws and starting-player
orders prohibit treating them as a controlled strength comparison. Off-policy
positions remain useful tests, but changing a move changes all later live states.

## Deliberate strategic limits

Combat is a static, conservative approximation. It does not forecast tricks,
complex evasion, attack taxes, multiplayer retaliation or replacement effects.
Blocking uses offered native pairs and caps, including minimum menace groups,
but does not implement a complete combat search. The overrun proof only handles
unblocked opponents with known life totals. Recursion, modal spells, complicated
payments and tutor constraints are deliberately restricted; the pilot may hold
useful cards or stop on unsupported decisions. A complete future game and win
rate are not established by historical-position coverage.


Opening and land policy corrections (mirror regression)
----------------------------------------------------
Land selection now accounts for Cemetery and forced tapped lands, the useful spells
that a land enables immediately, and colored pips in the visible hand. This is a
planning estimate; native legality and payment remain authoritative. Bottoming
compares complete retained hands, protects the first three land drops and color
coverage, and values tutors and cheap recursion separately from expensive recovery.
A castable Demonic Tutor counts as an early setup line and has an explicit cast
path. Native Scry 1 uses its exact prompt, labels, offered card, and response shape,
without relying on the misleading triggering source name. It bottoms surplus lands
when four land drops are already available, and delays situational cards early.
The recorded mirror regressions cover the missed Sol Ring start, green-source
choice, Witness/Bidding choice, redundant Swamp, tutor opening, and failed scry.
