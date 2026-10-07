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
from .deckbuilding_methodology import Document, get_methodology


MAX_RESULT_BYTES = 500_000
SERVER_INSTRUCTIONS = (
    "For Commander deckbuilding or revisions, load get_deckbuilding_methodology "
    "for SKILL.md and each of its three references before building. This is a pinned "
    "copy of methodology, not a native ChatGPT skill. For card discovery, call "
    "catalog_status first and pass its snapshot_id to catalog tools. Tags are "
    "advisory, missing price is unknown, and Argentum is authoritative for game "
    "rules and card implementations."
)


def _bounded(result: Any) -> Any:
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > MAX_RESULT_BYTES:
        raise CatalogError("catalog result too large; narrow the query")
    return result


def build_server(root: str | Path):
    """Register bounded public catalog and pinned methodology read tools."""
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
        instructions=SERVER_INSTRUCTIONS,
    )
    readonly = ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                               idempotentHint=True, openWorldHint=False)

    @server.tool(annotations=readonly)
    def get_deckbuilding_methodology(document: Document = "SKILL.md") -> dict[str, Any]:
        """Read one exact, pinned Commander Deckbuilding skill document.

        Read SKILL.md and all three listed references before building or
        revising a deck. The response includes the bundle version, each
        document hash, its canonical skill source, and the selected content.
        """
        return _bounded(get_methodology(document))

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
                     registered_in: str | None = None,
                     limit: int = 20, cursor: str | None = None) -> dict[str, Any]:
        """Search cards using bounded structured filters, not Scryfall syntax.

        Returns Oracle and printing IDs with source evidence. Commander legality
        is catalog data for discovery; Argentum remains the rules authority.
        Optional Scryfall EDHREC and Penny ranks are snapshot-dated, lower is
        more popular, and null means unranked. Sort by name or either rank;
        rank sorts put nulls last and pagination stays snapshot-pinned.
        Optional registered_in selects an exact coverage_id from list_engine_coverage.
        It filters registry presence only, never gameplay correctness. Leave it
        unset for ordinary paper decks; qualified revisions are not deployed claims.
        """
        return checked(lambda: access.search_cards(
            snapshot_id, name=name, oracle_text=oracle_text, type_line=type_line,
            tag_id=tag_id, tag_kind=tag_kind, commander_legal=commander_legal,
            color_identity=color_identity, mana_value_min=mana_value_min,
            mana_value_max=mana_value_max, edhrec_rank_min=edhrec_rank_min,
            edhrec_rank_max=edhrec_rank_max, penny_rank_min=penny_rank_min,
            penny_rank_max=penny_rank_max, sort_by=sort_by, registered_in=registered_in,
            limit=limit, cursor=cursor))

    @server.tool(annotations=readonly)
    def list_engine_coverage(snapshot_id: str) -> dict[str, Any]:
        """List hashed registry evidence pinned to this catalog and engine SHA.

        Select an explicit coverage_id. Verified deployed-engine receipts and
        qualified newer revisions remain separate; no revision is auto-selected.
        Registry presence does not certify gameplay correctness.
        """
        return checked(lambda: access.list_engine_coverage(snapshot_id))

    @server.tool(annotations=readonly)
    def get_engine_coverage(snapshot_id: str, oracle_id: str,
                            coverage_id: str | None = None) -> dict[str, Any]:
        """Look up registry presence for one exact Oracle ID in pinned evidence.

        Missing or ambiguous evidence is unknown. Omit coverage_id to return
        unknown without guessing an engine revision. Gameplay correctness is
        separate and remains unknown; this tool does not restrict paper decks.
        """
        return checked(lambda: access.get_engine_coverage(snapshot_id, oracle_id, coverage_id))

    @server.tool(annotations=readonly)
    def get_card(snapshot_id: str, oracle_id: str | None = None,
                 printing_id: str | None = None) -> dict[str, Any] | None:
        """Get one Oracle identity or printing with faces, rulings, tags, and sources.

        Supply exactly one ID. Missing prices stay null, and tags are advisory.
        """
        return checked(lambda: access.get_card(snapshot_id, oracle_id=oracle_id, printing_id=printing_id))

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local read-only catalog MCP adapter over stdio")
    parser.add_argument("--catalog-root", type=Path, required=True,
                        help="private configured catalog root (never supplied by tool callers)")
    args = parser.parse_args()
    build_server(args.catalog_root).run(transport="stdio")


if __name__ == "__main__":
    main()
