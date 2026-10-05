"""Forge-style, pilot-approved bounded priority passing over native Argentum views.

The strategic pilot must first choose PassPriority and explicitly authorize a short
wait. Subsequent choices use only the acting seat's masked observation and the
current native legal menu. Missing evidence expires the lease and wakes the pilot.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
from threading import Lock
from typing import Any, Mapping

from .pilot import ArtificialPlayer, ArgentumActionChoice, ArgentumDecisionChoice, PilotChoice


_NATIVE_PHASES = {"BEGINNING", "PRECOMBAT_MAIN", "COMBAT", "POSTCOMBAT_MAIN", "ENDING"}
_NATIVE_STEPS = {
    "UNTAP", "UPKEEP", "DRAW", "PRECOMBAT_MAIN", "BEGIN_COMBAT",
    "DECLARE_ATTACKERS", "DECLARE_BLOCKERS", "FIRST_STRIKE_COMBAT_DAMAGE",
    "COMBAT_DAMAGE", "END_COMBAT", "POSTCOMBAT_MAIN", "END", "CLEANUP",
}


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


def _nonmana_ability_keys(observation: Mapping[str, Any]) -> tuple[tuple[str, str], ...] | None:
    """Exact currently executable alternatives; unknown action shapes fail closed."""
    if _native_pass(observation) is None:
        return None
    keys = []
    for offered in observation["legalActions"]:
        if not isinstance(offered, Mapping):
            return None
        if offered.get("kind") == "PassPriority" or (
            offered.get("kind") == "ActivateAbility" and offered.get("isManaAbility") is True
        ):
            continue
        # Argentum exposes some currently unaffordable abilities for inspection.
        # Like Forge's actionability certificate, they need no strategic deferral;
        # becoming affordable changes this exact executable set and wakes the pilot.
        if (
            offered.get("kind") == "ActivateAbility"
            and offered.get("isManaAbility") is False
            and offered.get("affordable") is False
            and offered.get("isAffordable") is False
            and offered.get("isDecisionOption") is not True
        ):
            continue
        action = offered.get("action")
        if (
            offered.get("kind") != "ActivateAbility" or not isinstance(action, Mapping)
            or offered.get("isManaAbility") is not False
            or offered.get("isDecisionOption") is True
        ):
            return None
        source, ability = action.get("sourceId"), action.get("abilityId")
        if not isinstance(source, str) or not source or not isinstance(ability, str) or not ability:
            return None
        keys.append((source, ability))
    return tuple(sorted(set(keys)))


def _nonmana_ability_menu_digest(observation: Mapping[str, Any]) -> str | None:
    keys = _nonmana_ability_keys(observation)
    if keys is None:
        return None
    visible = []
    for offered in observation["legalActions"]:
        if offered.get("kind") != "ActivateAbility" or offered.get("isManaAbility") is not False:
            continue
        if offered.get("affordable") is False and offered.get("isAffordable") is False:
            continue
        # actionId is only a live routing handle. Everything else in the
        # Argentum-authored offer, including parameterSpec, must remain equal.
        visible.append({key: value for key, value in offered.items() if key != "actionId"})
    try:
        encoded = json.dumps(visible, sort_keys=True)
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(encoded.encode()).hexdigest()


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
        if not isinstance(kind, str):
            return None
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
        if kind == "Battlefield":
            ids = zone.get("cardIds")
            if not isinstance(ids, list):
                return None
            if owner != seat and not watch_opponents:
                controlled_ids = []
                for card_id in ids:
                    card = cards.get(card_id) if isinstance(card_id, str) else None
                    if not isinstance(card, Mapping) or not isinstance(card.get("controllerId"), str):
                        return None
                    if card["controllerId"] == seat:
                        controlled_ids.append(card_id)
                ids = controlled_ids
            if owner == seat or watch_opponents or ids:
                observed_zones.append({"owner": owner, "kind": kind, "ids": ids})
                public_ids.update(x for x in ids if isinstance(x, str))
        elif owner == seat and kind in {"Graveyard", "Exile", "Command"}:
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
    deferred_abilities: tuple[tuple[str, str], ...] | None = None
    deferred_menu_digest: str | None = None
    max_passes: int = 16


@dataclass
class _Continuation:
    steps: tuple[dict[str, Any], ...]
    index: int
    reason: str
    plan_id: str
    before: dict[str, Any]
    last_action: str
    last_card_id: str | None = None
    lease: _Lease | None = None


def _condition_matches(condition: Mapping[str, Any], now: Mapping[str, Any]) -> bool:
    return (
        ("phase" not in condition or condition["phase"] == now["phase"])
        and ("step" not in condition or condition["step"] == now["step"])
        and ("stackEmpty" not in condition
             or condition["stackEmpty"] is (not bool(now["stack"])))
    )


def _fresh_named_action(
    observation: Mapping[str, Any], now: Mapping[str, Any], step: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    if observation.get("pendingDecision") is not None or not _condition_matches(step["when"], now):
        return None
    kind = "PlayLand" if step["type"] == "playLand" else "CastSpell"
    card_id = step["cardId"]
    if kind == "PlayLand":
        if now["active"] != now["seat"] or now["stack"] or card_id not in now["hand"]:
            return None
    elif card_id not in now["hand"] and card_id not in (_own_command_zone(now) or []):
        return None
    legal = observation.get("legalActions")
    if not isinstance(legal, list):
        return None
    same_card = [offer for offer in legal if isinstance(offer, Mapping)
                 and isinstance(offer.get("action"), Mapping)
                 and offer["action"].get("cardId") == card_id]
    if len(same_card) != 1:
        return None
    offered = same_card[0]
    action = offered["action"]
    if (
        offered.get("kind") != kind or offered.get("actionType") != kind
        or offered.get("affordable") is not True
        or offered.get("isAffordable") is not True
        or offered.get("isDecisionOption") is not False
        or action.get("playerId") != now["seat"]
        or type(offered.get("actionId")) is not int
        or step["params"] != {}
    ):
        return None
    if kind == "PlayLand":
        if (action != {"type": "PlayLand", "playerId": now["seat"],
                       "cardId": card_id, "asBackFace": False}
            or offered.get("parameterSpec") != {"allowedFields": {}}):
            return None
    elif not _parameterless_native_cast(offered, guarded_template=True):
        return None
    return offered


def _continuation_wake(
    plan: _Continuation, observation: Mapping[str, Any], reason: str,
) -> dict[str, Any]:
    now = _visible_checkpoint(observation, watch_opponents=True)
    previous = plan.lease.previous if plan.lease is not None else plan.before
    events: list[Any] = []
    if now is not None and now["log"][:len(previous["log"])] == previous["log"]:
        events = now["log"][len(previous["log"]):]
    return {
        "planId": plan.plan_id, "reason": reason, "nextStep": plan.index,
        "intent": {"reason": plan.reason, "steps": list(plan.steps)},
        "changedVisibleFields": (
            None if now is None else [key for key in (
                "turn", "phase", "step", "active", "hand", "zones", "cards",
                "stack", "mana", "life", "log",
            ) if now[key] != previous[key]]
        ),
        "maskedEvents": events,
        "maskedState": None if now is None else {
            key: now[key] for key in (
                "turn", "phase", "step", "active", "hand", "zones", "stack",
                "mana", "life",
            )
        },
    }


def _post_cast_wait_ready(
    plan: _Continuation, now: Mapping[str, Any], observation: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    """Recognize only the first unchanged priority over the exact chosen spell."""
    before = plan.before
    card_id = plan.last_card_id
    passed = _pass_and_mana_only(observation)
    if (passed is None or not isinstance(card_id, str)
        or before["seat"] != now["seat"] or before["turn"] != now["turn"]
        or before["phase"] != now["phase"] or before["step"] != now["step"]
        or before["active"] != now["active"] or before["stack"]
        or now["stack"] != [card_id] or before["life"] != now["life"]
        or any(value for key, value in now["mana"].items() if key != "restrictedMana")
        or now["mana"].get("restrictedMana")):
        return None
    old_hand = list(before["hand"])
    old_command = _own_command_zone(before)
    new_command = _own_command_zone(now)
    if card_id in old_hand:
        old_hand.remove(card_id)
        if now["hand"] != old_hand or old_command != new_command:
            return None
    elif old_command is not None and card_id in old_command:
        old_command = list(old_command)
        old_command.remove(card_id)
        if now["hand"] != old_hand or new_command != old_command:
            return None
    else:
        return None
    old_other = [z for z in before["zones"] if z["kind"] not in {"Hand", "Command"}]
    new_other = [z for z in now["zones"] if z["kind"] not in {"Hand", "Command"}]
    if old_other != new_other:
        return None
    if any(now["cards"].get(key) != value for key, value in before["cards"].items()):
        return None
    old_log, new_log = before["log"], now["log"]
    if (new_log[:len(old_log)] != old_log or len(new_log) != len(old_log) + 1
        or not isinstance(new_log[-1], Mapping)
        or new_log[-1].get("type") != "spellCast"):
        return None
    return passed


@dataclass
class _PendingCast:
    before: dict[str, Any]
    land_id: str
    cast_id: str
    reason: str
    guarded_template: bool = False
    cast_zone: str = "Hand"


def _own_battlefield(checkpoint: Mapping[str, Any]) -> list[str] | None:
    zones = [zone for zone in checkpoint["zones"]
             if zone["owner"] == checkpoint["seat"] and zone["kind"] == "Battlefield"]
    return zones[0]["ids"] if len(zones) == 1 else None


def _own_command_zone(checkpoint: Mapping[str, Any]) -> list[str] | None:
    zones = [zone for zone in checkpoint["zones"]
             if zone["owner"] == checkpoint["seat"] and zone["kind"] == "Command"]
    return zones[0]["ids"] if len(zones) == 1 else ([] if not zones else None)


def _parameterless_native_cast(
    offered: Mapping[str, Any], *, guarded_template: bool = False,
) -> bool:
    """Accept only a default cast; opt-in supports Argentum's optional field template."""
    spec = offered.get("parameterSpec")
    action = offered.get("action")
    if (
        not isinstance(spec, Mapping) or set(spec) != {"allowedFields"}
        or not isinstance(action, Mapping) or action.get("type") != "CastSpell"
        or offered.get("hasXCost") is not False
        or offered.get("additionalCostInfo") is not None
        or offered.get("isDecisionOption") is True
    ):
        return False
    fields = spec.get("allowedFields")
    if fields != {}:
        if (
            not guarded_template
            or fields != {"targets": "ENTITY_ID_ARRAY", "xValue": "INTEGER"}
            or offered.get("requiresTargets") is not False
            or offered.get("validTargets") not in (None, [])
            or offered.get("targetRequirements") is not None
            or offered.get("requiresDamageDistribution") is not False
            or offered.get("requiresManaColorChoice") is not False
            or offered.get("modalEnumeration") is not None
            or offered.get("maxAffordableX") is not None
        ):
            return False
    if guarded_template and any(
        offered.get(field) is not False for field in (
            "hasDelve", "hasConvoke", "hasHarmonize", "hasTapForGeneric",
        )
    ):
        return False
    defaults = {
        # Argentum's native CastSpell wire action includes these fields even
        # when the cast uses none of the corresponding optional costs. Match
        # their exact defaults; a real choice still requires a model wake.
        "additionalCostChoices": {}, "additionalManaForCounters": 0,
        "additionalCostPayment": None, "alternativeCostType": None,
        "alternativePayment": None, "castFaceDown": False,
        "castPrototyped": False,
        "casualtyCreature": None, "chosenModes": [], "conspiredCreatures": [],
        "damageDistribution": None, "declaredCostIndices": [],
        "declaredCostSlot": None, "declaredCostTimes": 1,
        "faceIndex": None, "giftRecipient": None,
        "graveyardCastRider": None, "graveyardLifeCost": 0,
        "modeDamageDistribution": {}, "modeTargetsOrdered": [],
        "splicedCardIds": [], "targets": [], "useAlternativeCost": False,
        "useWithoutPayingManaCost": False, "wasWaterbendPaid": False,
        "xValue": None,
    }
    if any(
        type(action.get(field)) is not type(value) or action.get(field) != value
        for field, value in defaults.items()
    ):
        return False
    return (
        action.get("paymentStrategy") == {"type": "AutoPay"}
        and isinstance(action.get("cardId"), str)
        and isinstance(action.get("playerId"), str)
        and set(action) == set(defaults) | {"type", "cardId", "playerId", "paymentStrategy"}
    )


