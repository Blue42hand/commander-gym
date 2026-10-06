---
name: commander-deckbuilding
description: Build, evaluate, and revise Magic Commander decks around the user's commander or theme/colors, budget, bracket, and desired play patterns. Use for new brews, upgrades, cuts, alternatives, and deck reviews. Research EDHREC and exact card data, verify a 100-card list, explain choices, and provide winning-oriented piloting guidance. Construction does not automatically launch playtests.
---

# Commander Deckbuilding

Make a good deck by fulfilling this request accurately. Optimize for winning within the agreed brief, including any novelty or play-pattern goals; do not impose a fixed power style or an obscurity quota. Welcome inexpensive staples when they earn their slots.

## 1. Establish the brief

- Recover the exact existing list and relevant constraints when revising. Never reconstruct an unseen deck from memory.
- Capture a specified commander **or** the desired theme/colors from which to choose one. Definitely establish the budget and bracket before finalizing a build. Reuse clear prior answers; ask only for consequential missing information.
- Record the budget's currency and basis: whole deck, new purchases, or upgrade package; whether the commander/basics count; and which specific cards are already owned. Do not assume an expensive card is owned. Keep cumulative upgrade spending within its agreed cap.
- Separate hard requirements from preferences: required/excluded cards, combo/tutor/fast-mana or other table restrictions, preferred lines, and known opponents. Distinguish general performance from a specific meta. Do not invent opponents or metagame weights.
- Use low-budget casual bracket 2–3 as the usual conversational context, **not** a substitute for the requested budget/bracket. Let each brief override it. As soft, budget-relative guidance, cards around $5 or less are usually comfortable; a roughly $10 card must earn its slot; purchases at $20+ are unusual. These are not bans, and confirmed owned pulls may change the calculation.

## 2. Research EDHREC first, then search the card space

Start with EDHREC to explore commanders, themes/colors, baseline cards, and average lists. Inspect popularity/inclusion, **commander-specific Lift**, and synergy information where available. Read [research-and-evidence.md](references/research-and-evidence.md) for source handling, cohort comparisons, Lift units, and fallback behavior.

- Critically inspect precon overrepresentation and aggregate data mixing casual brackets, optimized/cEDH builds, and Game Changers. Use relevant filters when available; disclose residual mixture when they are not. Do not treat the average list as a finished recommendation.
- Use popularity and Lift to discover candidates, not prove power. Compare same-commander theme inclusion with denominators when useful. Never invent missing Lift, samples, or filter availability.
- Expand through Scryfall effect/Oracle-text, Oracle-tag, type, color-identity, mana-value, and budget searches. EDHREC-rank sorting is often useful when supported, but must not hide functional alternatives. Cheap, effective staples and interesting lesser-played options both belong in the candidate pool.
- Prefer the connected Tolaria Scryfall catalog for exact records. Call `catalog_status`, pin its immutable snapshot ID throughout discovery/lookup, and preserve freshness. Treat tags as advisory, rank as popularity, and missing prices as unknown. Inspect exact Oracle records/faces/rulings for finalists.
- Use direct EDHREC and official card/rules sources when available; do not require a custom EDHREC MCP. Respect access denials and rate limits. If a source is unavailable, continue with permitted sources and identify which evidence is missing.

## 3. Build a coherent plan and functional coverage

State how the deck develops resources, gains advantage, presents a win, interacts, recovers, and functions when its commander is removed. Explain the chosen interesting cards/lines and their opportunity costs. Compare packages and credible substitutes by **price-to-impact in this deck**, not standalone reputation.

Use the user's coverage rule of thumb: **minimum 10 ramp, 10 draw, and 10 interaction; aim for 12–15 in a category when the deck benefits.** Audit meaningful functional coverage rather than padding labels. Show overlap explicitly and justify any exception below 10. Do not make the counts a substitute for curve, timing, reliability, or the brief. Read [validation-checklist.md](references/validation-checklist.md) for counting and mana checks.

