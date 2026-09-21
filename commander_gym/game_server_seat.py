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

    For the first bounded adapter, ordinary action choices return an exact native
    action supplied in ``legal_actions``.  Non-empty ``ActionParams`` fail closed:
    completing native action templates belongs in the Kotlin/native edge and must not
    be reimplemented as a second rules layer in Python.
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
            if choice.params:
                raise GameServerSeatError(
                    "game-server seat adapter does not silently parameterize native actions"
                )
            try:
                native = legal_actions[choice.action_id]
            except (IndexError, TypeError) as exc:
                raise GameServerSeatError("selected native action is no longer present") from exc
            response = NativeActionResponse(
                action_id=choice.action_id,
                action=deepcopy(dict(native["action"])),
                metadata=deepcopy(dict(choice.metadata)),
            )
            self._record(
                "chooseAction",
                observation,
                {"channel": "action", "actionId": choice.action_id, "metadata": choice.metadata},
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
            {"action": {"type": "KeepHand", "playerId": self._player_id}, "actionType": "KeepHand"},
            {"action": {"type": "TakeMulligan", "playerId": self._player_id}, "actionType": "TakeMulligan"},
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
        decision_id = bottom.get("decisionId", f"bottom-cards:{self._player_id}")
        if not isinstance(decision_id, str) or not decision_id:
            raise GameServerSeatError("bottom-cards callback decisionId must be a string when supplied")
        pending = {
            "decisionId": decision_id,
            "kind": "BottomCards",
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
            actions.append(item)

        if pending_decision is not None and not isinstance(pending_decision, Mapping):
            raise GameServerSeatError("native pending decision must be an object or null")
        if any(not isinstance(entry, str) for entry in recent_game_log):
            raise GameServerSeatError("recent game log entries must be strings")

        return {
            "type": "GameServerSeat",
            "state": deepcopy(dict(state)),
            "legalActions": actions,
            "pendingDecision": deepcopy(dict(pending_decision)) if pending_decision else None,
            "recentGameLog": list(recent_game_log),
            "perspectivePlayerId": self._player_id,
            "agentToAct": self._player_id,
            "terminated": False,
        }

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
