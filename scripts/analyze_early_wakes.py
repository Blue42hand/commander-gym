"""Offline, aggregate replay of the opt-in native no-choice route.

Reads private policy JSONL locally and prints counts only. A replay match is an
opportunity estimate, never evidence that a changed game would follow the same path.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path

from commander_gym.pilot_routing import NativeNoChoiceHandler


def analyze(paths: list[Path], through_turn: int) -> dict:
    handler = NativeNoChoiceHandler()
    games = []
    for path in paths:
        turns: dict[int, dict[str, int]] = defaultdict(
            lambda: {"callbacks": 0, "eligible": 0, "matched": 0, "diverged": 0}
        )
        with path.open(encoding="utf-8") as source:
            for line in source:
                record = json.loads(line)
                observation = record.get("observation")
                if not isinstance(observation, dict):
                    continue
                turn = observation.get("state", {}).get("turnNumber")
                if type(turn) is not int or not 1 <= turn <= through_turn:
                    continue
                row = turns[turn]
                row["callbacks"] += 1
                candidate = handler.choose(observation)
                if candidate is None:
                    continue
                row["eligible"] += 1
                key = "matched" if record.get("choice", {}).get("actionId") == candidate.action_id else "diverged"
                row[key] += 1
        games.append({
            "turns": {str(turn): row for turn, row in sorted(turns.items())},
            "total": {
                key: sum(row[key] for row in turns.values())
                for key in ("callbacks", "eligible", "matched", "diverged")
            },
        })
    return {
        "route": f"{handler.name}/v{handler.version}",
        "throughTurn": through_turn,
        "games": games,
        "interpretation": "matched counts are counterfactual wake opportunities, not observed API savings",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("policy_jsonl", nargs="+", type=Path)
    parser.add_argument("--through-turn", type=int, default=12)
    args = parser.parse_args()
    if args.through_turn < 1:
        parser.error("--through-turn must be positive")
    print(json.dumps(analyze(args.policy_jsonl, args.through_turn), indent=2))


if __name__ == "__main__":
    main()
