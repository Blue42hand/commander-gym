"""Automated two-Luna game-server debug run.

Starts from an already-running local Argentum game server with the Commander Gym provider
loaded, creates a two-AI fixed-deck tournament through Argentum's dev endpoint, waits for one
game to finish, then summarizes policy provenance for communication failures and avoidable
strategic wakes.

This is intentionally a game-server test harness, not a second game runner: Argentum owns
lifecycle, legality, state and results. Commander Gym only creates the test workload and audits
its own policy boundary.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import time
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .pilot_routing import (
    CertifiedNativeDecisionHandler,
    ForcedParameterlessChoiceHandler,
    NoChoiceCombatHandler,
)


ERROR_RE = re.compile(
    r"policy callback failed|AI failed to process server message|pilot_failure|"
    r"JsonDecodingException|OpenAIResponsesPilotError|"
    r"External AI action failed|Error handling AI action",
    re.IGNORECASE,
)


def _request_json(url: str, *, payload: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
    data = None if payload is None else json.dumps(payload).encode()
    request = Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method="POST" if data is not None else "GET",
    )
    try:
        with urlopen(request, timeout=10) as response:
            body = json.loads(response.read())
    except HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"cannot reach {url}: {exc}") from exc
    if not isinstance(body, Mapping):
        raise RuntimeError(f"{url} returned non-object JSON")
    return body


def _load_deck(path: Path) -> dict[str, int]:
    raw = json.loads(path.read_text())
    if isinstance(raw, Mapping) and isinstance(raw.get("cards"), Mapping):
        raw = raw["cards"]
    if not isinstance(raw, Mapping):
        raise ValueError(f"deck file must contain a card-name map: {path}")
    deck: dict[str, int] = {}
    for name, count in raw.items():
        if not isinstance(name, str) or type(count) is not int or count <= 0:
            raise ValueError(f"invalid deck entry in {path}: {name!r} -> {count!r}")
        deck[name] = count
    return deck


def _read_provenance(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if isinstance(item, dict):
            records.append(item)
    return records


def _metadata(record: Mapping[str, Any]) -> Mapping[str, Any]:
    choice = record.get("choice")
    if not isinstance(choice, Mapping):
        return {}
    metadata = choice.get("metadata")
    return metadata if isinstance(metadata, Mapping) else {}


def _avoidable_strategic_wakes(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    handlers = (
        ForcedParameterlessChoiceHandler(),
        CertifiedNativeDecisionHandler(),
        NoChoiceCombatHandler(),
    )
    findings: list[dict[str, Any]] = []
    for record in records:
        metadata = _metadata(record)
        routing = metadata.get("routing")
        if not isinstance(routing, Mapping) or routing.get("path") != "strategic":
            continue
        observation = record.get("observation")
        if not isinstance(observation, Mapping):
            continue
        for handler in handlers:
            try:
                would_handle = handler.choose(observation)
            except Exception:
                would_handle = None
            if would_handle is not None:
                findings.append(
                    {
                        "callback": record.get("callback"),
                        "playerId": record.get("playerId"),
                        "handler": handler.name,
                    }
                )
                break
    return findings


def _skill_review_flags(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Conservative flags for choices worth inspecting, not automatic claims of bad play."""

    flags: list[dict[str, Any]] = []
    for record in records:
        if record.get("callback") != "chooseAction":
            continue
        choice = record.get("choice")
        observation = record.get("observation")
        if not isinstance(choice, Mapping) or not isinstance(observation, Mapping):
            continue
        if choice.get("channel") != "action":
            continue
        action_id = choice.get("actionId")
        legal = observation.get("legalActions")
        if type(action_id) is not int or not isinstance(legal, list):
            continue
        selected = next(
            (
                action
                for action in legal
                if isinstance(action, Mapping) and action.get("actionId") == action_id
            ),
            None,
        )
        if not isinstance(selected, Mapping):
            continue

        # Manually choosing a mana ability while a substantive non-mana action exists is usually
        # redundant on Argentum because cast/activation payment can use the engine mana solver.
        if selected.get("isManaAbility") is True:
            substantive = [
                action
                for action in legal
                if isinstance(action, Mapping)
                and action.get("isManaAbility") is not True
                and action.get("kind") != "PassPriority"
            ]
            if substantive:
                flags.append(
                    {
                        "kind": "manual_mana_with_substantive_action_available",
                        "playerId": record.get("playerId"),
                        "selected": selected.get("description") or selected.get("kind"),
                    }
                )
    return flags


