#!/usr/bin/env python3
"""Bounded, private saved-position cache probe. No game server is started.

Review the exact source head, SDK wire preflight, and shared ledger before --execute.
The manifest and journal make restart conservative: an ambiguous request is never
replayed and the initial absolute spending ceilings are never recalculated.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
from time import perf_counter
from types import SimpleNamespace
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from commander_gym.cache_friendly_input import (
    cache_friendly_observation_input, reconstruct_cache_friendly_observation,
)
from commander_gym.cache_probe_session import (
    ProbeSession, ProbeSessionError, TRIALS, MAX_RETRIES, _read_jsonl,
)
from commander_gym.openai_responses_pilot import OpenAIResponsesPilot, OpenAIResponsesPilotError
from commander_gym.openai_run_budget import OpenAIRunBudget, OpenAIRunBudgetError
from commander_gym.pilot import ArgentumActionChoice


def _read_key(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.strip().removeprefix("export ")
        if value.startswith("OPENAI_API_KEY="):
            key = value.split("=", 1)[1].strip().strip("\"'")
            if key:
                return key
    raise ProbeSessionError("existing key file has no OPENAI_API_KEY entry")


def _saved_rows(trace: Path) -> list[dict[str, Any]]:
    rows = []
    for line in trace.open(encoding="utf-8"):
        row = json.loads(line)
        attempts = (((row.get("choice") or {}).get("metadata") or {}).get("modelIo") or {}).get("attempts") or []
        if attempts:
            rows.append({"observation": row["observation"],
                         "request": attempts[0]["request"]})
    if len(rows) <= max(position for position, _variant in TRIALS):
        raise ProbeSessionError("saved trace does not contain every selected provider row")
    return rows


def _request(row: Mapping[str, Any], variant: str, correction: str | None = None) -> dict[str, Any]:
    request = dict(row["request"])
    if request.get("model") != "gpt-6-luna" or request.get("store") is not False:
        raise ProbeSessionError("saved model or storage setting differs from qualification")
    if request.get("max_output_tokens") != OpenAIRunBudget.MAX_OUTPUT_TOKENS:
        raise ProbeSessionError("saved output cap differs from qualification")
    if variant == "cache":
        view = json.loads(request["input"].split("\n", 1)[1])
        request["input"] = cache_friendly_observation_input(view)
        if reconstruct_cache_friendly_observation(request["input"]) != view:
            raise ProbeSessionError("cache layout lost seat-masked information")
        request["prompt_cache_options"] = {"mode": "explicit"}
    elif variant != "baseline":
        raise ProbeSessionError("unknown probe variant")
    if correction is not None:
        # The bound is frozen with 2,048 extra bytes per position. The native
        # reason is capped before it can be appended to a retry request.
        message = ("The previous response was invalid: " + correction[:1024]
                   + "\nReturn a corrected JSON object using only the current observation.")
        if variant == "cache":
            request["input"] = [*request["input"], {"role": "user", "content": message}]
        else:
            request["input"] += "\n" + message
    return request


def _sdk_wire_check(request: Mapping[str, Any]) -> str:
    """Use a mock transport in the actual venv; no provider connection or real key."""
    import openai
    from openai.resources.responses.responses import Responses
    from openai.types.responses.response_usage import InputTokensDetails
    try:
        import httpx2 as http_transport
    except ImportError:
        import httpx as http_transport
    if "prompt_cache_options" not in inspect.signature(Responses.create).parameters:
        raise ProbeSessionError("installed SDK lacks prompt_cache_options")
    if not {"cached_tokens", "cache_write_tokens"}.issubset(InputTokensDetails.model_fields):
        raise ProbeSessionError("installed SDK lacks cache usage fields")
    captured = {}
    def handler(http_request):
        captured.update(json.loads(http_request.content))
        return http_transport.Response(400, json={
            "error": {"message": "offline mock", "type": "invalid_request_error"},
        })
    client = openai.OpenAI(
        api_key="sk-test-offline", max_retries=0,
        http_client=http_transport.Client(transport=http_transport.MockTransport(handler)),
    )
    try:
        client.responses.create(**request)
    except openai.BadRequestError:
        pass
    blocks = [block for message in captured.get("input", [])
              for block in message.get("content", []) if isinstance(block, dict)]
    if (captured.get("prompt_cache_options") != {"mode": "explicit"}
        or captured.get("store") is not False
        or sum("prompt_cache_breakpoint" in block for block in blocks) < 3):
        raise ProbeSessionError("SDK did not serialize cache fields or store:false")
    return importlib.metadata.version("openai")


def _usage_details(usage: Any) -> dict[str, int | None]:
    value = usage if isinstance(usage, Mapping) else vars(usage) if usage is not None else {}
    details = value.get("input_tokens_details")
    details = details if isinstance(details, Mapping) else vars(details) if details is not None else {}
    return {key: details.get(key) for key in ("cached_tokens", "cache_write_tokens")}


def _choice_summary(choice: Any) -> dict[str, Any]:
    if isinstance(choice, ArgentumActionChoice):
        return {"channel": "action", "actionId": choice.action_id,
                "params": dict(choice.params)}
    return {"channel": "decision", "response": dict(choice.response)}


def _dispatch(session: ProbeSession, budget: OpenAIRunBudget, client: Any,
              row: Mapping[str, Any], trial_index: int, variant: str,
              correction: str | None) -> None:
    session.verify(budget.snapshot())
    retry = correction is not None
    request = _request(row, variant, correction)
    ordinal, reserved = session.begin(trial_index, request, retry=retry)
    started = perf_counter()
    response = None
    try:
        response = budget.create(client.responses.create, request)
    except OpenAIRunBudgetError as error:
        if not error.dispatched:
            # Intent was fsynced before dispatch. Preserve it unresolved so a
            # restart cannot mistake this window for permission to retry.
            raise ProbeSessionError("reservation rejected; unresolved intent requires review") from error
        response = error.response
        if response is not None:
            session.record_response(ordinal, request, response, result_kind="budget_error")
        session.finish(ordinal, usage=getattr(response, "usage", None),
                       reserved=reserved, valid=False, result_kind="budget_error",
                       diagnostics={"error": str(error)[:256],
                                    "wallMs": round((perf_counter() - started) * 1000, 3)})
        session.verify(budget.snapshot())
        raise ProbeSessionError("dispatched provider attempt failed budget settlement") from error
    except Exception as error:
        # The ledger reserved this dispatched attempt. Preserve the reservation
        # and stop; its outcome may be ambiguous.
        session.finish(ordinal, usage=None, reserved=reserved,
                       valid=False, result_kind="transport_error",
                       diagnostics={"errorType": type(error).__name__,
                                    "wallMs": round((perf_counter() - started) * 1000, 3)})
        session.verify(budget.snapshot())
        raise ProbeSessionError("provider transport outcome is ambiguous") from error
    pilot = OpenAIResponsesPilot(
        client=client, model=request["model"], max_attempts=1,
        allow_priority_delegation=True, allow_named_deferrals=True,
        require_nonempty_named_deferrals=True, compact_model_observation=True,
        guarded_then_cast_templates=True, allow_declarative_continuation=True,
    )
    valid = True
    validation_error = None
    choice_summary = None
    try:
        choice_summary = _choice_summary(pilot._choice_from_response(
            response, row["observation"], retry_count=int(retry),
        ))
    except OpenAIResponsesPilotError as error:
        valid = False
        validation_error = str(error)[:1024]
    output = getattr(response, "output_text", "")
    session.record_response(ordinal, request, response, result_kind="provider_response")
    session.finish(ordinal, usage=getattr(response, "usage", None),
                   reserved=reserved, valid=valid, result_kind="provider_response",
                   diagnostics={
                       "wallMs": round((perf_counter() - started) * 1000, 3),
                       "responseId": getattr(response, "id", None),
                       "outputSha256": hashlib.sha256(str(output).encode()).hexdigest(),
                       "cacheUsage": _usage_details(getattr(response, "usage", None)),
                       "choice": choice_summary,
                       "validationError": validation_error,
                   })
    session.verify(budget.snapshot())
    print(json.dumps({"attempt": ordinal, "trial": trial_index,
                      "variant": variant, "retry": retry, "valid": valid}))


def execute_trials(
    session: ProbeSession, budget: OpenAIRunBudget,
    client: Any, rows: list[dict[str, Any]],
) -> None:
    """Resume the frozen trial order, including at most two immediate retries."""
    while True:
        results = _read_jsonl(session.run_dir / "results.jsonl")
        intents = _read_jsonl(session.run_dir / "intents.jsonl")
        session.verify(budget.snapshot())
        if results and (results[-1]["resultKind"] != "provider_response"
                        or results[-1]["unsettledDelta"]):
            raise ProbeSessionError("earlier provider error requires review")
        primary_count = sum(not intent["retry"] for intent in intents)
        if results and not results[-1]["valid"] and not results[-1]["retry"]:
            previous = results[-1]
            if sum(bool(intent["retry"]) for intent in intents) < MAX_RETRIES:
                trial_index = previous["trialIndex"]
                position, variant = TRIALS[trial_index]
                _dispatch(session, budget, client, rows[position], trial_index,
                          variant, previous["diagnostics"]["validationError"] or "invalid output")
                continue
        if primary_count >= len(TRIALS):
            print(json.dumps({"status": "completed", "attempts": len(intents)}))
            return
        position, variant = TRIALS[primary_count]
        _dispatch(session, budget, client, rows[position], primary_count, variant, None)


def run(args: argparse.Namespace) -> None:
    trace = args.trace.resolve(strict=True)
    ledger_path = args.ledger.resolve(strict=True)
    trace_sha = hashlib.sha256(trace.read_bytes()).hexdigest()
    if trace_sha != args.expected_trace_sha256:
        raise ProbeSessionError("saved trace digest differs from the reviewed preflight")
    rows = _saved_rows(trace)
    requests = [_request(rows[position], variant) for position, variant in TRIALS]
    sdk_version = _sdk_wire_check(_request(rows[25], "cache"))
    print(json.dumps({"sdkVersion": sdk_version, "wireProbe": "offline-pass"}))
    source_head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[1],
        text=True,
    ).strip()
    shared = OpenAIRunBudget(ledger_path, 18, authorized_max_usd=18,
                             max_requests=2212)
    with ProbeSession.open(args.run_dir.absolute(), shared,
            trace_sha256=trace_sha, source_head=source_head, requests=requests) as session:
        budget = session.guarded_budget(ledger_path)
        if not args.execute:
            print(json.dumps({"status": "preflight-only", "sourceHead": source_head,
                              "sessionCapUsd": session.manifest["sessionCapUsd"],
                              "sessionMaxRequests": session.manifest["sessionMaxRequests"],
                              "worstReservationUsd": session.manifest["worstTotalReservationUsd"]}))
            return
        key = _read_key(args.api_key_file.resolve(strict=True))
        import openai
        client = openai.OpenAI(api_key=key, max_retries=0)
        del key
        execute_trials(session, budget, client, rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", required=True, type=Path)
    parser.add_argument("--expected-trace-sha256", required=True)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--api-key-file", type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.execute and args.api_key_file is None:
        parser.error("--execute requires an existing --api-key-file")
    try:
        run(args)
    except ProbeSessionError as error:
        print(f"cache probe stopped: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
