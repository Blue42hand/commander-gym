# Canonical deck collection

Instance repositories can keep all deck lineages in a consistent browsable layout
without mixing collection selection with the active runtime roster. The reusable
validator lives in `commander_gym.deck_collection`; it never contacts a provider,
loads credentials, or starts a game.

## Layout

| Path | Role |
|---|---|
| `catalogs/deck-collection.json` | Explicit collection selections; Binding catalog v1 with collection metadata |
| `decks/README.md` | Generated collection table with direct card-list, primer and binding links |
| `decks/<commander>-<variant>/README.md` | Generated landing page for a distinct deck lineage |
| `decks/<commander>-<variant>/index.json` | Generated exact identity refs and navigation |
| `decks/<commander>-<variant>/revisions/<revision>/manifest.json` | Canonical Deck identity |
| `decks/<commander>-<variant>/revisions/<revision>/deck.json` | Runtime-compatible JSON payload |
| `decks/<commander>-<variant>/revisions/<revision>/decklist.txt` | Generated quantity-prefixed export with Commander/Main tags |
| `decks/<commander>-<variant>/knowledge/<revision>/knowledge.json` | Canonical DeckKnowledge identity and compatibility |
| `decks/<commander>-<variant>/knowledge/<revision>/primer.md` | Immutable primer bytes |
| `decks/<commander>-<variant>/sources/` | Preserved original cards, descriptions, maybeboards, primers and policy material |
| `bindings/<binding-id>/revisions/<revision>/binding.json` | Immutable exact Deck + DeckKnowledge + Pilot combination |
| `pilots/` | Shared versioned canonical Pilots |
| `rosters/` | Explicit active roster catalogs, independently selected |

The present collection validator supports single-commander, 100-slot JSON payloads
with `schema_version`, `deck_id`, `name`, `commander`, and `cards`. Quantities include
the commander. This is a structural contract, not Commander legality validation;
it does not check color identity, singleton rules, banned cards or engine coverage.
Partner/multiple-commander collections need an explicit payload/profile extension
rather than silently discarding commander zones.

## Catalog extension

Keep the ordinary v1 `bindings`, `decks`, `deck_knowledge`, `pilots` and
`active_bindings` fields expected by `load_binding_catalog`. Add
`collection_schema_version: 1` and a nonempty `collection` array. Each entry has:

- `name`, `directory`, and `status` (`active`, `historical` or `imported`).
- `deck`: `manifest`, `payload`, and `card_list` paths relative to the instance root.
- `deck_knowledge`: `manifest` and `payload` paths.
- `binding`: the explicitly selected versioned binding path.
- `coverage` and `qualification`: independent `status` and `detail` strings.
- `sources`: paths to preserved source material.
- Optional `alternate_knowledge`: manifest/payload pairs with a selection `reason`.
- For a migration: `card_source` with `path` and declared `format` (`text`,
  `argentum-json` or `archidekt-snapshot`).

The catalog's `active_bindings` means its advertised selectable profiles. In a
collection catalog it lists collection selections, not the live instance roster.
Use a roster catalog for ordinary active-instance configuration. Do not point a
live runtime at a collection containing unverified decks without coverage preflight.
Only one revision per Binding ID is selected in any runtime catalog; older binding
files remain immutable and a historical catalog can pin them separately.

There is no implicit latest revision. Per-deck indexes, card-list exports and
READMEs are generated views of the catalog, not independent selection authorities.

## Validate or regenerate

```bash
python -m commander_gym.deck_collection --instance-root /path/to/instance
python -m commander_gym.deck_collection --instance-root /path/to/instance --write
```

The command verifies identity fingerprints and exact payload bytes through the
production catalog loader, resolves every selected Binding, and constructs the
ordinary Argentum profile registry with inert validation pilots. It additionally
checks selected primer compatibility, all stored canonical revisions, consistent
catalog selections, and complete navigation. Check mode fails when exports or
indexes are stale. The explicit instance root makes validation independent of the
catalog's subdirectory and of the instance's filesystem location.

An optional `migration` path names a ledger containing `preserved_files` with
`original_path`, current `path`, and `git_blob`. Git blob hashes include Git's blob
header. The validator verifies preserved bytes and compares selected card
quantities with the declared preserved source. Keep the ledger/source commit
pinned in review; a ledger is evidence to inspect, not a license to rewrite history.
Archived import manifests retain their original path references and historical
validation flags. Resolve those paths through the ledger or original source commit.

For ongoing revisions, retain migration evidence for the migrated revisions;
do not silently change its card source to make an intentional deck change look
like a lossless migration. Record new source provenance for new list revisions.

## Updating an instance

Create new immutable Deck/DeckKnowledge/Binding revisions as appropriate, update
explicit collection/roster selections, regenerate navigation, and validate. A
directory-only move of identical artifact bytes does not change the artifact's
identity. Reencoding a payload changes its digest and requires a new Deck revision.
Changing primer text or compatibility requires a new DeckKnowledge revision and
an explicitly updated Binding. Do not carry gameplay qualification onto new pins.

CI should use a reviewed commit pin of public Commander Gym, validate in check
mode, and repeat after copying the instance to a different directory. Keep private
cards and instance metadata in the private repository; public tests use synthetic
fixture instances and contain no private deck material.