def _summarize(
    records: list[dict[str, Any]],
    *,
    completed: bool,
    lobby_id: str,
    game_ids: list[str],
    max_turn: int,
    log_path: Path | None,
    wall_time_seconds: float,
) -> dict[str, Any]:
    callbacks = Counter(str(record.get("callback", "unknown")) for record in records)
    routes = Counter()
    provider_calls = 0
    retries = 0
    input_tokens = 0
    output_tokens = 0
    cached_input_tokens = 0
    provider_wall_time_ms = 0.0
    max_provider_wall_time_ms = 0.0

    for record in records:
        metadata = _metadata(record)
        routing = metadata.get("routing")
        if isinstance(routing, Mapping):
            routes[str(routing.get("path", "unknown"))] += 1
        if metadata.get("provider") == "openai":
            provider_calls += 1
            wall_time = metadata.get("providerWallTimeMs")
            if isinstance(wall_time, (int, float)) and not isinstance(wall_time, bool):
                provider_wall_time_ms += float(wall_time)
                max_provider_wall_time_ms = max(max_provider_wall_time_ms, float(wall_time))
            retry = metadata.get("retryCount")
            if type(retry) is int:
                retries += retry
            usage = metadata.get("usage")
            if isinstance(usage, Mapping):
                for key, target in (("input_tokens", "input"), ("output_tokens", "output")):
                    value = usage.get(key)
                    if type(value) is int:
                        if target == "input":
                            input_tokens += value
                        else:
                            output_tokens += value
                details = usage.get("input_tokens_details")
                if isinstance(details, Mapping):
                    cached = details.get("cached_tokens")
                    if type(cached) is int:
                        cached_input_tokens += cached

    communication_errors: list[str] = []
    if log_path is not None and log_path.exists():
        for line in log_path.read_text(errors="replace").splitlines():
            if ERROR_RE.search(line):
                communication_errors.append(line[-1200:])

    avoidable = _avoidable_strategic_wakes(records)
    skill_flags = _skill_review_flags(records)
    seat_ids = {
        str(record.get("playerId"))
        for record in records
        if isinstance(record.get("playerId"), str) and record.get("playerId")
    }
    deck_context_seats = {
        str(record.get("playerId"))
        for record in records
        if isinstance(record.get("playerId"), str)
        and isinstance(record.get("observation"), Mapping)
        and isinstance(record["observation"].get("knownDeck"), Mapping)
    }
    provenance_complete = len(seat_ids) >= 2 and seat_ids <= deck_context_seats
    technical_qualified = (
        completed
        and provenance_complete
        and provider_calls > 0
        and retries == 0
        and not communication_errors
        and not avoidable
        and not skill_flags
    )

    return {
        "result": "qualified" if technical_qualified else "needs-debug",
        "completed": completed,
        "lobbyId": lobby_id,
        "gameSessionIds": game_ids,
        "maxTurnObserved": max_turn,
        "wallTimeSeconds": round(wall_time_seconds, 3),
        "policyCallbacks": len(records),
        "policySeats": sorted(seat_ids),
        "deckContextSeats": sorted(deck_context_seats),
        "provenanceComplete": provenance_complete,
        "callbacks": dict(callbacks),
        "routing": dict(routes),
        "providerCalls": provider_calls,
        "providerRequests": provider_calls + retries,
        "validationRetries": retries,
        "providerWallTimeMs": round(provider_wall_time_ms, 3),
        "maxProviderWallTimeMs": round(max_provider_wall_time_ms, 3),
        "inputTokens": input_tokens,
        "cachedInputTokens": cached_input_tokens,
        "outputTokens": output_tokens,
        "strategicWakesAvoided": routes.get("mechanical", 0),
        "avoidableStrategicWakes": avoidable,
        "communicationErrors": communication_errors[:20],
        "skillReviewFlags": skill_flags,
        "technicalQualified": technical_qualified,
    }


def run(args: argparse.Namespace) -> int:
    run_started = time.monotonic()
    deck_a = _load_deck(Path(args.deck_a))
    deck_b = _load_deck(Path(args.deck_b))
    base = args.server_url.rstrip("/")

    created = _request_json(
        f"{base}/api/dev/ai-tournament",
        payload={"decks": [deck_a, deck_b], "gamesPerMatch": 1},
    )
    lobby_id = created.get("lobbyId")
    if not isinstance(lobby_id, str) or not lobby_id:
        raise RuntimeError(f"AI tournament did not return lobbyId: {created}")

    deadline = time.monotonic() + args.timeout
    game_ids: list[str] = []
    max_turn = 0
    last_signature: tuple[Any, ...] | None = None
    completed = False

    while time.monotonic() < deadline:
        status = _request_json(f"{base}/api/dev/ai-tournament/{lobby_id}")
        live = status.get("liveGames")
        if not isinstance(live, list):
            live = []
        for game in live:
            if not isinstance(game, Mapping):
                continue
            game_id = game.get("gameSessionId")
            if isinstance(game_id, str) and game_id not in game_ids:
                game_ids.append(game_id)
            turn = game.get("turnNumber")
            if type(turn) is int:
                max_turn = max(max_turn, turn)
        signature = (
            status.get("state"),
            status.get("round"),
            tuple(
                (game.get("gameSessionId"), game.get("turnNumber"))
                for game in live
                if isinstance(game, Mapping)
            ),
        )
        if signature != last_signature:
            print("TWO_LUNA_STATUS=" + json.dumps(dict(status), sort_keys=True), flush=True)
            last_signature = signature
        if status.get("complete") is True:
            completed = True
            break
        time.sleep(args.poll_seconds)

    # Give sidecar provenance writes a moment to flush after terminal state.
    time.sleep(0.25)
    records = _read_provenance(Path(args.provenance) if args.provenance else None)
    result = _summarize(
        records,
        completed=completed,
        lobby_id=lobby_id,
        game_ids=game_ids,
        max_turn=max_turn,
        log_path=Path(args.server_log) if args.server_log else None,
        wall_time_seconds=time.monotonic() - run_started,
    )
    print("TWO_LUNA_DEBUG_RESULT=" + json.dumps(result, sort_keys=True), flush=True)
    return 0 if result["technicalQualified"] else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-url", default="http://127.0.0.1:8080")
    parser.add_argument("--deck-a", required=True)
    parser.add_argument("--deck-b", required=True)
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--provenance")
    parser.add_argument("--server-log")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
