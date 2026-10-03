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
from commander_gym.delegated_autopass import DelegatedAutopassPilot
from commander_gym.pilot import ArgentumActionChoice


class _RecordedStrategicPilot:
    """Hypothetically approve a bounded wait on each recorded strategic pass."""

    name = "recorded-strategic-pass"
    version = "1"

    def __init__(self) -> None:
        self.record: dict | None = None

    def choose(self, observation: dict) -> ArgentumActionChoice:
        assert self.record is not None
        action_id = self.record.get("choice", {}).get("actionId")
        selected = next((
            action for action in observation.get("legalActions", [])
            if action.get("actionId") == action_id
        ), None)
        metadata = {}
        if selected and selected.get("kind") == "PassPriority":
            metadata["priorityDelegation"] = {
                "until": "next_own_main", "reason": "Hypothetical recorded-pass approval",
            }
        return ArgentumActionChoice(action_id=action_id, metadata=metadata)


def analyze(paths: list[Path], through_turn: int) -> dict:
    handler = NativeNoChoiceHandler()
    games = []
    for path in paths:
        replay = _RecordedStrategicPilot()
        delegation = DelegatedAutopassPilot(replay)
        delegated = matched_delegated = rejected_delegation = 0
        turns: dict[int, dict[str, int]] = defaultdict(
            lambda: {"callbacks": 0, "eligible": 0, "matched": 0, "diverged": 0}
        )
        observed = defaultdict(lambda: defaultdict(int))
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
                choice = record.get("choice")
                metadata = choice.get("metadata") if isinstance(choice, dict) else None
                if isinstance(metadata, dict):
                    route = metadata.get("routing")
                    handled_by = route.get("handledBy") if isinstance(route, dict) else None
                    implementation = handled_by.get("implementation") if isinstance(handled_by, dict) else None
                    if implementation == "native-no-choice":
                        observed[turn]["nativeNoChoice"] += 1
                    model_io = metadata.get("modelIo")
                    if isinstance(model_io, dict) and isinstance(model_io.get("attempts"), list):
                        observed[turn]["modelAttempts"] += len(model_io["attempts"])
                    if isinstance(metadata.get("priorityDelegation"), dict):
                        observed[turn]["leaseRequests"] += 1
                    if metadata.get("priorityDelegationRejected") is not None:
                        observed[turn]["leaseRejected"] += 1
                    if isinstance(metadata.get("delegatedPass"), dict):
                        observed[turn]["delegatedPasses"] += 1
                legal = observation.get("legalActions")
                if isinstance(choice, dict) and isinstance(legal, list):
                    selected = next((
                        action for action in legal if isinstance(action, dict)
                        and action.get("actionId") == choice.get("actionId")
                    ), None)
                    if isinstance(selected, dict) and selected.get("kind") == "PassPriority":
                        observed[turn]["selectedPasses"] += 1
                        if all(
                            action is selected or (
                                isinstance(action, dict)
                                and action.get("kind") == "ActivateAbility"
                                and action.get("isManaAbility") is True
                            ) for action in legal
                        ):
                            observed[turn]["passPlusManaOnly"] += 1
                        else:
                            observed[turn]["passWithNonmanaChoice"] += 1
                if type(record.get("choice", {}).get("actionId")) is int:
                    replay.record = record
                    replayed = delegation.choose(observation)
                    if "priorityDelegationRejected" in replayed.metadata:
                        rejected_delegation += 1
                    if "delegatedPass" in replayed.metadata:
                        delegated += 1
                        matched_delegated += int(
                            replayed.action_id == record["choice"]["actionId"]
                        )
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
            "hypotheticalDelegation": {
                "delegatedPasses": delegated,
                "matchedRecordedChoice": matched_delegated,
                "rejectedProposals": rejected_delegation,
            },
            "observed": {
                "turns": {str(turn): dict(observed[turn]) for turn in sorted(turns)},
                "total": {
                    key: sum(observed[turn][key] for turn in turns)
                    for key in (
                        "modelAttempts", "nativeNoChoice", "leaseRequests",
                        "leaseRejected", "delegatedPasses", "selectedPasses",
                        "passPlusManaOnly", "passWithNonmanaChoice",
                    )
                },
            },
        })
    return {
        "route": f"{handler.name}/v{handler.version}",
        "throughTurn": through_turn,
        "games": games,
        "interpretation": (
            "observed counts are from recorded policy provenance; eligible/matched/diverged "
            "and hypotheticalDelegation are counterfactual opportunities, not measured savings "
            "from a changed game; hypothetical delegation assumes approval at each recorded pass"
        ),
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
