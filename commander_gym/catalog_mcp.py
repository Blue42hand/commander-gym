"""Local stdio MCP adapter for the public, read-only card catalog.

Network reachability, authentication, and ChatGPT connection are separate
deployment decisions. This module never opens a listener.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .catalog_access import CatalogAccess
from .card_catalog import CatalogError
from .edhrec_compare import ContextReader, compare_contexts


MAX_RESULT_BYTES = 500_000


def _bounded(result: Any) -> Any:
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > MAX_RESULT_BYTES:
        raise CatalogError("catalog result too large; narrow the query")
    return result


def build_server(root: str | Path, edhrec_reader: ContextReader | None = None):
    """Register four catalog tools; add comparison only with an injected source."""
    from mcp.server import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp.types import ToolAnnotations

    access = CatalogAccess(root)

    def checked(call):
        try:
            return _bounded(call())
        except CatalogError as exc:
            raise ToolError(str(exc)) from exc

    server = MCPServer(
        "commander-gym-public-cards",
        instructions=("Public Scryfall card discovery only. Call catalog_status first and pass its "
                      "snapshot_id to subsequent tools. Tags are advisory, missing price is unknown, "
                      "and Argentum is authoritative for game rules and card implementations."),
    )
    readonly = ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                               idempotentHint=True, openWorldHint=False)

    @server.tool(annotations=readonly)
    def catalog_status(snapshot_id: str | None = None) -> dict[str, Any]:
        """Get current or pinned snapshot counts, source dates, hashes, and freshness context.

        Omit snapshot_id to discover the current immutable snapshot. Pass that ID
        to all other calls; later refreshes will not change a pinned session.
        """
        return checked(lambda: access.catalog_status(snapshot_id))

    @server.tool(annotations=readonly)
    def search_tags(snapshot_id: str, query: str, kind: str = "oracle",
                    limit: int = 20, cursor: str | None = None) -> dict[str, Any]:
        """Search advisory tag labels; a missing tag does not prove absence.

        Only oracle tags are loaded in the current catalog. Use an exact tag_id
        returned here to filter cards. Pagination is pinned to snapshot_id.
        """
        return checked(lambda: access.search_tags(snapshot_id, query, kind=kind, limit=limit, cursor=cursor))

    @server.tool(annotations=readonly)
    def search_cards(snapshot_id: str, name: str | None = None,
                     oracle_text: str | None = None, type_line: str | None = None,
                     tag_id: str | None = None, tag_kind: str = "oracle",
                     commander_legal: bool | None = None,
                     color_identity: str | None = None,
                     mana_value_min: float | None = None,
                     mana_value_max: float | None = None,
                     edhrec_rank_min: int | None = None,
                     edhrec_rank_max: int | None = None,
                     penny_rank_min: int | None = None,
                     penny_rank_max: int | None = None,
                     sort_by: str = 'name',
                     limit: int = 20, cursor: str | None = None) -> dict[str, Any]:
        """Search cards using bounded structured filters, not Scryfall syntax.

        Returns Oracle and printing IDs with source evidence. Commander legality
        is catalog data for discovery; Argentum remains the rules authority.
        Optional Scryfall EDHREC and Penny ranks are snapshot-dated, lower is
        more popular, and null means unranked. Sort by name or either rank;
        rank sorts put nulls last and pagination stays snapshot-pinned.
        """
        return checked(lambda: access.search_cards(
            snapshot_id, name=name, oracle_text=oracle_text, type_line=type_line,
            tag_id=tag_id, tag_kind=tag_kind, commander_legal=commander_legal,
            color_identity=color_identity, mana_value_min=mana_value_min,
            mana_value_max=mana_value_max, edhrec_rank_min=edhrec_rank_min,
            edhrec_rank_max=edhrec_rank_max, penny_rank_min=penny_rank_min,
            penny_rank_max=penny_rank_max, sort_by=sort_by, limit=limit, cursor=cursor))

    @server.tool(annotations=readonly)
    def get_card(snapshot_id: str, oracle_id: str | None = None,
                 printing_id: str | None = None) -> dict[str, Any] | None:
        """Get one Oracle identity or printing with faces, rulings, tags, and sources.

        Supply exactly one ID. Missing prices stay null, and tags are advisory.
        """
        return checked(lambda: access.get_card(snapshot_id, oracle_id=oracle_id, printing_id=printing_id))

    if edhrec_reader is not None:
        @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                                  idempotentHint=False, openWorldHint=True))
        def compare_commander_themes(snapshot_id: str, commander_oracle_id: str,
                                     commander_slug: str, theme_a: str | None,
                                     theme_b: str | None, limit: int = 20) -> dict[str, Any]:
            """Compare inclusion in two explicitly chosen EDHREC contexts.

            Use null for overall. Theme populations can overlap. EDHREC context
            data is ephemeral and cannot be historically replayed; Scryfall
            identity is pinned to snapshot_id. No live source is bundled.
            """
            def query():
                if not isinstance(commander_oracle_id, str) or not commander_oracle_id or len(commander_oracle_id) > 64:
                    raise CatalogError('invalid commander Oracle ID')
                try:
                    commander_oracle_id.encode('utf-8')
                except UnicodeError as exc:
                    raise CatalogError('invalid commander Oracle ID') from exc
                commander = access.get_card(snapshot_id, oracle_id=commander_oracle_id)
                if commander is None:
                    raise CatalogError('commander Oracle ID unavailable in snapshot')
                result = compare_contexts(
                    edhrec_reader, commander_slug, theme_a, theme_b,
                    lambda name, oid: access.resolve_exact_name(snapshot_id, name, oid),
                    commander_oracle_id=commander_oracle_id, limit=limit)
                result['snapshot_id'] = snapshot_id
                result['commander_oracle_id'] = commander_oracle_id
                return result
            return checked(query)

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local read-only catalog MCP adapter over stdio")
    parser.add_argument("--catalog-root", type=Path, required=True,
                        help="private configured catalog root (never supplied by tool callers)")
    parser.add_argument('--enable-edhrec', action='store_true',
                        help='enable the proposed on-demand EDHREC reader after access review')
    parser.add_argument('--edhrec-access-reviewed', action='store_true',
                        help='operator assertion that EDHREC automated access was separately authorized')
    args = parser.parse_args()
    if args.enable_edhrec != args.edhrec_access_reviewed:
        parser.error('EDHREC requires both explicit enablement and independent access review')
    reader = None
    if args.enable_edhrec:
        from .edhrec_http import HttpEdhrecSource
        reader = ContextReader(HttpEdhrecSource())
    build_server(args.catalog_root, reader).run(transport="stdio")


if __name__ == "__main__":
    main()
