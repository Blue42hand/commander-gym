# Read-only card discovery prototype

This is a design and test note, not repository guidance. `ARCHITECTURE.md`
continues to define the ownership boundary and Argentum rules authority.

`commander_gym.card_catalog.CardCatalog` is a local query layer for a prepared
SQLite snapshot. It does not download Scryfall data, run a service, access deck
files, or start games. It can later sit behind a narrow Commander Gym tool
adapter without changing its query contract.

The four operations are `catalog_status()`, `search_tags()`, `search_cards()`,
and `get_card()`. Card search accepts bounded, structured filters for name,
Oracle text, type line, exact tag ID, Commander legal status, color identity
subset, and mana value range. It does not parse Scryfall search syntax. Results
are ordered by name and Oracle ID, limited to 50 records per page, and carry a
cursor tied to the selected snapshot. The caller supplies `snapshot_id`; there
is no implicit switch to newer data in the middle of a session.

`create_schema()` defines the offline loader contract. Each card row has a
Scryfall Oracle ID and one selected printing ID. This prototype does not
represent all printings. Snapshot metadata records separate card and tag
sources and update times. A loader should populate a complete snapshot in a
transaction, validate IDs and referential integrity, then publish its ID for
readers. The repository does not yet contain that loader or a full data copy.

Tag memberships are advisory. An untagged card may still perform the role.
`price_usd = null` means unavailable, not free. Commander legality is source
data for discovery, not a rules decision; Argentum remains authoritative for
game rules and legal actions. No private deck or experiment data is read.

## Source check and remaining integration work

The official `https://api.scryfall.com/bulk-data` manifest was read with a
meaningful User-Agent and `Accept: application/json` on 2026-10-05. It listed
`oracle_cards`, `default_cards`, `all_cards`, `unique_artwork`, `rulings`,
`oracle_tags`, and `art_tags`, using `jsonl_download_uri` and
`compressed_size` (not legacy `size`). The `oracle_tags` entry had
`compressed_size: 5978797` and `updated_at: 2026-10-04T21:00:33.620+00:00`.
An HTTP range request read only the first 131,072 bytes of the official
[`oracle_tags` gzip](https://data.scryfall.io/oracle-tags/oracle-tags-20261004210033.jsonl.gz)
(206 Partial Content, `Content-Range: bytes 0-131071/5978797`). Its complete
sampled records had
`object: tag`, `type: oracle`, `id`, `label`, and `taggings` containing
`oracle_id` and `weight`; they also carried `slug`, `uri`, `description`,
`parent_ids`, `child_ids`, and `aliases`. `normalize_oracle_tag()` projects
the observed tag ID, label, and Oracle memberships. It does not claim that
the sample covers all record variants. Card and art-tag record shapes were
not downloaded or validated. All query tests still use synthetic fixtures.
Full card bulk downloads, scheduled refresh, and remote MCP transport are
outside this prototype.

The first complete sampled record had tag ID
`00155182-3099-4742-be68-f8b4ea259d78`, label
`tutor-creature-giant`, alias `tutor-giant`, and one tagging with Oracle ID
`2445e58b-87ed-4ab2-8209-a5e1f566fba7` and weight `median`.

Run fixture tests with `python -m unittest discover -s tests -p test_card_catalog.py -v`.
