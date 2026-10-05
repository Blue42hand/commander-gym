# Local Scryfall catalog loader prototype

This is an operator note, not repository guidance. `ARCHITECTURE.md` still
defines the project boundary. Argentum remains authoritative for Magic rules.

The one-shot command `python -m commander_gym.catalog_loader --output DIR`
downloads only official `default_cards`, `rulings`, and `oracle_tags` entries
selected from the current `https://api.scryfall.com/bulk-data` manifest. It
requires an explicit local output directory. There is no scheduler, listener,
MCP transport, deck access, or game action in this loader.

The 2026-10-05 official manifest and 128 KiB HTTP range samples showed:

| Dataset | Compressed bytes | Observed join key |
| --- | ---: | --- |
| `default_cards` | 78,697,099 | `oracle_id`, printing `id` |
| `rulings` | 5,422,271 | `oracle_id` |
| `oracle_tags` | 5,978,797 | `taggings[].oracle_id` |

The sampled files were the manifest-selected
[`default_cards`](https://data.scryfall.io/default-cards/default-cards-20261004210543.jsonl.gz),
[`rulings`](https://data.scryfall.io/rulings/rulings-20261004210034.jsonl.gz), and
[`oracle_tags`](https://data.scryfall.io/oracle-tags/oracle-tags-20261004210033.jsonl.gz)
gzip files. Each sample used `Range: bytes=0-131071` and returned HTTP 206.

The loader enforces HTTPS on `data.scryfall.io` with dataset-specific paths,
refuses redirects, verifies compressed sizes and SHA-256 hashes, bounds JSONL
line size, record count, and decompressed bytes, and rejects unknown Oracle
references. It builds SQLite with foreign keys enabled, checks integrity, and
publishes an immutable database and metadata file under `snapshots/` before
atomically replacing `current.json`. The prior database and pointer survive a
failed build. Metadata records each official URL, update time, byte size,
SHA-256, and download time. The raw gzip downloads are temporary and are not
retained after publication.

`default_cards` is printing-level data. Every printing is stored with its
price and face evidence. For the one-row-per-Oracle search table, the selected
printing is deterministic: English before other languages, physical before
digital, newest release date first, then printing UUID. The displayed USD
price comes only from that selected printing; a null price stays null even if
another printing has a price. `get_card(printing_id=...)` returns that
printing's separate evidence. Rulings retain source, date, and comment.
Oracle tag membership is advisory; absence never proves a gameplay role is
absent. Only exact single-tag filtering is implemented.

The full public corpus has not yet been downloaded or validated in this
worktree. A real import should be reviewed and run in a private local public
catalog directory on the data volume, coordinated with the tolaria migration
owner. It must not be pointed at existing Gym service, backup, or private deck
directories. No service configuration changes are needed for the import.