def _ready_followup_cast(
    pending: _PendingCast, now: dict[str, Any] | None, observation: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    if now is None or observation.get("pendingDecision") is not None:
        return None
    before = pending.before
    if (
        now["seat"] != before["seat"] or now["turn"] != before["turn"]
        or now["phase"] != before["phase"] or now["active"] != now["seat"]
        or now["stack"] != before["stack"] or now["mana"] != before["mana"]
        or now["life"] != before["life"]
        or (pending.guarded_template and now["step"] != before["step"])
    ):
        return None
    if pending.cast_zone == "Command" and pending.guarded_template:
        command = _own_command_zone(now)
        if command is None or pending.cast_id not in command:
            return None
    elif pending.cast_id not in now["hand"]:
        return None
    old_hand = list(before["hand"])
    if pending.land_id not in old_hand:
        return None
    old_hand.remove(pending.land_id)
    old_board, new_board = _own_battlefield(before), _own_battlefield(now)
    if pending.guarded_template:
        # Native views omit empty zones. The land must be the sole battlefield
        # addition; ordering of pre-existing permanents is not meaningful.
        old_board = old_board if old_board is not None else (
            [] if not any(z["kind"] == "Battlefield" and z["owner"] == before["seat"]
                          for z in before["zones"]) else None
        )
        new_board = new_board if new_board is not None else (
            [] if not any(z["kind"] == "Battlefield" and z["owner"] == now["seat"]
                          for z in now["zones"]) else None
        )
    if old_board is None or new_board is None or pending.land_id in old_board:
        return None
    added = list(new_board)
    if pending.land_id not in added:
        return None
    added.remove(pending.land_id)
    board_matches = (
        len(added) == len(old_board) and len(set(added)) == len(added)
        and len(set(old_board)) == len(old_board) and set(added) == set(old_board)
    ) if pending.guarded_template else added == old_board
    if now["hand"] != old_hand or not board_matches:
        return None
    if [z for z in now["zones"] if z["kind"] != "Battlefield" or z["owner"] != now["seat"]] != [
        z for z in before["zones"] if z["kind"] != "Battlefield" or z["owner"] != before["seat"]
    ]:
        return None
    if any(now["cards"].get(key) != value for key, value in before["cards"].items()):
        return None
    old_log, new_log = before["log"], now["log"]
    if new_log[:len(old_log)] != old_log or len(new_log) != len(old_log) + 1:
        return None
    if not isinstance(new_log[-1], Mapping) or new_log[-1].get("type") != "permanentEntered":
        return None
    legal = observation.get("legalActions")
    if not isinstance(legal, list):
        return None
    if pending.guarded_template and any(
        isinstance(offered, Mapping)
        and isinstance(offered.get("action"), Mapping)
        and offered["action"].get("cardId") == pending.cast_id
        and offered.get("kind") != "CastSpell"
        for offered in legal
    ):
        # A second cost, face, or timing variant needs a new strategic choice.
        return None
    if pending.guarded_template and sum(
        isinstance(offered, Mapping)
        and offered.get("kind") == "CastSpell"
        and isinstance(offered.get("action"), Mapping)
        and offered["action"].get("cardId") == pending.cast_id
        for offered in legal
    ) != 1:
        return None
    casts = [action for action in legal if isinstance(action, Mapping)
             and action.get("kind") == "CastSpell"
             and isinstance(action.get("action"), Mapping)
             and action["action"].get("cardId") == pending.cast_id
             and action["action"].get("playerId") == now["seat"]
             and action.get("affordable") is True
             and action.get("isAffordable") is True
             and (not pending.guarded_template or (
                 action.get("sourceZone") == ("COMMAND" if pending.cast_zone == "Command" else None)
             ))
             and _parameterless_native_cast(action, guarded_template=pending.guarded_template)
             and type(action.get("actionId")) is int]
    return casts[0] if len(casts) == 1 else None


@dataclass
class DelegatedAutopassPilot:
    """Opt-in frontier component carrying explicit, per-seat strategic leases."""

    strategic_pilot: ArtificialPlayer
    name: str = "delegated-autopass"
    version: str = "1"
    max_passes: int = 16
    allow_named_deferrals: bool = False
    guarded_then_cast_templates: bool = False
    allow_declarative_continuation: bool = False
    _leases: dict[str, _Lease] = field(default_factory=dict, init=False, repr=False)
    _pending_casts: dict[str, _PendingCast] = field(default_factory=dict, init=False, repr=False)
    _continuations: dict[str, _Continuation] = field(default_factory=dict, init=False, repr=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def choose(self, observation: Mapping[str, Any]) -> PilotChoice:
        seat = observation.get("agentToAct")
        lease = None
        pending_cast = None
        plan = None
        if isinstance(seat, str):
            with self._lock:
                lease = self._leases.pop(seat, None)
                pending_cast = self._pending_casts.pop(seat, None)
                plan = self._continuations.pop(seat, None)
        wake = None
        if plan is not None:
            result, reason = self._continue_plan(plan, observation)
            if result is not None:
                return result
            wake = _continuation_wake(plan, observation, reason)
        if pending_cast is not None:
            now = _visible_checkpoint(observation, watch_opponents=True)
            followup = _ready_followup_cast(pending_cast, now, observation)
            if followup is not None:
                return ArgentumActionChoice(
                    action_id=followup["actionId"],
                    metadata={"forgeThenCast": {"cardId": pending_cast.cast_id,
                                                "reason": pending_cast.reason,
                                                "version": self.version}},
                )
        checkpoint = _visible_checkpoint(
            observation, watch_opponents=lease.watch_opponents if lease else False,
        )
        if isinstance(seat, str):
            if lease is not None and checkpoint is not None and self._continues(lease, checkpoint, observation):
                passed = _native_pass(observation) if lease.deferred_abilities is not None else _pass_and_mana_only(observation)
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
        if wake is not None:
            if isinstance(choice, ArgentumActionChoice):
                choice = ArgentumActionChoice(
                    action_id=choice.action_id, params=choice.params,
                    metadata={**dict(choice.metadata), "continuationWake": wake},
                )
            elif isinstance(choice, ArgentumDecisionChoice):
                choice = ArgentumDecisionChoice(
                    response=choice.response,
                    metadata={**dict(choice.metadata), "continuationWake": wake},
                )
        continuation = choice.metadata.get("continuation") if isinstance(choice, ArgentumActionChoice) else None
        if continuation is not None:
            accepted, reason = self._install_continuation(choice, observation, continuation)
            if not accepted:
                return ArgentumActionChoice(
                    action_id=choice.action_id, params=choice.params,
                    metadata={**dict(choice.metadata), "continuationRejected": reason},
                )
            return choice
        then_cast = choice.metadata.get("thenCast") if isinstance(choice, ArgentumActionChoice) else None
        if then_cast is not None:
            current = _visible_checkpoint(observation, watch_opponents=True)
            legal = observation.get("legalActions")
            action = next((item for item in legal if isinstance(item, Mapping)
                           and item.get("actionId") == choice.action_id), None) if isinstance(legal, list) else None
            land_id = action.get("action", {}).get("cardId") if isinstance(action, Mapping) and isinstance(action.get("action"), Mapping) else None
            valid = (
                self.allow_named_deferrals and current is not None
                and isinstance(then_cast, Mapping) and set(then_cast) == {"cardId", "reason"}
                and isinstance(then_cast.get("cardId"), str) and then_cast["cardId"]
                and isinstance(then_cast.get("reason"), str) and then_cast["reason"].strip()
                and isinstance(action, Mapping) and action.get("kind") == "PlayLand"
                and isinstance(land_id, str) and land_id in current["hand"]
                and (then_cast["cardId"] in current["hand"] or (
                    self.guarded_then_cast_templates
                    and then_cast["cardId"] in (_own_command_zone(current) or [])
                )) and then_cast["cardId"] != land_id
                and current["active"] == seat and not current["stack"]
                and choice.params == {}
            )
            if not valid:
                return ArgentumActionChoice(
                    action_id=choice.action_id, params=choice.params,
                    metadata={**dict(choice.metadata), "thenCastRejected": "invalid exact land-to-cast intent"},
                )
            with self._lock:
                self._pending_casts[seat] = _PendingCast(
                    before=current, land_id=land_id, cast_id=then_cast["cardId"],
                    reason=then_cast["reason"],
                    guarded_template=self.guarded_then_cast_templates,
                    cast_zone=("Command" if then_cast["cardId"] not in current["hand"] else "Hand"),
                )
            return choice
        # A wake discards the old lease. A new lease needs a fresh checkpoint
        # using the strategic choice's own opponent-watch setting.
        directive = choice.metadata.get("priorityDelegation") if isinstance(choice, ArgentumActionChoice) else None
        if directive is None:
            return choice
        if isinstance(directive, Mapping):
            checkpoint = _visible_checkpoint(
                observation, watch_opponents=(
                    self.allow_named_deferrals or directive.get("watchOpponents", False) is True
                ),
            )
        passed = _native_pass(observation)
        named = self.allow_named_deferrals and isinstance(directive, Mapping) and directive.get("until") == "turn_end"
        offered = _nonmana_ability_keys(observation) if named else None
        requested = directive.get("deferAbilities") if named else None
        requested_keys = None
        if isinstance(requested, list) and all(
            isinstance(item, Mapping) and set(item) == {"sourceId", "abilityId"}
            and isinstance(item.get("sourceId"), str) and isinstance(item.get("abilityId"), str)
            for item in requested
        ):
            requested_keys = tuple(sorted((item["sourceId"], item["abilityId"]) for item in requested))
        if (
            checkpoint is None or passed is None or choice.action_id != passed["actionId"]
            or not isinstance(directive, Mapping)
            or not {"until", "reason"}.issubset(directive)
            or set(directive) - ({"until", "reason", "deferAbilities", "watchOpponents"} if named else {"until", "reason", "watchOpponents"})
            or (named and (offered is None or not offered or requested_keys != offered))
            or (named and directive.get("watchOpponents", True) is not True)
            or (not named and directive.get("until") not in {"phase_end", "next_own_main"})
            or not isinstance(directive.get("reason"), str)
            or not directive["reason"].strip()
            or (not named and type(directive.get("watchOpponents", False)) is not bool)
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
                lease_id=lease_id, watch_opponents=(
                    True if self.allow_named_deferrals else directive.get("watchOpponents", False)
                ),
                deferred_abilities=offered if named else None,
                deferred_menu_digest=_nonmana_ability_menu_digest(observation) if named else None,
            )
        return choice

    def _install_continuation(
        self, choice: ArgentumActionChoice, observation: Mapping[str, Any],
        directive: Any,
    ) -> tuple[bool, str]:
        if not self.allow_declarative_continuation:
            return False, "component-disabled"
        now = _visible_checkpoint(observation, watch_opponents=True)
        legal = observation.get("legalActions")
        selected = next((item for item in legal if isinstance(item, Mapping)
                         and item.get("actionId") == choice.action_id), None) if isinstance(legal, list) else None
        if (now is None or not isinstance(selected, Mapping) or choice.params != {}
            or not isinstance(directive, Mapping) or set(directive) != {"reason", "steps"}
            or not isinstance(directive.get("reason"), str) or not directive["reason"].strip()):
            return False, "invalid-current-choice-or-view"
        steps = directive.get("steps")
        if not isinstance(steps, list) or not 1 <= len(steps) <= 4:
            return False, "invalid-step-count"
        kinds = []
        for step in steps:
            if not isinstance(step, Mapping):
                return False, "malformed-step"
            kind = step.get("type")
            if not isinstance(kind, str):
                return False, "invalid-step-type"
            if kind == "wait":
                if (set(step) != {"type", "until", "maxPasses"}
                    or not isinstance(step.get("until"), str)
                    or step.get("until") not in {"phase_end", "next_own_main"}
                    or type(step.get("maxPasses")) is not int
                    or not 1 <= step["maxPasses"] <= min(self.max_passes, 16)):
                    return False, "invalid-wait"
            elif kind in {"playLand", "cast"}:
                when = step.get("when")
                if (set(step) != {"type", "cardId", "when", "params"}
                    or not isinstance(step.get("cardId"), str) or not step["cardId"]
                    or not isinstance(when, Mapping) or "stackEmpty" not in when
                    or set(when) - {"phase", "step", "stackEmpty"}
                    or any(not isinstance(when[key], str) or not when[key]
                           for key in ("phase", "step") if key in when)
                    or ("stackEmpty" in when and type(when["stackEmpty"]) is not bool)
                    or ("phase" in when and when["phase"] not in _NATIVE_PHASES)
                    or ("step" in when and when["step"] not in _NATIVE_STEPS)
                    or step.get("params") != {}):
                    return False, "invalid-action-step"
                visible = now["hand"] if kind == "playLand" else (
                    now["hand"] + (_own_command_zone(now) or []))
                if step["cardId"] not in visible:
                    return False, "card-not-currently-visible"
            else:
                return False, "unknown-step"
            kinds.append(kind)
        current_kind = selected.get("kind")
        if not isinstance(current_kind, str):
            return False, "uncertified-native-choice"
        if (current_kind == "PlayLand"
            and kinds not in (["cast"], ["cast", "wait"])
            or current_kind == "PassPriority"
            and kinds not in (["wait"], ["wait", "playLand"],
                              ["wait", "playLand", "cast"],
                              ["wait", "playLand", "cast", "wait"])
            or current_kind not in {"PlayLand", "PassPriority"}):
            return False, "unsupported-step-order"
        if (selected.get("affordable") is not True
            or selected.get("isAffordable") is not True
            or selected.get("actionType") != current_kind
            or not isinstance(selected.get("action"), Mapping)
            or selected["action"].get("playerId") != now["seat"]):
            return False, "uncertified-native-choice"
        if current_kind == "PlayLand":
            land_id = selected["action"].get("cardId")
            if (not isinstance(land_id, str) or land_id not in now["hand"]
                or land_id == steps[0]["cardId"] or now["active"] != now["seat"]
                or now["stack"] or selected.get("parameterSpec") != {"allowedFields": {}}
                or selected["action"] != {
                    "type": "PlayLand", "playerId": now["seat"], "cardId": land_id,
                    "asBackFace": False,
                }):
                return False, "invalid-land-transition-start"
        else:
            if (_pass_and_mana_only(observation) is not selected
                or any(value for key, value in now["mana"].items() if key != "restrictedMana")
                or now["mana"].get("restrictedMana")):
                return False, "unsafe-priority-start"
            land_id = None
        plan_id = hashlib.sha256(json.dumps(
            [now["seat"], now["turn"], now["log"], directive], sort_keys=True,
        ).encode()).hexdigest()[:24]
        plan = _Continuation(
            steps=tuple(deepcopy(dict(step)) for step in steps), index=0,
            reason=directive["reason"], plan_id=plan_id, before=deepcopy(now),
            last_action=current_kind, last_card_id=land_id,
        )
        if current_kind == "PassPriority":
            step = steps[0]
            plan.lease = _Lease(
                start=deepcopy(now), previous=deepcopy(now), until=step["until"],
                reason=directive["reason"], lease_id=plan_id,
                watch_opponents=True, max_passes=step["maxPasses"],
            )
        with self._lock:
            self._continuations[now["seat"]] = plan
        return True, "accepted"

    def _continue_plan(
        self, plan: _Continuation, observation: Mapping[str, Any],
    ) -> tuple[PilotChoice | None, str]:
        now = _visible_checkpoint(observation, watch_opponents=True)
        if (now is None or observation.get("pendingDecision") is not None
            or observation.get("terminated") is not False):
            return None, "decision-or-invalid-view"
        if plan.lease is not None:
            lease = plan.lease
            boundary = (
                lease.until == "phase_end"
                and (now["turn"], now["phase"]) != (lease.start["turn"], lease.start["phase"])
            ) or (
                lease.until == "next_own_main" and now["active"] == now["seat"]
                and now["step"] == "PRECOMBAT_MAIN"
                and (now["turn"] > lease.start["turn"]
                     or lease.start["step"] != "PRECOMBAT_MAIN")
            )
            if not boundary:
                if not self._continues(lease, now, observation):
                    return None, "wait-state-or-menu-changed"
                passed = _pass_and_mana_only(observation)
                if passed is None:
                    return None, "wait-menu-changed"
                lease.previous = deepcopy(now)
                lease.passes += 1
                with self._lock:
                    self._continuations[now["seat"]] = plan
                return ArgentumActionChoice(
                    action_id=passed["actionId"],
                    metadata={"declarativeContinuation": {
                        "planId": plan.plan_id, "step": plan.index, "type": "wait",
                        "ordinal": lease.passes,
                    }},
                ), "continued"
            if not self._continues(
                lease, now, observation, ignore_boundary=True, require_menu=False,
                ignore_limit=True,
            ):
                return None, "wait-changed-at-boundary"
            plan.lease = None
            plan.index += 1
            if plan.index >= len(plan.steps):
                return None, "boundary-reached"
        elif plan.last_action == "PlayLand":
            step = plan.steps[plan.index]
            if step["type"] != "cast":
                return None, "unexpected-land-continuation"
            pending = _PendingCast(
                before=plan.before, land_id=plan.last_card_id or "",
                cast_id=step["cardId"], reason=plan.reason,
                guarded_template=True,
                cast_zone=("Command" if step["cardId"] not in plan.before["hand"] else "Hand"),
            )
            offered = _ready_followup_cast(pending, now, observation)
            if offered is None or not _condition_matches(step["when"], now):
                return None, "land-transition-or-cast-offer-changed"
            if plan.index + 1 < len(plan.steps):
                plan.before = deepcopy(now)
                plan.last_action = "CastSpell"
                plan.last_card_id = step["cardId"]
                plan.index += 1
                with self._lock:
                    self._continuations[now["seat"]] = plan
            return ArgentumActionChoice(
                action_id=offered["actionId"],
                metadata={"declarativeContinuation": {
                    "planId": plan.plan_id,
                    "step": plan.index - 1 if plan.last_action == "CastSpell" else plan.index,
                    "type": "cast",
                    "cardId": step["cardId"],
                }},
            ), "completed"
        elif plan.last_action == "CastSpell":
            step = plan.steps[plan.index]
            if step["type"] != "wait":
                return None, "unexpected-cast-continuation"
            passed = _post_cast_wait_ready(plan, now, observation)
            if passed is None:
                return None, "cast-transition-or-pass-menu-changed"
            lease = _Lease(
                start=deepcopy(now), previous=deepcopy(now), until=step["until"],
                reason=plan.reason, lease_id=plan.plan_id,
                watch_opponents=True, passes=1, max_passes=step["maxPasses"],
            )
            plan.lease = lease
            with self._lock:
                self._continuations[now["seat"]] = plan
            return ArgentumActionChoice(
                action_id=passed["actionId"],
                metadata={"declarativeContinuation": {
                    "planId": plan.plan_id, "step": plan.index, "type": "wait",
                    "ordinal": 1,
                }},
            ), "continued"
        step = plan.steps[plan.index]
        offered = _fresh_named_action(observation, now, step)
        if offered is None:
            return None, "condition-or-native-offer-changed"
        step_index = plan.index
        if step["type"] == "playLand" and plan.index + 1 < len(plan.steps):
            plan.before = deepcopy(now)
            plan.last_action = "PlayLand"
            plan.last_card_id = step["cardId"]
            plan.index += 1
            with self._lock:
                self._continuations[now["seat"]] = plan
        return ArgentumActionChoice(
            action_id=offered["actionId"],
            metadata={"declarativeContinuation": {
                "planId": plan.plan_id, "step": step_index, "type": step["type"],
                "cardId": step["cardId"],
            }},
        ), "continued"

    def _continues(
        self, lease: _Lease, now: dict[str, Any], observation: Mapping[str, Any],
        *, ignore_boundary: bool = False, require_menu: bool = True,
        ignore_limit: bool = False,
    ) -> bool:
        if not ignore_limit and lease.passes >= min(self.max_passes, lease.max_passes):
            return False
        if lease.deferred_abilities is not None:
            if (
                now["turn"] != lease.start["turn"]
                or _nonmana_ability_keys(observation) != lease.deferred_abilities
                or _nonmana_ability_menu_digest(observation) != lease.deferred_menu_digest
            ):
                return False
        elif require_menu and _pass_and_mana_only(observation) is None:
            return False
        start, previous = lease.start, lease.previous
        if (
            now["turn"] < previous["turn"]
            or now["turn"] > start["turn"] + start["playerCount"]
            or now["playerCount"] != start["playerCount"]
        ):
            return False
        if not ignore_boundary and lease.until == "phase_end" and (now["turn"], now["phase"]) != (start["turn"], start["phase"]):
            return False
        if not ignore_boundary and lease.until == "next_own_main" and (
            now["active"] == now["seat"] and now["step"] == "PRECOMBAT_MAIN"
            and (now["turn"] > start["turn"] or start["step"] != "PRECOMBAT_MAIN")
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
