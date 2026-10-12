# Pinned Argentum registry evidence

This is an implementation note, not repository guidance. The methodology
discovery blocker is closed: the owner refreshed the Tolaria plugin and
retrieved all four pinned methodology documents in an ordinary chat.

The optional coverage layer joins the existing runtime CardRegistry export
from `:gym-server:commanderGymDeckCoverage` to one immutable catalog snapshot.
It does not scan card source files, build Argentum, run games, read decks,
download bulk data, or query a host. The public query layer remains independent
of MCP. No guidance, security controls, server instructions, or deployment
configuration changes are part of this layer.

## Evidence production

The operator supplies the existing CI artifact's sorted, unique
`argentum-registry-card-names.txt`, its `argentum-coverage.json`, and an
independently reviewed qualification receipt. The report must contain
`schema: 1`, `registryCardNames`, and `argentum.repository` / `argentum.commit`.
The names count must match the report. Private roster contents are never copied
into coverage evidence; only the report's byte hash is retained.

A qualification receipt is a JSON object with these exact evidence fields:

```json
{
  "engine_sha": "<40 lowercase hex characters, matching the report>",
  "source_url": "https://github.com/Blue42hand/commander-gym/actions/runs/<run-id>",
  "conclusion": "success",
  "verified_at": "2026-10-07T00:00:00Z",
  "registry_export_sha256": "<SHA-256 of exact export bytes>",
  "coverage_report_sha256": "<SHA-256 of exact report bytes>"
}
```

The receipt records an operator's verification of the CI run, engine SHA, and
artifact hashes. The offline builder validates consistency; it does not contact
GitHub or independently authenticate that attestation. A hash guarantees content
identity, not the truth of an operator's receipt. Review evidence before publishing
under the configured catalog root, using its existing authorized access route.

Run the offline command against a prepared public catalog:

```sh
python -m commander_gym.engine_coverage \
  --catalog-root /path/to/public-catalog \
  --snapshot-id <catalog-snapshot-id> \
  --registry-names /path/to/argentum-registry-card-names.txt \
  --coverage-report /path/to/argentum-coverage.json \
  --qualification-receipt /path/to/qualification-receipt.json
```

The command publishes `engine-coverage/<coverage_id>.json` using exclusive,
atomic publication. Its ID is the full SHA-256 of canonical evidence JSON.
Republication of identical bytes is idempotent; different content receives a
different ID and cannot replace an existing artifact. Artifacts retain the
catalog snapshot, exact catalog identity hash, public Scryfall source metadata,
engine SHA, registry/report hashes, receipts, and per-Oracle mappings. Read
operations validate the digest, source metadata, identity set, and semantic pins.
Discovery refuses stores containing more than 50 artifacts, and each artifact
is capped at 16 MB. Publication does not impose a total storage cap.
Snapshot refresh never migrates coverage silently: rebuild the join for the new
snapshot and retain old IDs while sessions use them.

## Identity and uncertainty

Matching is case-sensitive and exact against the catalog's canonical card name
or a face name belonging to the same Oracle identity. A face with a different
`face_oracle_id` is excluded. There is no fuzzy match, punctuation substitution,
or implicit split-card/front-face normalization. A name shared by multiple
Oracle identities makes those matches unknown, even if another alias matches.
Unmapped registry names are counted without inventing Oracle IDs.

`registry_presence` is `present`, `absent`, or `unknown`. Absence means none of
the exact catalog names appeared in the complete pinned export; it is not a
claim that no differently named engine implementation exists. Missing evidence,
a missing Oracle entry, and ambiguous identity mapping are unknown. A missing
or corrupt explicitly selected artifact fails closed; it does not silently
switch to a different engine or treat unknown as absent. Printing IDs and card
names cannot substitute for Oracle IDs in the lookup.

`gameplay_correctness` is always `unknown`. Even a registered face does not
prove every face, interaction, rule, or gameplay scenario works. Argentum keeps
rules authority; registry evidence is not gameplay qualification.

## Revision selection and query surface

`list_engine_coverage(snapshot_id)` lists public metadata and IDs for this
snapshot without selecting a preferred engine. `get_engine_coverage(snapshot_id,
oracle_id, coverage_id)` looks up one exact identity. Omitting `coverage_id`
returns unknown rather than guessing the deployed version.

`search_cards(..., registered_in=coverage_id)` is an optional positive registry
filter. It includes only unambiguous `present` identities, preserving the normal
structured search and page bounds. Cursors bind to both catalog snapshot and
coverage ID, so changing evidence requires a fresh search. The ordinary search
default is unchanged and imposes no engine limitation on paper-deck requests.
All three operations are exposed through the existing read-only stdio adapter;
callers cannot supply file paths, receipts, or write evidence through MCP.

Evidence without a deployment receipt is labeled `qualified_revision`. To label
it `verified_deployed_engine`, the operator must additionally supply
`--deployment-receipt`: an independently reviewed object with the same
`engine_sha`, a public exact CI-run or commit `source_url`, successful
`conclusion`, timezone-qualified `verified_at`, and a
`deployed_artifact_sha256` observed through an authorized deployment route.
That receipt describes the verified deployment at its recorded time, not a live
host check. It never upgrades a different SHA. Qualified newer revisions remain
separate artifacts and cannot overwrite or auto-select deployed evidence.

