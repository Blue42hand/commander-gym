"""Bounded old/compact same-state screen. Dry-run unless --execute is supplied.

This does not advance a game. It stops on the first invalid choice or semantic
divergence and requires independent tactical review before any further screen.
The trace, ledger and output are private local artifacts supplied by the caller.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import sys
import tempfile
from time import monotonic
from typing import Any

from commander_gym.openai_responses_pilot import OpenAIResponsesPilot, OpenAIResponsesPilotError
from commander_gym.openai_run_budget import OpenAIRunBudget
from commander_gym.pilot import ArgentumActionChoice, validate_pilot_choice


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


def _semantic_choice(signature: dict[str, Any]) -> dict[str, Any]:
    """Compare native choices and lease commands, excluding explanatory prose."""
    comparable = deepcopy(signature)
    for field in ("priorityDelegation", "thenCast"):
        intent = comparable.get(field)
        if isinstance(intent, dict):
            intent.pop("reason", None)
    return comparable


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


def _screen_worker(
    sender: Any, ledger_path: Path, observation: dict[str, Any],
    compact: bool, deadline: float, model_io_path: Path,
) -> None:
    """One process per attempt permits a hard wall deadline during SDK I/O."""
    model_io = None
    try:
        from openai import OpenAI

        budget = OpenAIRunBudget(
            ledger_path, 6, authorized_max_usd=6, max_requests=610,
        )
        client = _TimedClient(OpenAI(max_retries=0), deadline)
        choice = _pilot(client, budget, compact).choose(observation)
        model_io = choice.metadata.get("modelIo")
        validate_pilot_choice(choice, observation)
        attempt = model_io["attempts"][0]
        _save_model_io(model_io_path, model_io)
        sender.send({
            "choice": _choice_signature(choice, observation),
            "usage": attempt["response"].get("usage"),
            "providerWallMs": attempt["response"].get("providerWallTimeMs"),
            "modelIoArtifact": str(model_io_path),
            "error": None,
        })
    except Exception as exc:
        model_io = getattr(exc, "model_io", None) or model_io
        artifact = None
        if model_io is not None:
            _save_model_io(model_io_path, model_io)
            artifact = str(model_io_path)
        sender.send({
            "choice": None, "usage": None, "providerWallMs": None,
            "modelIoArtifact": artifact,
            "error": f"{type(exc).__name__}: {exc}",
        })
    finally:
        sender.close()


def _save_model_io(path: Path, model_io: Any) -> None:
    """Preserve full private request/response evidence before reporting a result."""
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=path.name + ".tmp-", delete=False,
        ) as target:
            temporary = target.name
            json.dump(model_io, target, sort_keys=True)
            target.flush()
            os.fsync(target.fileno())
        if path.exists():
            raise FileExistsError(f"model I/O artifact already exists: {path}")
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


def _run_with_watchdog(
    worker: Any, worker_args: tuple[Any, ...], deadline: float,
) -> dict[str, Any]:
    receiver, sender = multiprocessing.get_context("spawn").Pipe(duplex=False)
    process = multiprocessing.get_context("spawn").Process(
        target=worker, args=(sender, *worker_args), daemon=True,
    )
    try:
        process.start()
        sender.close()
        if not receiver.poll(max(0.0, deadline - monotonic())):
            process.terminate()
            process.join(2.0)
            if process.is_alive():
                process.kill()
                process.join()
            raise TimeoutError("paired screen hard wall deadline reached; attempt may be billed")
        try:
            result = receiver.recv()
        except EOFError as exc:
            raise RuntimeError("screen worker exited without a result; attempt may be billed") from exc
        process.join(max(0.0, deadline - monotonic()))
        if process.is_alive():
            process.terminate()
            process.join(2.0)
            if process.is_alive():
                process.kill()
                process.join()
            raise TimeoutError("paired screen hard wall deadline reached after worker result")
        return result
    finally:
        receiver.close()
        sender.close()


def _append_result(path: Path, result: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as target:
        target.write(json.dumps(result, sort_keys=True) + "\n")
        target.flush()
        os.fsync(target.fileno())


def _verify_trace(path: Path, expected_name: str, expected_sha256: str) -> None:
    if path.name != Path(expected_name).name:
        raise ValueError("trace does not match the reviewed source")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected_sha256:
        raise ValueError("trace content does not match the reviewed source")


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
        or manifest["sourceSha256"] != "f77cb63314714b13100e7ef3aa64cfc86edf4c37cbc030fde5fde0592952e184"
    ):
        raise ValueError("paired screen manifest violates its reviewed limits")
    if not all(path.is_absolute() for path in (args.trace, args.ledger, args.output)):
        raise ValueError("trace, ledger and output must be absolute paths")
    if not args.output.parent.is_dir():
        raise ValueError("private output directory must already exist")
    _verify_trace(args.trace, manifest["sourceTrace"], manifest["sourceSha256"])
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
    import openai  # check the dependency before raising the ledger cap

    output_fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(output_fd)

    deadline = monotonic() + manifest["wallSeconds"]
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
    attempts = 0
    for index, record, observation in rows:
        pair = []
        for compact in (False, True):
            if monotonic() >= deadline or attempts >= manifest["maxNewRequests"]:
                print("Stopped at wall or request limit; inspect private output", file=sys.stderr)
                return 2
            before = budget.snapshot()
            started = monotonic()
            model_io_path = args.output.with_name(
                f"{args.output.stem}.record-{index}.{'compact' if compact else 'old'}.model-io.json"
            )
            if model_io_path.exists():
                raise ValueError("model I/O artifact already exists; refusing silent resume")
            try:
                worker_result = _run_with_watchdog(
                    _screen_worker,
                    (args.ledger, observation, compact, deadline, model_io_path),
                    deadline,
                )
            except Exception as exc:
                worker_result = {
                    "choice": None, "usage": None, "providerWallMs": None,
                    "modelIoArtifact": str(model_io_path) if model_io_path.exists() else None,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            after = budget.snapshot()
            attempts += after["requests"] - before["requests"]
            result = {
                "recordIndex": index, "view": "compact" if compact else "old",
                "valid": worker_result["error"] is None,
                "error": worker_result["error"], "choice": worker_result["choice"],
                "semanticChoice": (
                    _semantic_choice(worker_result["choice"])
                    if worker_result["choice"] is not None else None
                ),
                "usage": worker_result["usage"],
                "providerWallMs": worker_result["providerWallMs"],
                "modelIoArtifact": worker_result["modelIoArtifact"],
                "harnessWallMs": round((monotonic() - started) * 1000, 3),
                "ledgerRequestsBefore": before["requests"],
                "ledgerRequestsAfter": after["requests"],
                "ledgerEstimatedUsdBefore": before["estimatedUsd"],
                "ledgerEstimatedUsdAfter": after["estimatedUsd"],
            }
            _append_result(args.output, result)
            if worker_result["error"] is not None or after["requests"] != before["requests"] + 1:
                print(f"Stopped on invalid or ambiguous record {index}; inspect private output", file=sys.stderr)
                return 2
            pair.append(_semantic_choice(worker_result["choice"]))
        if pair[0] != pair[1]:
            print(f"Stopped on semantic divergence at record {index}; tactical review required", file=sys.stderr)
            return 2
    print(json.dumps({"completedPairs": len(rows), "newRequests": attempts,
                      "estimatedCumulativeUsd": budget.snapshot()["estimatedUsd"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
