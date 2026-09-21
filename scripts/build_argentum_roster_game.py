#!/usr/bin/env python3
"""Build a directly runnable full-game manifest from an exact roster."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    roster = json.loads(args.manifest.read_text(encoding="utf-8"))
    root = args.manifest.parent
    players = []
    seats = []
    for index, entry in enumerate(roster["decks"]):
        deck = json.loads((root / entry["file"]).read_text(encoding="utf-8"))
        strategy = (root / entry["primer"]).read_text(encoding="utf-8")
        name = f"Seat {index} — {deck['commander']}"
        players.append({
            "name": name,
            "deck": {"type": "Explicit", "cards": deck["cards"]},
            "startingLife": 40,
            "commanderCardName": deck["commander"],
        })
        seats.append({
            "player_name": name,
            "deck_id": entry["id"],
            "deck_version": f"sha256:{entry['sha256']}",
            "primer_version": f"sha256:{entry['primer_sha256']}",
            "pilot": {"backend": "openai_responses", "model": args.model, "strategy": strategy},
        })

    result = {
        "argentum_config": {
            "format": {
                "type": "com.wingedsheep.sdk.core.Format.Commander",
                "commanderDamageThreshold": 21,
                "deckSize": 100,
                "startingLife": 40,
                "startingHandSize": 7,
                "alwaysDivertToCommand": False,
            },
            "skipMulligans": False,
            "useHandSmoother": False,
            "startingPlayerIndex": 0,
            "revealAll": False,
            "players": players,
        },
        "seats": seats,
    }
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
