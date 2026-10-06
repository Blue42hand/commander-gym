# Research and evidence

## EDHREC discovery

Use EDHREC first for the relevant commander or theme/color search, then inspect suitable themes, inclusion/popularity, average lists, commander-specific Lift, and any labeled synergy data. Treat these as different discovery lenses. Recheck the live interface rather than assuming a filter or metric exists everywhere.

For consequential claims, retain the URL, retrieval date, page/context, commander/theme, bracket/budget filters, date window when shown, deck count, metric name/value/units, and relevant limitations. Do not fabricate unavailable metadata.

Check whether a candidate's apparent prevalence plausibly reflects precon inclusion, budget, card age, mixed subthemes, or a mixture of casual and competitive builds. A card appearing in a precon is neither automatically good nor automatically bad; explain its actual role and opportunity cost. Do not import cEDH, expensive staples, or Game Changers merely because aggregate recommendations contain them. Likewise, do not exclude an excellent inexpensive staple for being common.

## Lift, inclusion, and synergy

Verify the currently displayed metric's definition and scale. EDHREC's [October 1, 2026 announcement](https://edhrec.com/articles/changelog-replacing-synergy-with-lift-on-edhrecs-card-pages) describes expansion of Lift to commander/tag/combo pages and a ratio centered on 1, unlike the older percentage-centered synergy measure. Use this dated source as a starting point, not an eternal interface contract. Older descriptions or exports may use a transformed scale; preserve their labels and units rather than comparing incompatible values.

Use commander-specific Lift explicitly to spot proportionally associated candidates that raw popularity can obscure. Also inspect inclusion and sample size: a large Lift in a tiny or highly selected population is fragile. Neither Lift nor old synergy proves mechanical synergy, competitive strength, or causal benefit. Validate the card interaction and deck fit independently.

Do not invent EDHREC Lift from incomplete inputs. If calculating a separate statistic, label it as your own inclusion difference or ratio, show its inputs, and do not call it the site's Lift. Missing Lift is missing evidence, not a neutral value.

For a same-commander theme comparison, record both inclusion counts and denominators: theme decks containing the card / eligible theme decks, versus the explicitly identified comparison population. Match snapshot, time window, commander, bracket/budget settings, and card eligibility as far as the sources allow. State mismatches.

- Theme versus all-commander decks overlaps; do not describe it as independent groups.
- Derive a non-theme complement only if the populations really nest and the same eligibility/snapshot rules apply. Never subtract unrelated dashboard totals.
- Keep differences in percentage points separate from ratios. If a denominator is zero or unavailable, do not manufacture a number.
- Do not generalize a theme-associated card into all builds of the commander or infer a real-world win-rate gain.

## Exact cards and broader search

For Tolaria, discover tools rather than assuming full Scryfall query syntax. Call `catalog_status` and pin the returned snapshot. Use structured `search_cards` and `search_tags`, then `get_card` with returned Oracle/printing IDs. Preserve pagination and resolve all relevant pages before claiming exhaustive coverage. The current catalog exposes advisory Oracle tags; a missing tag is not proof that an effect is absent.

Combine effect/Oracle-text, type, color identity, and mana-value searches with tag discovery. Search alternative wordings when a narrow term misses functional substitutes. Resolve exact faces, costs, restrictions, triggers, and rulings before final selection. Never silently fuzzy-match an ambiguous card name.

Use EDHREC-rank sorting and price limits where the current source supports them. Do not invent unsupported tool parameters. If sorting fetched results locally, describe the bounded set accurately; it is not a complete rank-sorted catalog. Distinguish Oracle identity from printing: prices belong to a printing/finish/source and may be absent.

Use a relevant connected plugin when it supports the read. Direct EDHREC pages and official Scryfall/Wizards sources are legitimate alternatives when accessible. A custom EDHREC MCP is optional, not a prerequisite. For technical failure, try a permitted supported route; after an explicit access denial or rate limit, stop that access attempt and respect the restriction. Disclose source gaps and use available evidence rather than delaying all construction or inventing results.

## Claim labels

Separate:
- Verified card/rules/price fact, with source date and applicable scope.
- Construction judgment or matchup assumption, with reasoning.
- Theoretical line, with stated requirements and interaction assumptions.
- Actual observed game/goldfish evidence, with its recorded mode and limitations.

Keep broad efficacy, local-meta efficacy, popularity, novelty, and engine support distinct. A compelling explanation is not a played result.