- Distinguish early reliable ramp from expensive or conditional resource engines. Distinguish net cards/sustained card access from selection-only cantrips. Distinguish interaction that answers relevant threats from narrow or self-protective effects.
- Count each physical card once in the deck total. Multi-role cards may appear in several role audits, with overlap disclosed. Show commander-provided coverage separately and consider commander absence/removal.
- Check actual casting requirements, colored pips by turn, realistic reductions/alternate costs, ramp timing, curve, enabler/payoff density, resilience, and ability to convert advantage into a win. Do not equate mana value with casting cost.
- Require every nonbasic land to justify its slot against a basic. Usually favor basics in one or two colors over weak unconditional tapped lands. Consider useful conditional tapped lands, especially at three or more colors; use premium untapped fixing when the budget supports it. Evaluate color demand, timing, conditions, and utility rather than applying a blanket tapped-land ban.
- Separate broadly useful choices from matchup-specific concessions. Explain the cost of a narrow meta answer outside that matchup.
- Check proposed combos against exact text and current rules. Identify setup, costs, resource changes, stopping conditions, and relevant interaction windows. An analyzed line is not evidence that a game was played.

## 4. Verify the final candidate

Run the full [validation-checklist.md](references/validation-checklist.md) before presenting a completed build or revision. Verify the exact 100-card count including commander(s), quantities and exceptions, commander eligibility/pairing, color identity, current legality, bracket/table constraints, prices, and budget basis. Recheck the complete list after every swap.

Use deterministic counts and exact card identities. Consult current official rules and format/bracket sources for unsettled construction questions. Tolaria catalog legality supports discovery; preserve Argentum as native gameplay rules authority. Do not call a catalog lookup or an assistant's review proof of engine support.

If a check is blocked, deliver a clearly labeled provisional candidate with the exact unresolved issue; do not silently fill prices with zero, assert legality from stale data, or claim compliance with an unknown budget/bracket.

## 5. Deliver the list and pilot

For every completed build or revision, always provide the **exact 100-card list including commander(s), plus piloting information**. Keep any maybeboard outside those 100. A fuller primer is optional. Answer narrow follow-up questions at their requested scope; do not invent a replacement list when the source deck is unavailable.

Include:

1. The brief and any explicit assumptions; the complete importable list and validated total.
2. Budget/legality/bracket checks with dates and unresolved limits; functional ramp/draw/interaction counts, overlap, and exceptions; key mana decisions.
3. A concise game plan, mulligan guidance, sequencing, win routes, interaction/target priorities, and recovery play. Pilot to win within the brief, not to stage attractive showcase lines. Avoid scripted decisions dependent on future draws or opponents' hidden cards.
4. Reasons for important inclusions/cuts, meaningful alternatives, and the expected cost/benefit of changes. Tie basic role assignments to the whole list or coherent packages.
5. Optionally, a separate maybeboard whose entries each state a reason or hypothesis, potential cut, and a useful test. Do not smuggle extra cards into the final total.

Use [revision-and-testing.md](references/revision-and-testing.md) when saving a candidate, writing a fuller primer/pilot, interpreting games, or handing work to Commander Gym.

## 6. Preserve evidence and testing boundaries

Version the candidate, its brief, primer/pilot, and hypotheses together through the current authorized collection workflow. Preserve prior versions, reasons, provenance, prices, and contrary evidence. Do not silently promote a proposal to the user's adopted deck.

Keep paper analysis separate from actual observed play. **Never invent chat goldfishes, simulated turns, wins, or test results.** The user's manual Archidekt goldfishes and historical Forge play may be useful labeled observations; they are not new Argentum games or interchangeable win-rate evidence.

Treat current Commander Gym as the route to challenging native Argentum games when testing is requested and authorized. Inspect its current instructions/interface rather than hardcoding old paths or registry files. Do not activate the legacy `commander-test-lab` Python/assistant-adjudication or Forge workflows from this skill. Preserve native legal actions, engine rules authority, and hidden-information protections. Do not automatically launch paid tests, increase spending limits, or change repository guidance.

Propose measured refinement from real observations: name the bottleneck, the exact candidate change, the expected effect, and evidence that would support or reject it. Separate deck weakness, pilot error, engine limitation, and uncertainty. Never interpret an unsupported mechanic or censored game as a fair loss or manufacture support for a preferred card.