Live Tolaria deployment verification and production evidence loading are
deferred until the authorized access route is clarified. The denied host path
has not been retried. Repository validation uses fixtures, existing public CI
exports, and public MCP snapshot metadata.

Run the focused offline suite with:

```sh
python -m unittest discover -s tests -p test_engine_coverage.py -v
```

## Coverage status and submitted deck slots

`coverage_status(snapshot_id, coverage_id?, expected_engine_sha?, expected_gym_sha?)`
returns existing evidence metadata without automatically selecting a revision.
An empty store is unavailable. Explicit missing, corrupt, or mismatched evidence
fails closed. Full lowercase commit pins can be compared to selected evidence;
`stale_for_requested_context` means the catalog is no longer current or a known
pin differs. `matches_requested_pins` requires both pins to match and the catalog
to be current. Otherwise freshness is unknown. It is never a live deployment check
or a time-based freshness guarantee; inspect the retained receipt timestamp.

The existing offline publisher emits schema 2 when the authoritative report has
`commanderGym.repository` and `commanderGym.commit`. It validates the exact public
Gym repository and full SHA, retains only those fields, and hashes them into the
same immutable artifact. Existing schema 1 artifacts remain readable with unknown
Gym identity; no inferred source pin or migration changes old evidence IDs.

`get_engine_coverage` also separates snapshot Scryfall Commander legality,
registry presence, unknown tested card behavior, unknown upstream presence,
registry presence at a deployment receipt's recorded time, and unknown current
deployed support. Successful registry CI is not a per-card behavior test and none
of these outputs asserts human verification.

`get_deck_coverage(snapshot_id, oracle_ids, coverage_id?)` counts between 1 and
1000 submitted slots. Include commander slots and repeat IDs for quantities.
Results aggregate present/absent/unknown registration and Scryfall legality,
with a percentage over all submitted slots (including unknowns). They do not
certify a legal Commander deck or gameplay behavior. There is no deck identifier,
stored deck lookup, trace access, write, or second ledger; the caller's IDs are
not echoed. The read-only service does not persist submitted lists. Operators
must assess their transport logging policy before sending private deck contents.

Rollout remains with the Tolaria host owner. Deploy the reviewed source and rebuild
public evidence with the exact catalog, registry export, report and reviewed CI
receipt; add a deployment receipt only after independently observing the actual
matching artifact. Refresh plugin discovery, then check status, selected pins,
per-card lookup and aggregate counts. Schema 2 artifacts require this reader;
retain schema 1 IDs for sessions until the reader transition is complete.


## Canonical names and explicit front faces (issue 248)

Schema 3 uses canonical_card_or_explicit_front_face_v2. It separates a whole
Oracle card's canonical name from its face aliases. A unique exact canonical
registry name establishes registration for that whole identity; unrelated
art-series or prepare aliases do not make that canonical match ambiguous and
do not themselves establish registration for their parent card.

The pinned engine's CardRegistry.register indexes card.name and may also
index back/flip faces. DeckCoverageCli.writeRegistryNames exports that registry
name set. Its normalizeForRegistry explicitly reports front-face normalization
separately from exact whole-name coverage. These are names, not Oracle IDs or
gameplay tests. The new mapper permits an explicit front match only for a
catalog transform or modal_dfc identity with exactly two own/null-Oracle
faces at indices 0/1 forming the exact canonical Front // Back name.
Pinned naming-contract sources:
[CardRegistry](https://github.com/Blue42hand/argentum-engine/blob/4569820f513321c952a19b00b233e691af4cea9d/rules-engine/src/main/kotlin/com/wingedsheep/engine/registry/CardRegistry.kt) and
[DeckCoverageCli](https://github.com/Blue42hand/argentum-engine/blob/4569820f513321c952a19b00b233e691af4cea9d/gym-server/src/main/kotlin/com/wingedsheep/gym/server/tools/DeckCoverageCli.kt).
Layout comes from that card's matching representative printing. Unknown or
malformed layout, back-only matches, prepare/art-series aliases and foreign
faces cannot grant whole-card support. Split/adventure or other layout aliases
also remain unknown without an explicit naming contract.

A canonical name shared by multiple Oracle identities remains unknown, as do
colliding eligible front identities or a front shadowed by a different whole
canonical owner. Unknown results carry public candidates containing Oracle ID,
canonical name and match role. Case, punctuation and face ownership remain exact;
Commander legality is not used to resolve registry identity.

The role-aware catalog hash includes canonical names, face indices and ownership,
and representative layout evidence. Schema 3 retains the exact UTF-8 registry
export internally, preserving newline bytes, verifies its source hash/count,
and regenerates each provided card entry against the selected catalog on read.
Raw export bytes are omitted from query metadata. Missing card entries remain
unknown. Old schema 1/2 artifacts retain their original flattened mapping,
hashes, results and IDs; no reader migration silently reinterprets them.
Rebuilding produces a new content-addressed coverage ID. Publishing to the live
store and changing its selected evidence remain separate host-owner actions.

Regression fixtures include the 13 issue identities and 15 related aliases
from snapshot d5eac6f597c9feaf9bfce68d, with source hashes retained. They are
small synthetic test databases using captured public identity rows, not a
second coverage ledger. Gameplay correctness and current deployed support
remain unknown regardless of registry mapping.
