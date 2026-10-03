"""Forge-style, pilot-approved bounded priority passing over native Argentum views.

The strategic pilot must first choose PassPriority and explicitly authorize a short
wait. Subsequent choices use only the acting seat's masked observation and the
current native legal menu. Missing evidence expires the lease and wakes the pilot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from threading import Lock
from typing import Any, Mapping

from .pilot import ArtificialPlayer, ArgentumActionChoice, PilotChoice


def _native_pass(observation: Mapping[str, Any]) -> Mapping[str, Any] | None:
    if observation.get("pendingDecision") is not None or observation.get("terminated") is True:
        return None
    legal = observation.get("legalActions")
    if not isinstance(legal, list) or not legal:
        return None
    passes = [a for a in legal if isinstance(a, Mapping) and a.get("kind") == "PassPriority"]
    if (
        len(passes) != 1 or type(passes[0].get("actionId")) is not int
        or passes[0].get("affordable") is False
        or passes[0].get("isDecisionOption") is True
    ):
        return None
    return passes[0]


def _pass_and_mana_only(observation: Mapping[str, Any]) -> Mapping[str, Any] | None:
    passed = _native_pass(observation)
    if passed is None:
        return None
    legal = observation["legalActions"]
    if all(
        action is passed or (
            isinstance(action, Mapping)
            and action.get("kind") == "ActivateAbility"
            and action.get("isManaAbility") is True
            and action.get("isDecisionOption") is not True
        )
        for action in legal
    ):
        return passed
    return None


def _visible_checkpoint(
    observation: Mapping[str, Any], *, watch_opponents: bool = False,
) -> dict[str, Any] | None:
    state = observation.get("state")
    seat = observation.get("agentToAct")
    if (
        not isinstance(state, Mapping) or not isinstance(seat, str)
        or observation.get("perspectivePlayerId") != seat
        or state.get("priorityPlayerId") != seat
    ):
        return None
    turn, step, phase = (
        state.get("turnNumber"), state.get("currentStep"), state.get("currentPhase")
    )
    players, zones, cards, log = (
        state.get("players"), state.get("zones"), state.get("cards"), state.get("gameLog")
    )
    if (
        type(turn) is not int or not isinstance(step, str) or not isinstance(phase, str)
        or not isinstance(players, list) or not isinstance(zones, list)
        or not isinstance(cards, Mapping) or not isinstance(log, list)
    ):
        return None
    own = next((p for p in players if isinstance(p, Mapping) and p.get("playerId") == seat), None)
    if own is None or not isinstance(own.get("manaPool"), Mapping):
        return None
    observed_zones: list[dict[str, Any]] = []
    public_ids: set[str] = set()
    own_hand: list[str] | None = None
    stack: list[str] = []
    for zone in zones:
        if not isinstance(zone, Mapping) or not isinstance(zone.get("zoneId"), Mapping):
            return None
        owner = zone["zoneId"].get("ownerId")
        kind = zone["zoneId"].get("zoneType")
        if kind == "Hand" and owner == seat:
            ids = zone.get("cardIds")
            if not isinstance(ids, list):
                return None
            own_hand = ids
        if kind == "Stack":
            ids = zone.get("cardIds")
            if not isinstance(ids, list):
                return None
            stack.extend(ids)
        if (kind == "Battlefield" and (owner == seat or watch_opponents)) or (
            owner == seat and kind in {"Graveyard", "Exile", "Command"}
        ):
            ids = zone.get("cardIds")
            if not isinstance(ids, list):
                return None
            observed_zones.append({"owner": owner, "kind": kind, "ids": ids})
            public_ids.update(x for x in ids if isinstance(x, str))
    if own_hand is None:
        return None
    visible_cards = {card_id: cards.get(card_id) for card_id in sorted(public_ids)}
    life = [
        (p.get("playerId"), p.get("life"), p.get("poisonCounters"), p.get("commanderDamage"))
        for p in players if isinstance(p, Mapping)
    ]
    return {
        "seat": seat, "turn": turn, "step": step, "phase": phase,
        "active": state.get("activePlayerId"),
        "playerCount": len(life),
        "hand": own_hand, "zones": observed_zones, "cards": visible_cards,
        "stack": stack, "mana": dict(own["manaPool"]), "life": life,
        "log": log,
    }


@dataclass
class _Lease:
    start: dict[str, Any]
    previous: dict[str, Any]
    until: str
    reason: str
    lease_id: str
    watch_opponents: bool = False
    passes: int = 0


@dataclass
class DelegatedAutopassPilot:
    """Opt-in frontier component carrying explicit, per-seat strategic leases."""

    strategic_pilot: ArtificialPlayer
    name: str = "delegated-autopass"
    version: str = "1"
    max_passes: int = 16
    _leases: dict[str, _Lease] = field(default_factory=dict, init=False, repr=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def choose(self, observation: Mapping[str, Any]) -> PilotChoice:
        seat = observation.get("agentToAct")
        lease = None
        if isinstance(seat, str):
            with self._lock:
                lease = self._leases.pop(seat, None)
        checkpoint = _visible_checkpoint(
            observation, watch_opponents=lease.watch_opponents if lease else False,
        )
        if isinstance(seat, str):
            if lease is not None and checkpoint is not None and self._continues(lease, checkpoint, observation):
                passed = _pass_and_mana_only(observation)
                assert passed is not None
                lease.previous = checkpoint
                lease.passes += 1
                with self._lock:
                    self._leases[seat] = lease
                return ArgentumActionChoice(
                    action_id=passed["actionId"],
                    metadata={"delegatedPass": {
                        "leaseId": lease.lease_id, "version": self.version,
                        "until": lease.until, "reason": lease.reason,
                        "strategicWakeAvoided": True, "ordinal": lease.passes,
                    }},
                )

        choice = self.strategic_pilot.choose(observation)
        # A wake discards the old lease. A new lease needs a fresh checkpoint
        # using the strategic choice's own opponent-watch setting.
        directive = choice.metadata.get("priorityDelegation") if isinstance(choice, ArgentumActionChoice) else None
        if directive is None:
            return choice
        if isinstance(directive, Mapping):
            checkpoint = _visible_checkpoint(
                observation, watch_opponents=directive.get("watchOpponents", False) is True,
            )
        passed = _native_pass(observation)
        if (
            checkpoint is None or passed is None or choice.action_id != passed["actionId"]
            or not isinstance(directive, Mapping)
            or not {"until", "reason"}.issubset(directive)
            or set(directive) - {"until", "reason", "watchOpponents"}
            or directive.get("until") not in {"phase_end", "next_own_main"}
            or not isinstance(directive.get("reason"), str)
            or not directive["reason"].strip()
            or type(directive.get("watchOpponents", False)) is not bool
            or any(value for key, value in checkpoint["mana"].items() if key != "restrictedMana")
            or checkpoint["mana"].get("restrictedMana")
        ):
            # Keep the model's current native choice and provenance, but never arm
            # an invalid lease. The current action is still validated at the edge.
            return ArgentumActionChoice(
                action_id=choice.action_id, params=choice.params,
                metadata={
                    **dict(choice.metadata),
                    "priorityDelegationRejected": "invalid boundary, pass, state, or floating mana",
                },
            )
        lease_id = hashlib.sha256(json.dumps(
            [seat, checkpoint["turn"], checkpoint["step"], checkpoint["log"], directive],
            sort_keys=True, default=str,
        ).encode()).hexdigest()[:24]
        with self._lock:
            self._leases[seat] = _Lease(
                start=checkpoint, previous=checkpoint,
                until=directive["until"], reason=directive["reason"],
                lease_id=lease_id, watch_opponents=directive.get("watchOpponents", False),
            )
        return choice

    def _continues(
        self, lease: _Lease, now: dict[str, Any], observation: Mapping[str, Any],
    ) -> bool:
        if lease.passes >= self.max_passes or _pass_and_mana_only(observation) is None:
            return False
        start, previous = lease.start, lease.previous
        if (
            now["turn"] < previous["turn"]
            or now["turn"] > start["turn"] + start["playerCount"]
            or now["playerCount"] != start["playerCount"]
        ):
            return False
        if lease.until == "phase_end" and (now["turn"], now["phase"]) != (start["turn"], start["phase"]):
            return False
        if lease.until == "next_own_main" and now["turn"] > start["turn"] and (
            now["active"] == now["seat"] and now["step"] == "PRECOMBAT_MAIN"
        ):
            return False
        for key in ("hand", "zones", "cards", "stack", "mana", "life"):
            if now[key] != previous[key]:
                return False
        old_log, new_log = previous["log"], now["log"]
        if len(new_log) < len(old_log) or new_log[:len(old_log)] != old_log:
            return False
        # Forge's event-guarded lease wakes for every unreviewed consequential
        # event. Turn advancement alone is allowed only inside a future boundary.
        # Forge's default did not watch opponents' battlefield. Native stack
        # additions and unknown events still wake, even when there is no castable
        # response in the current legal menu.
        routine_events = {
            "turnChanged", "cardDrawn", "permanentEntered", "permanentLeft",
            "spellResolved", "creatureDied", "creatureAttacked", "counterAdded",
            "lifeChanged", "damageDealt",
        }
        return all(
            isinstance(event, Mapping) and event.get("type") in routine_events
            for event in new_log[len(old_log):]
        )
