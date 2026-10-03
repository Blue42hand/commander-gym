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
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .pilot_routing import ForcedParameterlessChoiceHandler
from .openai_run_budget import OpenAIRunBudget


ERROR_RE = re.compile(
    r"policy callback failed|AI failed to process server message|pilot_failure|"
    r"JsonDecodingException|OpenAIResponsesPilotError|"
    r"External AI action failed|Error handling AI action",
    re.IGNORECASE,
)


def _request_json(
    url: str, *, payload: Mapping[str, Any] | None = None, token: str | None = None,
    timeout: float = 10,
) -> Mapping[str, Any]:
    data = None if payload is None else json.dumps(payload).encode()
    request = Request(
        url,
        data=data,
        headers={
            **({"Content-Type": "application/json"} if data is not None else {}),
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
        method="POST" if data is not None else "GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read())
    except HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"cannot reach {url}: {exc}") from exc
    if not isinstance(body, Mapping):
        raise RuntimeError(f"{url} returned non-object JSON")
    return body


def _write_private_json(path: Path, value: Mapping[str, Any]) -> str:
    """Create one owner-only run artifact without replacing earlier evidence."""

    encoded = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(encoded)
    return hashlib.sha256(encoded).hexdigest()


def _capture_terminal_replay(
    base: str, finished: list[dict[str, Any]], evidence_dir: Path,
) -> dict[str, Any]:
    """Retain the in-memory replay and its native final frame before server shutdown."""

    if (len(finished) != 1 or not isinstance(finished[0], Mapping)
            or not isinstance(finished[0].get("gameSessionId"), str)):
        raise RuntimeError("expected exactly one finished game with a session id")
    if stat.S_IMODE(evidence_dir.stat().st_mode) & 0o077:
        raise RuntimeError("terminal evidence directory must be private to the current user")
    game = finished[0]
    game_id = game["gameSessionId"]
    replay = _request_json(f"{base}/api/public/replays/{game_id}", timeout=120)
    metadata = replay.get("metadata")
    if not isinstance(metadata, Mapping) or metadata.get("gameId") != game_id:
        raise RuntimeError("replay metadata did not identify the finished game")
    frame_count = metadata.get("snapshotCount")
    if type(frame_count) is not int or frame_count < 1:
        raise RuntimeError("replay did not report a positive frame count")
    replay_sha = _write_private_json(evidence_dir / "terminal-replay.json", replay)

    # This existing endpoint returns native GameState, including the final game-over fields.
    # Keep it private: unlike the ordinary spectator replay, it can contain hidden cards.
    final_state = _request_json(
        f"{base}/api/public/replays/{game_id}/frames/{frame_count - 1}/full-state",
        timeout=120,
    )
    if final_state.get("gameOver") is not True:
        raise RuntimeError("last replay frame did not contain native game-over state")
    if type(final_state.get("turnNumber")) is not int:
        raise RuntimeError("last replay frame had no native final turn")
    if game.get("winnerId") != final_state.get("winnerId"):
        raise RuntimeError("last replay frame winner disagreed with tournament result")
    state_sha = _write_private_json(evidence_dir / "terminal-state.json", final_state)
    return {
        "gameSessionId": game_id,
        "replaySha256": replay_sha,
        "terminalStateSha256": state_sha,
        "snapshotCount": frame_count,
        "nativeGameOver": True,
        "finalTurnNumber": final_state["turnNumber"],
        "winnerId": final_state.get("winnerId"),
        "stateReproducible": metadata.get("stateReproducible"),
    }


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
    handlers = (ForcedParameterlessChoiceHandler(),)
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


def _natural_terminal_game(finished: Any) -> dict[str, Any] | None:
    """Require one played native terminal match, not bracket completion alone."""

    if not isinstance(finished, list) or len(finished) != 1:
        return None
    game = finished[0]
    if not isinstance(game, Mapping):
        return None
    if not isinstance(game.get("gameSessionId"), str) or not game["gameSessionId"]:
        return None
    if game.get("nativeGameOver") is not True or game.get("isSimulated") is not False:
        return None
    if not (isinstance(game.get("winnerId"), str) or game.get("isDraw") is True):
        return None
    return dict(game)


@dataclass
class _ProgressGuard:
    """Observe native progress and request reservations, not just turn changes."""

    stall_seconds: float
    last_change: float
    signature: tuple[Any, ...] | None = None

    def observe(self, signature: tuple[Any, ...], now: float) -> bool:
        if signature != self.signature:
            self.signature = signature
            self.last_change = now
            return False
        return now - self.last_change >= self.stall_seconds


def _provenance_size(path: Path | None) -> int | None:
    if path is None:
        return None
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


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
    strategic_by_kind = Counter()
    mechanical_by_handler = Counter()
    provider_calls = 0
    delegated_passes = 0
    retries = 0
    input_tokens = 0
    output_tokens = 0
    cached_input_tokens = 0
    provider_wall_time_ms = 0.0
    max_provider_wall_time_ms = 0.0

    for record in records:
        metadata = _metadata(record)
        if isinstance(metadata.get("delegatedPass"), Mapping):
            delegated_passes += 1
        routing = metadata.get("routing")
        if isinstance(routing, Mapping):
            route_path = str(routing.get("path", "unknown"))
            routes[route_path] += 1
            if route_path == "mechanical":
                mechanical_by_handler[str(routing.get("handler", "unknown"))] += 1
            elif route_path == "strategic":
                callback = str(record.get("callback", "unknown"))
                observation = record.get("observation")
                kind = callback
                if isinstance(observation, Mapping):
                    pending = observation.get("pendingDecision")
                    if isinstance(pending, Mapping):
                        kind = str(pending.get("type") or pending.get("kind") or callback)
                    elif callback == "chooseAction":
                        choice = record.get("choice")
                        legal = observation.get("legalActions")
                        if isinstance(choice, Mapping) and isinstance(legal, list):
                            action_id = choice.get("actionId")
                            selected = next(
                                (
                                    action
                                    for action in legal
                                    if isinstance(action, Mapping)
                                    and action.get("actionId") == action_id
                                ),
                                None,
                            )
                            if isinstance(selected, Mapping):
                                kind = str(
                                    selected.get("kind")
                                    or selected.get("actionType")
                                    or "chooseAction"
                                )
                strategic_by_kind[kind] += 1
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
        "strategicByKind": dict(strategic_by_kind),
        "mechanicalByHandler": dict(mechanical_by_handler),
        "providerCalls": provider_calls,
        "delegatedPasses": delegated_passes,
        "providerRequests": provider_calls + retries,
        "validationRetries": retries,
        "providerWallTimeMs": round(provider_wall_time_ms, 3),
        "maxProviderWallTimeMs": round(max_provider_wall_time_ms, 3),
        "inputTokens": input_tokens,
        "cachedInputTokens": cached_input_tokens,
        "outputTokens": output_tokens,
        "strategicWakesAvoided": routes.get("mechanical", 0) + delegated_passes,
        "avoidableStrategicWakes": avoidable,
        "communicationErrors": communication_errors[:20],
        "skillReviewFlags": skill_flags,
        "technicalQualified": technical_qualified,
    }


