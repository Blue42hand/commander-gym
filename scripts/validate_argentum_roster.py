#!/usr/bin/env python3
"""Fail-closed validation for an exact Commander roster against Argentum source."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path


CARD_RE = re.compile(r'\bcard\(\s*"((?:[^"\\]|\\.)+)"')
BASIC_LANDS = {"Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes"}


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def card_names(argentum: Path) -> set[str]:
    names: set[str] = set()
    for path in (argentum / "mtg-sets").glob("**/src/main/**/*.kt"):
        for match in CARD_RE.finditer(path.read_text(encoding="utf-8", errors="replace")):
            names.add(bytes(match.group(1), "utf-8").decode("unicode_escape"))
    return names | BASIC_LANDS


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--argentum", type=Path, required=True)
    parser.add_argument("--allow-revision-mismatch", action="store_true")
    args = parser.parse_args()

    manifest = load(args.manifest)
    expected = manifest["argentum"]["revision"]
    actual = subprocess.check_output(
        ["git", "-C", str(args.argentum), "rev-parse", "HEAD"], text=True
    ).strip()
    errors: list[str] = []
    if actual != expected and not args.allow_revision_mismatch:
        errors.append(f"Argentum revision mismatch: expected {expected}, got {actual}")

    implemented = card_names(args.argentum)
    root = args.manifest.parent
    for entry in manifest["decks"]:
        deck_path = root / entry["file"]
        primer_path = root / entry["primer"]
        deck = load(deck_path)
        cards = deck["cards"]
        total = sum(cards.values())
        if total != 100:
            errors.append(f"{deck['deck_id']}: {total} cards, expected 100")
        commander = deck["commander"]
        if cards.get(commander) != 1:
            errors.append(f"{deck['deck_id']}: commander must occur exactly once")
        duplicates = sorted(name for name, count in cards.items() if count != 1 and name not in BASIC_LANDS)
        if duplicates:
            errors.append(f"{deck['deck_id']}: nonbasic duplicates: {', '.join(duplicates)}")
        missing = sorted(set(cards) - implemented)
        if missing:
            errors.append(f"{deck['deck_id']}: absent from Argentum source: {', '.join(missing)}")
        if sha256(deck_path) != entry.get("sha256"):
            errors.append(f"{deck['deck_id']}: deck digest drift")
        if not primer_path.is_file():
            errors.append(f"{deck['deck_id']}: missing primer {entry['primer']}")
        elif sha256(primer_path) != entry.get("primer_sha256"):
            errors.append(f"{deck['deck_id']}: primer digest drift")

    if errors:
        print("\n".join(f"ERROR: {error}" for error in errors))
        return 1
    print(f"validated {len(manifest['decks'])} decks against Argentum {actual}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
