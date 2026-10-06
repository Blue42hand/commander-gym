# Validation checklist

Use the current trustworthy collection/validation tooling when available. Otherwise perform transparent deterministic arithmetic and exact-record checks. Do not initialize or run a legacy game engine just to validate a decklist. Preserve the validation inputs and results with the candidate.

## Identity, construction, and format

1. Parse quantities and exact names/Oracle identities. Resolve ambiguous names before counting. Keep commander(s), main deck, and optional maybeboard/other zones explicit.
2. Verify exactly 100 physical cards including commander(s), using the actual quantities. For permitted two-commander configurations, count both in the 100. Do not count each face of a multiface card separately or treat role overlap as extra cards.
3. Check singleton by the relevant card-name identity and current rules, including basic-land and card-specific quantity exceptions. Do not assume all lands are exempt. Handle interchangeable-name printings correctly when relevant.
4. Verify each commander's eligibility and any pairing, background, color-choice, companion, or other special construction condition from current authoritative text/rules. Keep extra-zone entries explicit rather than silently dropping or adding them to the 100.
5. Check color identity from exact records, including all relevant faces, symbols, characteristic indicators, and special rules. Check relevant land/basic-land-type restrictions separately; printed card color alone is insufficient.
6. Check current Commander legality, the official banned list, and the requested bracket/table conditions. Consult current official bracket guidance and Game Changer list rather than embedding an enduring list or inferring compliance from count alone. Review the actual lines and play patterns that matter to the bracket.
7. Record dates and any unresolved issues. Distinguish current validation from a deliberately historical snapshot. Do not equate a catalog's legality field with complete construction validation or gameplay implementation.

Official starting points: [Wizards rules](https://magic.wizards.com/en/rules) and [banned/restricted list](https://magic.wizards.com/en/banned-restricted-list). Follow the current official links for bracket guidance when needed.

## Budget and price-to-impact

- Confirm currency, cap, and whole-deck versus purchases/upgrades basis. Include commander/basics according to the brief; make their treatment explicit.
- Use exact eligible printing/finish prices with source and as-of date, quantities, and known availability limits. Prefer affordable normal playable printings when no aesthetic preference overrides this. A cheapest catalog quote is not a checkout or inventory guarantee.
- Keep missing prices null/unknown. Never use zero as a placeholder or claim the full deck is within budget when unpriced cards could exceed it.
- Confirm ownership/exemption for excluded purchases. Do not reuse a whole-deck valuation as an upgrade quote, or reset an upgrade allowance after each revision.
- Show both total deck value and new-purchase cost when ownership materially changes the conclusion. Respect whatever the user's cap actually covers; mark shipping/tax uncertainty if relevant to a purchase budget.
- Requote affected cards and the full budget calculation after swaps. Judge a higher-price slot against the cheaper in-deck alternative and the rest of the budget. Apply the soft $5/$10/$20 guidance proportionally, not as a universal threshold.

## Functional role audit

Provide counted card names or reproducible assignments for ramp, draw, and interaction. Start from minimum 10 per category and aim for 12–15 when useful. Explicitly justify a lower count; do not silently redefine a role to meet the target.

- **Ramp:** Identify when a card actually accelerates usable mana or land development beyond the normal land drop. Separate cheap/reliable ramp from expensive setup, temporary bursts, conditional Treasure, and cost reductions limited to part of the deck. Ordinary lands are not ramp merely because they make mana.
- **Draw/card access:** Separate net draw and repeatable usable card access from filtering, selection-only cantrips, tutors, and speculative triggers. Explain conditional or nontraditional access rather than treating all of it as equivalent raw draw.
- **Interaction:** Identify targets, speed, restrictions, and relevant threat coverage. Separate broad answers, wipes, counters, narrow hate, and protection; self-protection alone does not establish an adequate answer suite.
- **Overlap:** A functional modal/dual-role card can count in multiple categories if the roles are real. Show the overlap and any mutually exclusive use. Do not add category totals to claim that many unique cards.
- **Commander:** Report its contribution separately from the 99/98 and explain reliance on access, survival, and repeated casting. Do not silently pad the minimum with speculative commander value.

Quantities are a starting diagnostic. Evaluate costs, conditions, sequencing conflicts, resilience, and whether the package delivers the intended resources at the needed time.

## Mana and plan audit

- Verify land count and distinguish unconditional mana sources, conditional sources, MDFC choices, and nonland ramp. Do not double-count an MDFC as both a land played and a spell cast in the same resource plan.
- Map color requirements and pip intensity to turns and available sources, including commander timing. Inspect early untapped sources, restricted mana, requirements for conditional lands, activation costs, and fetchable targets actually in the deck.
- Justify each nonbasic against a basic: fixing, useful types, relevant utility, or another concrete benefit. Pay attention to unconditional tapped lands and colorless utility overload. Basics generally win weak comparisons in one/two colors; multicolor requirements may justify budget conditional tapped lands. Premium untapped fixing still must fit the budget.
- Compare printed curve to realistic mana use, including commander tax exposure, alternate costs, discounts, X spells, and holding up interaction. Do not count hypothetical resources before their enablers exist.
- Check enabler/payoff balance, redundant access to core functions, dependence on the commander, recovery after likely disruption, and actual paths from resources to wins. Explain weaknesses and meta-specific concessions without inventing numerical performance.

## Final gate

Revalidate after the final edit. Confirm that the delivered import list, role audit, price sum, primer, and proposed cuts all describe the same exact candidate. Keep maybeboard entries out of the count. Label unresolved validation as provisional and name the blocker rather than presenting an unverified finished deck.
