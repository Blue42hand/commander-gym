# Public card catalog: local MCP and refresh preparation

This is an implementation and decision note, not repository guidance. The
catalog contains public Scryfall data only. Argentum remains authoritative for
rules, card implementations, and legal game actions. Card coverage work can use
Oracle text and rulings as evidence, but the existing per-card implementation,
scenario-test, and manual review standards still apply.

## Local read-only MCP adapter

The optional adapter uses the [official Python MCP SDK](https://github.com/modelcontextprotocol/python-sdk)
over **stdio only**. Install `requirements-catalog-mcp.txt` in a dedicated
environment and start:

```sh
python -m commander_gym.catalog_mcp --catalog-root /path/to/isolated/public-catalog
```

Its only tools are `catalog_status`, `search_tags`, `search_cards`, and
`get_card`. It does not expose a path argument, arbitrary SQL, shell commands,
deck files, game state, credentials, refresh, or card-implementation actions.
`catalog_status` discovers the active snapshot ID. Every other tool requires an
explicit ID, so a chat or coverage job can keep querying the same immutable
snapshot after a refresh. Older snapshots must be retained while callers use
their IDs and cursors. Responses are limited to 500,000 encoded bytes; card
search and tag search also keep their existing page bounds.

Results include official source URL, dataset update time, download time,
compressed size, and SHA-256 for each loaded dataset. `catalog_status` also
reports whether the requested snapshot is still current. Printing IDs and
Oracle IDs remain separate. Missing prices are null, and tags are advisory:
absence of a tag does not prove the absence of a gameplay role. Structured
search is not Scryfall search syntax.

The adapter does not create a listener or authorize a remote caller. The
[ChatGPT plugin connection guide](https://developers.openai.com/plugins/deploy/connect-chatgpt)
currently describes two remote paths: a public HTTPS streamable-HTTP MCP
endpoint, or a [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)
to a private stdio/HTTP server. The tunnel requires Platform permissions and a
runtime API key; public HTTPS needs an authentication design and public ingress.
Neither connection is configured by this change. The official guide describes
adding a custom MCP plugin on ChatGPT web. Phone support for a custom MCP plugin
has not been verified, so a web-first test is the acceptance path before any
claim about mobile availability.

## One-shot refresh command for a future scheduler

`python -m commander_gym.catalog_refresh --output /path/to/isolated/public-catalog --check-only`
reads the official bulk manifest and local snapshot metadata without changing
the catalog. Omit `--check-only` to run one refresh attempt. It compares the
official dataset URL and compressed size for `default_cards`, `rulings`, and
`oracle_tags` with the active snapshot. Unchanged sources cause no bulk
download. Changed sources invoke the existing bounded loader under its
exclusive lock; all three datasets are downloaded and a complete new SQLite
snapshot is validated before the pointer changes. A failed import leaves the
previous pointer in place. This command is not an installed schedule.

A proposed operating cadence is one daily check after the expected Scryfall
bulk publication window, with alerting if official source dates become stale
or consecutive refreshes fail. The operator must decide the cadence, retention
and disk budget for immutable old snapshots, how alerts are delivered, and how
the catalog account's restricted outbound Scryfall access is provided. No
timer, host service, public endpoint, tunnel, account permissions, or credential
has been changed here.

For Argentum card-coverage work, consume this public catalog by pinned
snapshot ID and record the Oracle/printing IDs plus source update dates with
each evidence note. Continue to check the relevant Scryfall Oracle text and
rulings and follow Argentum's card-add guidance; a catalog tag or legality
field alone cannot establish implementation correctness.