def _run_budget(args: argparse.Namespace) -> OpenAIRunBudget:
    return OpenAIRunBudget(
        Path(args.budget_ledger).resolve(), args.budget_cap,
        authorized_max_usd=args.budget_authorized_max,
        max_requests=args.budget_max_requests,
    )


def run(args: argparse.Namespace) -> int:
    run_started = time.monotonic()
    if args.profile_a == args.profile_b:
        raise RuntimeError("two-seat qualification requires distinct Binding profiles")
    token = os.environ.get("COMMANDER_GYM_SIDECAR_TOKEN", "")
    if not token:
        raise RuntimeError("COMMANDER_GYM_SIDECAR_TOKEN is required")
    catalog = _request_json(
        args.sidecar_url.rstrip("/") + "/v1/controller-profiles", token=token,
    )
    profiles = catalog.get("profiles")
    if not isinstance(profiles, list):
        raise RuntimeError("sidecar did not advertise controller profiles")
    selected = []
    for binding_id in (args.profile_a, args.profile_b):
        matches = [p for p in profiles if isinstance(p, Mapping) and p.get("id") == binding_id]
        if len(matches) != 1:
            raise RuntimeError(f"canonical Binding profile {binding_id!r} is unavailable")
        deck = matches[0].get("deck")
        if not isinstance(deck, Mapping) or not isinstance(deck.get("cards"), Mapping):
            raise RuntimeError(f"Binding profile {binding_id!r} has no exact deck")
        if not isinstance(deck.get("commander"), str):
            raise RuntimeError(f"Binding profile {binding_id!r} has no designated commander")
        selected.append(dict(deck["cards"]))
    budget = _run_budget(args)
    before = budget.snapshot()
    base = args.server_url.rstrip("/")

    created = _request_json(
        f"{base}/api/dev/ai-tournament",
        payload={
            "decks": selected,
            "gamesPerMatch": 1,
            "controllerSpecs": [
                {"mode": "commander-gym", "profileId": args.profile_a},
                {"mode": "commander-gym", "profileId": args.profile_b},
            ],
            "rules": "COMMANDER",
        },
    )
    lobby_id = created.get("lobbyId")
    if not isinstance(lobby_id, str) or not lobby_id:
        raise RuntimeError(f"AI tournament did not return lobbyId: {created}")

    deadline = time.monotonic() + args.timeout
    progress_guard = _ProgressGuard(args.stall_seconds, time.monotonic())
    provenance_path = Path(args.provenance) if args.provenance else None
    game_ids: list[str] = []
    max_turn = 0
    last_signature: tuple[Any, ...] | None = None
    completed = False
    terminal_evidence: dict[str, Any] | None = None
    stop_reason = "emergency_ceiling"
    final_status: Mapping[str, Any] = {}

    while time.monotonic() < deadline:
        status = _request_json(f"{base}/api/dev/ai-tournament/{lobby_id}")
        final_status = status
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
        finished = status.get("completedGames")
        if isinstance(finished, list):
            for game in finished:
                if not isinstance(game, Mapping):
                    continue
                game_id = game.get("gameSessionId")
                if isinstance(game_id, str) and game_id not in game_ids:
                    game_ids.append(game_id)
                final_turn = game.get("finalTurnNumber")
                if type(final_turn) is int:
                    max_turn = max(max_turn, final_turn)
            terminal_evidence = _natural_terminal_game(finished)
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
            completed = terminal_evidence is not None
            stop_reason = "native_complete" if completed else "native_complete_without_terminal"
            break
        # A native callback can advance inside one turn without changing the
        # tournament status. Provenance growth captures it; request reservations
        # capture an API attempt before any response or provenance write. Old
        # unsettled reservations never count as live progress.
        progress_signature = (
            signature,
            _provenance_size(provenance_path),
            budget.snapshot()["requests"],
        )
        if progress_guard.observe(progress_signature, time.monotonic()):
            stop_reason = "no_native_or_api_progress"
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
    result["terminalEvidence"] = terminal_evidence
    result["stopReason"] = stop_reason
    if final_status.get("complete") is True and args.terminal_evidence_dir:
        finished = final_status.get("completedGames")
        try:
            result["terminalArtifact"] = _capture_terminal_replay(
                base, finished if isinstance(finished, list) else [],
                Path(args.terminal_evidence_dir),
            )
        except (OSError, RuntimeError, ValueError) as exc:
            result["terminalArtifact"] = {"error": str(exc)}
    after = budget.snapshot()
    result["budget"] = {
        "capUsd": after["capUsd"],
        "gameEstimatedUsd": round(after["estimatedUsd"] - before["estimatedUsd"], 6),
        "cumulativeEstimatedUsd": round(after["estimatedUsd"], 6),
        "remainingUsd": round(after["capUsd"] - after["estimatedUsd"], 6),
        "gameApiRequests": after["requests"] - before["requests"],
        "cumulativeApiRequests": after["requests"],
        "gameInputTokens": after["inputTokens"] - before["inputTokens"],
        "gameOutputTokens": after["outputTokens"] - before["outputTokens"],
        "unsettledRequests": after["unsettledRequests"],
        "model": "gpt-6-luna",
        "basis": "conservative published standard token rates times 2.2; ambiguous attempts retain reservation",
    }
    print("TWO_LUNA_DEBUG_RESULT=" + json.dumps(result, sort_keys=True), flush=True)
    return 0 if result["technicalQualified"] else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-url", default="http://127.0.0.1:8080")
    parser.add_argument("--sidecar-url", required=True)
    parser.add_argument("--profile-a", required=True)
    parser.add_argument("--profile-b", required=True)
    parser.add_argument("--budget-ledger", required=True)
    parser.add_argument("--budget-cap", type=float, default=5.0)
    parser.add_argument("--budget-authorized-max", type=float, default=5.0)
    parser.add_argument("--budget-max-requests", type=int)
    parser.add_argument("--timeout", type=float, default=3600,
                        help="emergency wall-time ceiling; natural completion remains the goal")
    parser.add_argument("--stall-seconds", type=float, default=600,
                        help="stop after no native callback, turn, or API reservation progress")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--provenance")
    parser.add_argument("--server-log")
    parser.add_argument("--terminal-evidence-dir",
                        help="owner-only run directory for final replay and native terminal state")
    args = parser.parse_args()
    if args.timeout <= 0 or args.stall_seconds <= 0 or args.poll_seconds <= 0:
        parser.error("timeout, stall-seconds, and poll-seconds must be positive")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
