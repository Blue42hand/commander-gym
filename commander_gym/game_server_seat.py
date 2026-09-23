"""Fail-closed policy boundary for an Argentum game-server AI seat.

The in-process Kotlin provider is responsible only for serializing the native,
seat-masked callback values and deserializing the response into Argentum's native
types.  This module is the Commander Gym side of that replaceable bridge: it turns
the callback into the existing :class:`ArtificialPlayer` observation shape and never
accepts a trusted/unmasked runtime snapshot.

No fallback policy lives here.  Invalid, stale, or provider-failure results raise and
must remain failures when returned through ``AiPlayerController``.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from .pilot import (
    ArgentumActionChoice,
    ArgentumDecisionChoice,
    ArtificialPlayer,
    PilotContractError,
    choose_for_observation,
)


class GameServerSeatError(PilotContractError):
    """Raised when a game-server callback cannot be adapted without guessing."""


@dataclass(frozen=True)
class NativeActionResponse:
    """Return one of the exact native legal action payloads supplied by Argentum."""

    action_id: int
    action: Mapping[str, Any]
    metadata: Mapping[str, Any]
    params: Mapping[str, Any]


@dataclass(frozen=True)
class NativeDecisionResponse:
    """Return a native structured ``DecisionResponse`` for the acting seat."""

    player_id: Any
    response: Mapping[str, Any]
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class SeatProvenance:
    """One policy invocation, containing only the masked policy boundary."""

    callback: str
    observation: Mapping[str, Any]
    choice: Mapping[str, Any]


ProvenanceSink = Callable[[SeatProvenance], None]


class GameServerSeatAdapter:
    """Adapt masked game-server callbacks to one existing ``ArtificialPlayer``.

    ``state``, legal actions, and decisions are JSON-compatible representations of
    the corresponding native Argentum values.  The bridge deliberately has no
    parameter for ``AiControllerContext.snapshot``; trusted authoritative state
    therefore cannot accidentally enter policy input.

    Ordinary action choices return the exact native action template supplied in
    ``legal_actions`` plus the pilot's native ``ActionParams``. Python does not apply
    those params: completion happens at the Kotlin/native edge through Argentum's
    authoritative parameterizer, so Commander Gym does not grow a second rules layer.
    """

    def __init__(
        self,
        pilot: ArtificialPlayer,
        player_id: Any,
        *,
        provenance_sink: ProvenanceSink | None = None,
    ) -> None:
        if player_id is None:
            raise GameServerSeatError("game-server seat requires player_id")
        self._pilot = pilot
        self._player_id = player_id
        self._provenance_sink = provenance_sink
        self._known_deck: dict[str, int] | None = None
        self._deck_archetype: str | None = None

    def set_deck_list(
        self,
        deck_list: Mapping[str, Any],
        archetype: str | None = None,
    ) -> None:
        """Retain the deck composition Argentum explicitly gives this AI seat.

        The list is card-name -> count only; it contains no library order and therefore adds
        strategic deck knowledge without exposing hidden runtime state.
        """

        if not isinstance(deck_list, Mapping):
            raise GameServerSeatError("deck list must be an object")
        normalized: dict[str, int] = {}
        for name, count in deck_list.items():
            if not isinstance(name, str) or not name:
                raise GameServerSeatError("deck-list card names must be non-empty strings")
            if type(count) is not int or count <= 0:
                raise GameServerSeatError("deck-list counts must be positive integers")
            normalized[name] = count
        if archetype is not None and (not isinstance(archetype, str) or not archetype.strip()):
            raise GameServerSeatError("deck archetype must be a non-empty string when supplied")
        self._known_deck = normalized
        self._deck_archetype = archetype.strip() if isinstance(archetype, str) else None

    def choose_action(
        self,
        state: Mapping[str, Any],
        legal_actions: Sequence[Mapping[str, Any]],
        pending_decision: Mapping[str, Any] | None,
        recent_game_log: Sequence[str] = (),
    ) -> NativeActionResponse | NativeDecisionResponse:
        observation = self._observation(
            state,
            legal_actions,
            pending_decision,
            recent_game_log,
        )
        choice = choose_for_observation(self._pilot, observation)

        if isinstance(choice, ArgentumActionChoice):
            try:
                native = legal_actions[choice.action_id]
            except (IndexError, TypeError) as exc:
                raise GameServerSeatError("selected native action is no longer present") from exc
            response = NativeActionResponse(
                action_id=choice.action_id,
                action=deepcopy(dict(native["action"])),
                metadata=deepcopy(dict(choice.metadata)),
                params=deepcopy(dict(choice.params)),
            )
            self._record(
                "chooseAction",
                observation,
                {
                    "channel": "action",
                    "actionId": choice.action_id,
                    "params": choice.params,
                    "metadata": choice.metadata,
                },
            )
            return response

        if isinstance(choice, ArgentumDecisionChoice):
            response = NativeDecisionResponse(
                player_id=self._player_id,
                response=deepcopy(dict(choice.response)),
                metadata=deepcopy(dict(choice.metadata)),
            )
            self._record(
                "chooseAction",
                observation,
                {"channel": "decision", "response": choice.response, "metadata": choice.metadata},
            )
            return response

        raise GameServerSeatError("pilot returned an unsupported game-server response")

    def decide_mulligan(self, mulligan: Mapping[str, Any]) -> bool:
        actions = [
            {
                "action": {"type": "KeepHand", "playerId": self._player_id},
                "actionType": "KeepHand",
                "kind": "KeepHand",
                "description": "Keep this opening hand",
                "semanticId": "commander-gym-callback-v1:mulligan:keep",
                "affordable": True,
            },
            {
                "action": {"type": "TakeMulligan", "playerId": self._player_id},
                "actionType": "TakeMulligan",
                "kind": "TakeMulligan",
                "description": "Take a mulligan",
                "semanticId": "commander-gym-callback-v1:mulligan:take",
                "affordable": True,
            },
        ]
        observation = self._observation(
            {"mulligan": deepcopy(dict(mulligan))}, actions, None, ()
        )
        choice = choose_for_observation(self._pilot, observation)
        if not isinstance(choice, ArgentumActionChoice) or choice.params:
            raise GameServerSeatError("mulligan callback requires an unparameterized keep/take choice")
        keep = choice.action_id == 0
        self._record("decideMulligan", observation, {"keep": keep, "metadata": choice.metadata})
        return keep

    def choose_bottom_cards(self, bottom: Mapping[str, Any]) -> list[Any]:
        required = bottom.get("cardsToPutOnBottom")
        hand = bottom.get("hand")
        if type(required) is int and isinstance(hand, list):
            forced: list[Any] | None = None
            if required == 0:
                forced = []
            elif required == len(hand):
                forced = list(hand)
            if forced is not None:
                observation = self._observation({}, (), None, ())
                metadata = {
                    "routing": {
                        "path": "mechanical",
                        "handler": "forced-bottom-cards",
                        "handlerVersion": "1",
                        "strategicWakeAvoided": True,
                    }
                }
                self._record(
                    "chooseBottomCards",
                    observation,
                    {"selectedCards": forced, "metadata": metadata},
                )
                return forced

        decision_id = bottom.get("decisionId", f"bottom-cards:{self._player_id}")
        if not isinstance(decision_id, str) or not decision_id:
            raise GameServerSeatError("bottom-cards callback decisionId must be a string when supplied")
        pending = {
            "decisionId": decision_id,
            "semanticId": "commander-gym-callback-v1:bottom-cards",
            "kind": "BottomCards",
            "prompt": "Choose cards to put on the bottom of your library",
            "requiresStructuredResponse": True,
            "cardsToPutOnBottom": bottom.get("cardsToPutOnBottom"),
            "hand": deepcopy(bottom.get("hand")),
            "cards": deepcopy(bottom.get("cards", {})),
        }
        observation = self._observation({}, (), pending, ())
        choice = choose_for_observation(self._pilot, observation)
        if not isinstance(choice, ArgentumDecisionChoice):
            raise GameServerSeatError("bottom-cards callback requires a structured response")
        selected = choice.response.get("selectedCards")
        required = bottom.get("cardsToPutOnBottom")
        hand = bottom.get("hand")
        if not isinstance(selected, list) or type(required) is not int or not isinstance(hand, list):
            raise GameServerSeatError("bottom-cards response is malformed")
        if len(selected) != required or len(selected) != len(set(map(str, selected))):
            raise GameServerSeatError("bottom-cards response has the wrong number of unique cards")
        if any(card not in hand for card in selected):
            raise GameServerSeatError("bottom-cards response contains a card outside the masked hand")
        self._record(
            "chooseBottomCards",
            observation,
            {"selectedCards": selected, "metadata": choice.metadata},
        )
        return deepcopy(selected)

    def _observation(
        self,
        state: Mapping[str, Any],
        legal_actions: Sequence[Mapping[str, Any]],
        pending_decision: Mapping[str, Any] | None,
        recent_game_log: Sequence[str],
    ) -> dict[str, Any]:
        if not isinstance(state, Mapping):
            raise GameServerSeatError("masked ClientGameState must be an object")
        perspective = state.get("perspectivePlayerId", state.get("viewingPlayerId"))
        if perspective is not None and perspective != self._player_id:
            raise GameServerSeatError("masked state perspective does not match the controlled seat")

        actions: list[dict[str, Any]] = []
        for action_id, raw in enumerate(legal_actions):
            if not isinstance(raw, Mapping) or not isinstance(raw.get("action"), Mapping):
                raise GameServerSeatError("each native legal action must contain an action object")
            item = deepcopy(dict(raw))
            item["actionId"] = action_id
            # Keep the ArtificialPlayer observation vocabulary aligned with the Gym path while
            # preserving the native game-server fields verbatim. These are aliases only: no
            # legality or strategic meaning is inferred in Python.
            if "kind" not in item and isinstance(item.get("actionType"), str):
                item["kind"] = item["actionType"]
            if "affordable" not in item and isinstance(item.get("isAffordable"), bool):
                item["affordable"] = item["isAffordable"]
            actions.append(item)

        if pending_decision is not None and not isinstance(pending_decision, Mapping):
            raise GameServerSeatError("native pending decision must be an object or null")
        if any(not isinstance(entry, str) for entry in recent_game_log):
            raise GameServerSeatError("recent game log entries must be strings")

        pending = None
        if pending_decision is not None:
            pending = deepcopy(dict(pending_decision))
            # AiPlayerController receives Argentum's native PendingDecision JSON, while the
            # core ArtificialPlayer contract follows the Gym observation vocabulary. Keep the
            # native fields for model context, but add the stable aliases/routing metadata the
            # strategic pilot validates before it will return a DecisionResponse.
            if "decisionId" not in pending:
                native_id = pending.get("id")
                if isinstance(native_id, str) and native_id:
                    pending["decisionId"] = native_id
            if "kind" not in pending:
                native_type = pending.get("type")
                if isinstance(native_type, str) and native_type:
                    pending["kind"] = native_type
            # On the game-server seam a non-null PendingDecision is not folded into synthetic
            # legal actions; AiPlayerController must answer it with a native DecisionResponse.
            pending["requiresStructuredResponse"] = True

        observation = {
            "type": "GameServerSeat",
            "state": deepcopy(dict(state)),
            "legalActions": actions,
            "pendingDecision": pending,
            "recentGameLog": list(recent_game_log),
            "perspectivePlayerId": self._player_id,
            "agentToAct": self._player_id,
            "terminated": False,
        }
        if self._known_deck is not None:
            observation["knownDeck"] = {
                "cards": deepcopy(self._known_deck),
                **({"archetype": self._deck_archetype} if self._deck_archetype else {}),
            }
        return observation

    def _record(
        self,
        callback: str,
        observation: Mapping[str, Any],
        choice: Mapping[str, Any],
    ) -> None:
        if self._provenance_sink is not None:
            self._provenance_sink(
                SeatProvenance(
                    callback=callback,
                    observation=deepcopy(dict(observation)),
                    choice=deepcopy(dict(choice)),
                )
            )
