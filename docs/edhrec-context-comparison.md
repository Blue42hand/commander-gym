# EDHREC context comparison prototype

This is an offline implementation note, not repository guidance or a live
connection. The existing Scryfall catalog remains snapshot-pinned. Argentum
remains authoritative for rules. EDHREC deck themes are separate from
Scryfall's advisory card tags.

`commander_gym.edhrec_compare` accepts a deliberately narrow in-memory context
envelope from an injected source. It does not include an HTTP client, URL
discovery, persistent payload storage, a background job, or a bulk downloader.
`catalog_mcp.build_server()` exposes `compare_commander_themes` only when a
source is explicitly injected; the normal four-tool catalog server is
unchanged. The stdio entry point does not configure an EDHREC source.

One request compares exactly two contexts for the same commander: theme A vs
theme B, or one theme vs overall (`null`). Each context has its source URL and
retrieval time and source-derived commander name. Both commander names must
resolve exactly to the caller's pinned Scryfall Oracle ID; mismatches and
unverifiable names fail closed. The bounded result shows each card's inclusion count,
`potential_decks` denominator, inclusion percentage, optional average-deck
quantity, lift ratio, synergy percentage, and A-minus-B difference in
percentage points. Missing counts and denominators remain null; zero remains
zero. A rate is unavailable if its denominator is zero. The response reports
coverage and unresolved exact-name joins. It makes no complement inference:
themes can overlap and overall contains themed decks.

Count and metric magnitudes are bounded before arithmetic and serialization.
Invalid UTF-8 strings, nonfinite values, and excessively nested JSON fail
closed. Card names and face names join by exact case-sensitive identity only;
ambiguous names remain unresolved.

The injected source is serialized and spaced by at least two seconds. A 403
or 429 stops further calls on that reader, without retry or alternate route.
The parser caps response bytes and card rows; the MCP result has its own byte
cap. Any future transport must enforce a timeout and streamed byte cap before
it returns bytes. The source-specific projection into the internal envelope
must be reviewed against a permitted, minimal live sample. No such sample was
requested for this prototype, so live field mapping is unverified.

EDHREC's [terms](https://edhrec.com/terms) restrict automated searches,
requests, and queries. A small on-demand call pattern does not itself resolve
permission. Live access and any source implementation require a separate
permission determination and review before configuration or deployment.
Fixtures exercise the projection contract without contacting EDHREC.
