"""Check official Scryfall bulk revisions and refresh an isolated catalog.

No timer or service is installed here. A caller may run this one-shot command
later under the dedicated catalog account after choosing an operating cadence.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import tempfile

from .catalog_loader import (
    CatalogLoadError, KINDS, _exclusive_output, _publish_locked,
    download_source, fetch_manifest,
)


_SNAPSHOT_ID = re.compile(r"[0-9a-f]{24}\Z")


def _current_sources(root: Path) -> tuple[str, dict] | None:
    pointer = root / "current.json"
    if pointer.is_symlink():
        raise CatalogLoadError("invalid catalog pointer")
    if not pointer.exists():
        return None
    if not pointer.is_file() or pointer.stat().st_size > 1_000_000:
        raise CatalogLoadError("invalid catalog pointer")
    try:
        active = json.loads(pointer.read_text(encoding="utf-8"))
        snapshot_id = active["snapshot_id"]
        if not isinstance(snapshot_id, str) or not _SNAPSHOT_ID.fullmatch(snapshot_id):
            raise ValueError("invalid snapshot ID")
        metadata_path = root / "snapshots" / f"{snapshot_id}.json"
        database_path = root / "snapshots" / f"{snapshot_id}.sqlite"
        if (metadata_path.is_symlink() or database_path.is_symlink()
                or not metadata_path.is_file() or not database_path.is_file()
                or metadata_path.stat().st_size > 1_000_000):
            raise ValueError("missing snapshot")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if (metadata["snapshot_id"] != snapshot_id
                or not isinstance(metadata["sources"], dict)
                or set(metadata["sources"]) != set(KINDS)
                or any(not isinstance(metadata["sources"][kind], dict) for kind in KINDS)):
            raise ValueError("invalid snapshot metadata")
        return snapshot_id, metadata["sources"]
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise CatalogLoadError("invalid current catalog metadata") from exc


def _changed(manifest: dict, current: tuple[str, dict] | None) -> bool:
    if current is None:
        return True
    _, sources = current
    return any(sources[kind].get("url") != manifest[kind]["jsonl_download_uri"]
               or sources[kind].get("compressed_size") != manifest[kind]["compressed_size"]
               for kind in KINDS)


def check_refresh(root: Path) -> dict:
    """Read the official manifest and local metadata without changing the catalog."""
    root = Path(root)
    current = _current_sources(root)
    manifest = fetch_manifest()
    return {"current_snapshot_id": current[0] if current else None,
            "refresh_needed": _changed(manifest, current),
            "official_updated_at": {kind: manifest[kind]["updated_at"] for kind in KINDS}}


def refresh_catalog(root: Path) -> dict:
    """Skip unchanged sources; otherwise publish a complete atomic snapshot."""
    root = Path(root)
    with _exclusive_output(root):
        current = _current_sources(root)
        manifest = fetch_manifest()
        if not _changed(manifest, current):
            return {"current_snapshot_id": current[0], "refresh_needed": False,
                    "published": False}
        with tempfile.TemporaryDirectory(prefix="commander-gym-catalog-") as work:
            sources = {kind: download_source(kind, manifest[kind], Path(work)) for kind in KINDS}
            metadata = _publish_locked(root, sources)
        return {"current_snapshot_id": metadata["snapshot_id"],
                "refresh_needed": True, "published": True,
                "official_updated_at": {kind: manifest[kind]["updated_at"] for kind in KINDS}}


def main() -> None:
    parser = argparse.ArgumentParser(description="Check or refresh the official public Scryfall catalog")
    parser.add_argument("--output", required=True, type=Path, help="isolated local catalog root")
    parser.add_argument("--check-only", action="store_true", help="read manifest and local metadata only")
    args = parser.parse_args()
    result = check_refresh(args.output) if args.check_only else refresh_catalog(args.output)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
