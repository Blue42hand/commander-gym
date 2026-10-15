#!/usr/bin/env python3
"""Bounded local-only synthetic native-action smoke; never starts a game.

Run from the repository root with PYTHONPATH=. and an explicit private config:
  python scripts/smoke_lmstudio.py --config /path/to/local.json
The fixture endpoint/model are placeholders, not deployment defaults. Context
admission assumes a qualified byte-token tokenizer and template fitting the
configured reserve. No schema downgrade, model loading or cloud fallback occurs.
"""

import argparse
import json

from commander_gym.lmstudio_pilot import load_lmstudio_pilot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    pilot = load_lmstudio_pilot(args.config)
    observation = {
        "type": "Game", "schemaHash": "synthetic-compatibility-only",
        "stateDigest": "synthetic", "perspectivePlayerId": "synthetic-seat",
        "agentToAct": "synthetic-seat", "terminated": False, "pendingDecision": None,
        "legalActions": [{"actionId": 7, "semanticId": "synthetic-pass",
                          "kind": "PassPriority", "parameterSpec": {"allowedFields": {}}}],
    }
    choice = pilot.choose(observation)
    if choice.action_id != 7 or choice.params != {}:
        raise RuntimeError("synthetic native choice mismatch")
    print(json.dumps({"qualification": "synthetic_local_inference_only",
                      "actionId": choice.action_id, "metadata": choice.metadata}, indent=2))


if __name__ == "__main__":
    main()
