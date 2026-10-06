# EDHREC commander context comparison draft

This is a proposed opt-in feature and operator note, not repository guidance.
No live EDHREC call or deployment was made for this draft. The existing
Scryfall catalog stays snapshot-pinned; Argentum remains the rules authority.
EDHREC deck themes are distinct from Scryfall's advisory card tags.

## Contract

`compare_commander_themes` compares one commander in exactly two explicitly
chosen contexts: theme A vs theme B, or theme vs overall (`null`). It returns
each card's observed inclusion count and its own `potential_decks` denominator,
inclusion percentage, optional average-deck quantity, lift **ratio**,
synergy **percentage**, and A-minus-B difference in percentage points. A
missing count or denominator stays null; a reported zero stays zero. Zero
denominator means the rate is null. The response reports resolved-card
coverage, unresolved exact-name count, source URLs, and retrieval times.
Themes may overlap, and overall includes themed decks; no complement is
inferred. Source-derived commander names in both responses must resolve
exactly to the requested Oracle ID in the pinned Scryfall catalog. Exact
card or face names must resolve uniquely; ambiguous names remain unresolved.
Average-deck entries carry their own quantities and exact Oracle joins where
possible. If the source omits a theme list or average deck, that field is null,
not an empty list or an invented value. Unresolved average-deck names are
counted but not echoed into the tool result.

## Proposed read path

`commander_gym.edhrec_http.HttpEdhrecSource` proposes one JSON page per
requested context, at `https://json.edhrec.com/pages/commanders/{slug}.json`
or `https://json.edhrec.com/pages/commanders/{slug}/{theme}.json`. **The theme
route and live response shape are unverified.** The projector currently
expects `container.json_dict.card.name`, `cardlists[].cardviews[]` with
`num_decks`, `potential_decks`, optional `synergy` as a fraction and optional
`lift` as a ratio, optional `average_deck`/`avgdeck` entries, and optional
`panels.taglinks`. It also requires `container.json_dict.selected_theme_slug`
to match the requested theme (explicit null for overall). That field is a
proposed binding check, **not verified in live EDHREC data**; if the real page
uses different evidence, revise the mapping after authorization and review.
Unknown shapes fail closed; it does not probe alternate
routes. Before live use, validate this mapping against one authorized,
user-requested context and revise it through review if necessary.

The HTTP reader is GET-only, with a fixed EDHREC origin, meaningful User-Agent,
JSON Accept header, no credentials, proxies, redirects, retries, pagination,
or continuation requests. It enforces a response byte cap, connect/read
timeout, elapsed-time check, bounded rows, one in-flight request, and at least
two seconds between starts. A 403 or 429 stops that reader immediately. Raw
payloads and projected records are held only in process memory; there is no
disk cache, database ingestion, background fetch, bulk access, or raw payload
logging. Tool output is bounded and marks source data untrusted. EDHREC
results are ephemeral, so historical replay is unavailable.

## Activation and review gate

The stdio MCP entry point still exposes only the original four Scryfall tools
by default. The proposed fifth read-only tool appears only if an operator
launches it with **both** `--enable-edhrec` and
`--edhrec-access-reviewed`. Neither flag is present in the deployed service.
These flags assert a separate access decision; they do not grant permission.
Deployment would require a reviewed service-command change, source-shape
validation, and a refreshed ChatGPT tool definition. Reverting the command to
its existing form returns to the four-tool catalog without touching snapshots.

EDHREC's [terms](https://edhrec.com/terms) restrict automated searches,
requests, and queries. An on-demand pattern does not resolve permission.
Obtain a clear access determination from EDHREC before any live fetch or
activation. If permission is not available, the pure comparison layer can
operate on user-supplied context data without contacting EDHREC.

Tests use synthetic fixture pages and a local loopback HTTP server only. They
exercise success, source projection, exact identity, average-deck quantities,
byte and time limits, host and redirect restrictions, 403/429 stop, malformed
data, and the opt-in switch. No EDHREC endpoint is contacted by the tests.
