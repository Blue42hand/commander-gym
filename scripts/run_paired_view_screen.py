"""Bounded old/compact same-state screen. Dry-run unless --execute is supplied.

This does not advance a game. It stops on the first invalid choice or semantic
divergence and requires independent tactical review before any further screen.
The trace, ledger and output are private local artifacts supplied by the caller.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
from time import monotonic
from typing import Any

from commander_gym.openai_responses_pilot import OpenAIResponsesPilot, OpenAIResponsesPilotError
from commander_gym.openai_run_budget import OpenAIRunBudget
from commander_gym.pilot import ArgentumActionChoice, choose_for_observation


class _CaptureResponses:
    def __init__(self) -> None:
        self.request: dict[str, Any] | None = None

    def create(self, **request: Any) -> None:
        self.request = request
        raise RuntimeError("offline request capture")


class _CaptureClient:
    def __init__(self) -> None:
        self.responses = _CaptureResponses()


class _CaptureBudget:
    MAX_OUTPUT_TOKENS = 2048

    def create(self, create: Any, request: dict[str, Any]) -> None:
        create(**request)


def _pilot(client: Any, budget: Any, compact: bool) -> OpenAIResponsesPilot:
    return OpenAIResponsesPilot(
        client=client, model="gpt-6-luna", max_attempts=1, budget=budget,
        allow_priority_delegation=True, allow_named_deferrals=True,
        require_nonempty_named_deferrals=True,
        compact_model_observation=compact,
    )


def _captured_request(observation: dict[str, Any], compact: bool) -> dict[str, Any]:
    client = _CaptureClient()
    try:
        _pilot(client, _CaptureBudget(), compact).choose(observation)
    except OpenAIResponsesPilotError as exc:
        if not isinstance(exc.__cause__, RuntimeError) or str(exc.__cause__) != "offline request capture":
            raise
    assert client.responses.request is not None
    return client.responses.request


def _selected_record(path: Path, indices: list[int]) -> list[tuple[int, dict[str, Any], dict[str, Any]]]:
    wanted = set(indices)
    selected = []
    with path.open(encoding="utf-8") as source:
        for index, line in enumerate(source):
            if index not in wanted:
                continue
            record = json.loads(line)
            raw = record["observation"]
            prior = record["choice"]["metadata"]["modelIo"]["attempts"][0]["request"]
            model_observation = json.loads(prior["input"].split("\n", 1)[1])
            observation = deepcopy(raw)
            observation["deckKnowledge"] = model_observation["deckKnowledge"]
            if _captured_request(observation, False) != prior:
                raise ValueError(f"old-view request at record {index} no longer reproduces exactly")
            compact = _captured_request(observation, True)
            if compact["model"] != "gpt-6-luna" or compact["max_output_tokens"] != 2048:
                raise ValueError(f"unbounded or wrong model request at record {index}")
            selected.append((index, record, observation))
    if len(selected) != len(indices):
        raise ValueError("trace is missing selected records")
    seats: dict[str, int] = {}
    for _, record, _ in selected:
        player = record.get("playerId")
        if not isinstance(player, str):
            raise ValueError("selected record has no player identity")
        seats[player] = seats.get(player, 0) + 1
    if len(seats) != 2 or sorted(seats.values()) != [6, 6]:
        raise ValueError("screen must include six decisions per seat")
    by_index = {index: (index, row, observation) for index, row, observation in selected}
    return [by_index[index] for index in indices]


def _choice_signature(choice: Any, observation: dict[str, Any]) -> dict[str, Any]:
    if isinstance(choice, ArgentumActionChoice):
        selected = next(
            action for action in observation["legalActions"]
            if action["actionId"] == choice.action_id
        )
        signature = {
            "channel": "action", "semanticId": selected["semanticId"],
            "params": dict(choice.params),
        }
    else:
        response = dict(choice.response)
        response.pop("decisionId", None)
        signature = {"channel": "decision", "response": response}
    for field in ("priorityDelegation", "thenCast"):
        if field in choice.metadata:
            signature[field] = choice.metadata[field]
    return signature


class _TimedResponses:
    def __init__(self, sdk: Any, deadline: float) -> None:
        self.sdk = sdk
        self.deadline = deadline

    def create(self, **request: Any) -> Any:
        remaining = self.deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError("paired screen wall limit reached")
        return self.sdk.with_options(timeout=remaining, max_retries=0).responses.create(**request)


class _TimedClient:
    def __init__(self, sdk: Any, deadline: float) -> None:
        self.responses = _TimedResponses(sdk, deadline)


def _append_result(path: Path, result: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as target:
        target.write(json.dumps(result, sort_keys=True) + "\n")
        target.flush()
        os.fsync(target.fileno())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("paired_view_screen_manifest.json"))
    parser.add_argument("--execute", action="store_true", help="permit paid calls after independent review")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if (
        manifest["schemaVersion"] != 1 or manifest["maxNewRequests"] != 24
        or manifest["wallSeconds"] != 360 or manifest["model"] != "gpt-6-luna"
        or manifest["maxOutputTokens"] != 2048
        or manifest["expectedLedgerRequests"] != 586
        or manifest["absoluteLedgerRequestLimit"] != 610
        or manifest["startingCapUsd"] != 5
        or manifest["authorizedCapUsd"] != 6
        or manifest["traceIndices"] != [2, 13, 31, 76, 114, 192, 7, 20, 44, 65, 93, 174]
    ):
        raise ValueError("paired screen manifest violates its reviewed limits")
    if not all(path.is_absolute() for path in (args.trace, args.ledger, args.output)):
        raise ValueError("trace, ledger and output must be absolute paths")
    if not args.output.parent.is_dir():
        raise ValueError("private output directory must already exist")
    if args.trace.name != Path(manifest["sourceTrace"]).name:
        raise ValueError("trace does not match the reviewed source")
    if args.output.exists():
        raise ValueError("output already exists; a screen cannot silently resume")
    rows = _selected_record(args.trace, manifest["traceIndices"])
    if not args.execute:
        print(json.dumps({"dryRun": True, "oldRequestsReproduced": len(rows),
                          "plannedAttempts": 2 * len(rows), "maxWallSeconds": 360,
                          "maximumLedgerRequests": manifest["absoluteLedgerRequestLimit"]}))
        return 0
    if not args.ledger.is_file() or not args.ledger.read_bytes():
        raise ValueError("existing nonempty cumulative ledger required")
    if not os.environ.get("OPENAI_API_KEY"):
        raise ValueError("existing OpenAI credential is unavailable")
    from openai import OpenAI  # import only on the explicit paid path

    output_fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(output_fd)

    original = OpenAIRunBudget(
        args.ledger, manifest["startingCapUsd"],
        authorized_max_usd=manifest["authorizedCapUsd"],
    )
    snapshot = original.snapshot()
    if snapshot["requests"] != manifest["expectedLedgerRequests"] or "maxRequests" in snapshot:
        raise ValueError("cumulative ledger does not match the reviewed starting point")
    original.set_request_limit(manifest["absoluteLedgerRequestLimit"])
    original.increase_cap(manifest["authorizedCapUsd"])
    budget = OpenAIRunBudget(
        args.ledger, manifest["authorizedCapUsd"],
        authorized_max_usd=manifest["authorizedCapUsd"],
        max_requests=manifest["absoluteLedgerRequestLimit"],
    )
    deadline = monotonic() + manifest["wallSeconds"]
    client = _TimedClient(OpenAI(max_retries=0), deadline)
    attempts = 0
    for index, record, observation in rows:
        pair = []
        for compact in (False, True):
            if monotonic() >= deadline or attempts >= manifest["maxNewRequests"]:
                print("Stopped at wall or request limit; inspect private output", file=sys.stderr)
                return 2
            before = budget.snapshot()
            started = monotonic()
            try:
                choice = choose_for_observation(_pilot(client, budget, compact), observation)
                signature = _choice_signature(choice, observation)
                attempt = choice.metadata["modelIo"]["attempts"][0]
                usage = attempt["response"].get("usage")
                wall_ms = attempt["response"].get("providerWallTimeMs")
                error = None
            except Exception as exc:
                signature, usage, wall_ms = None, None, None
                error = f"{type(exc).__name__}: {exc}"
            after = budget.snapshot()
            attempts += after["requests"] - before["requests"]
            result = {
                "recordIndex": index, "view": "compact" if compact else "old",
                "valid": error is None, "error": error, "choice": signature,
                "usage": usage, "providerWallMs": wall_ms,
                "harnessWallMs": round((monotonic() - started) * 1000, 3),
                "ledgerRequestsBefore": before["requests"],
                "ledgerRequestsAfter": after["requests"],
                "ledgerEstimatedUsdBefore": before["estimatedUsd"],
                "ledgerEstimatedUsdAfter": after["estimatedUsd"],
            }
            _append_result(args.output, result)
            if error is not None or after["requests"] != before["requests"] + 1:
                print(f"Stopped on invalid or ambiguous record {index}; inspect private output", file=sys.stderr)
                return 2
            pair.append(signature)
        if pair[0] != pair[1]:
            print(f"Stopped on semantic divergence at record {index}; tactical review required", file=sys.stderr)
            return 2
    print(json.dumps({"completedPairs": len(rows), "newRequests": attempts,
                      "estimatedCumulativeUsd": budget.snapshot()["estimatedUsd"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
