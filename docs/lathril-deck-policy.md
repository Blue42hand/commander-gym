# Lathril deck policy v2

Lathril is an automatically selected deck overlay on the [shared policy system](policy-layers.md).
Its registration requires the exact reviewed deck and primer fingerprints. Every
other deck receives the default layer and bounded local inference, with an optional
matching overlay of its own.

`commander_gym/deck_policies/lathril_v1.json` retains its filename for compatibility;
its policy version is 2. It contains the reviewed deck objectives, card roles,
opening/development weights, mana priorities and fingerprints. The skill identity
covers that profile, shared policy/fallback sources and custom Lathril hooks.

| Deck strategy | Lathril behavior |
| --- | --- |
| Accelerate and develop Elves | Prefer early mana Elves, engines, commander and a wide board; delay board-dependent engines without creatures |
| Protect a usable opening | Keep colored mana plus acceleration/early development or a castable tutor; compare complete retained hands using deck role values |
| Use tribal payoffs | Choose Elf by exact label |
| Connect with Lathril | Use native attack pairs, conservative connections and menace; preserve the drain board |
| Activate the drain plan | Require the exact native ability/cost and ten other ready Elves |
| Preserve recovery | Reserve recursion, sweepers and finishers unless the represented plan justifies them |
| Tutor for the plan | Prefer development or interaction when behind; reserve finishers without a demonstrated lethal line |
| Handle uncertainty | Defer unknown actions/decisions and tied removal targets to the bounded fallback |

Reusable land sequencing, bottoming, discard, scry, life/draw payment, reveal and
free-token mechanics live in the shared layer. Lathril modifies their scoring
through hooks instead of copying those handlers. Models are called only by the
fallback layer. Unrepresented native choices no longer raise `lathril-unsupported`
and stop composition; they return explicit deferrals.

The recorded regressions cover land choices, Witness versus Bidding, excess-land
versus tutor bottoming, tutor openings, Scry 1, hand-limit discard, Overgrown Tomb,
Growing Rites, optional library-search bounds and free token triggers. Bojuka Bog's
previously unhandled target choice is a bounded-fallback regression.

Combat remains a conservative approximation rather than complete tactical search.
Native constraints and final execution remain authoritative. Recorded-position
coverage does not establish a completed game, live model strength or win rate.
