# Read-only card discovery prototype

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

The designated official source is `https://api.scryfall.com/bulk-data` with a
meaningful User-Agent and `Accept: application/json`. On this task host, its
DNS lookup failed with `[Errno 8] nodename nor servname provided, or not known`
on the first request. Therefore this prototype uses synthetic fixtures only.
The current manifest fields and `oracle_tags` record shape remain unverified
here. Before authoring a loader, fetch the current manifest and a small bounded
official tag sample, verify fields including `jsonl_download_uri` and
`compressed_size`, and map actual tag records to this schema. Full card bulk
downloads, scheduled refresh, and remote MCP transport are outside this
prototype.

Run fixture tests with `PYTHONPATH=. pytest tests/test_card_catalog.py -q`.
