"""Narrow deterministic pilot for public lifecycle qualification fixtures.

This policy is intentionally not a general Magic agent. A manifest must opt into it
explicitly. It selects only actions present in the current Argentum observation and
fails closed outside the small land/cast/activate/attack/no-block surface exercised by
the public Krenko qualification pod.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .pilot import ArgentumActionChoice, PilotChoice, PilotContractError


def _legal_actions(observation: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    legal = observation.get("legalActions")
    if not isinstance(legal, list) or not legal:
        raise PilotContractError("qualification pilot requires non-empty legalActions")
    if not all(isinstance(action, Mapping) for action in legal):
        raise PilotContractError("qualification pilot received malformed legalActions")
    return legal


def _unique_kind(
    legal: list[Mapping[str, Any]], kind: str
) -> Mapping[str, Any] | None:
    matches = [action for action in legal if action.get("kind") == kind]
    if len(matches) > 1:
        raise PilotContractError(
            f"qualification pilot found ambiguous {kind} actions"
        )
    return matches[0] if matches else None


def _unique_non_mana_activation(
    legal: list[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    matches = [
        action
        for action in legal
        if action.get("kind") == "ActivateAbility"
        and action.get("isManaAbility") is not True
    ]
    if len(matches) > 1:
        raise PilotContractError(
            "qualification pilot found ambiguous non-mana ActivateAbility actions"
        )
    return matches[0] if matches else None


def _deterministic_equivalent_kind(
    legal: list[Mapping[str, Any]], kind: str
) -> Mapping[str, Any] | None:
    """Choose reproducibly only when repeated actions differ solely by identity."""

    matches = [action for action in legal if action.get("kind") == kind]
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]

    def policy_surface(action: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in action.items()
            if key not in {"actionId", "semanticId"}
        }

    expected = policy_surface(matches[0])
    if any(policy_surface(action) != expected for action in matches[1:]):
        raise PilotContractError(
            f"qualification pilot found non-equivalent {kind} actions"
        )
    if not all(isinstance(action.get("semanticId"), str) for action in matches):
        raise PilotContractError(
            f"qualification pilot cannot order equivalent {kind} actions"
        )
    return min(matches, key=lambda action: str(action["semanticId"]))


def _choice(
    action: Mapping[str, Any],
    *,
    params: Mapping[str, Any] | None = None,
    policy: str,
) -> ArgentumActionChoice:
    action_id = action.get("actionId")
    if type(action_id) is not int:
        raise PilotContractError("qualification action is missing integer actionId")
    return ArgentumActionChoice(
        action_id=action_id,
        params=dict(params or {}),
        metadata={
            "provider": "local",
            "policy": "qualification-aggro",
            "policyDecision": policy,
        },
    )


@dataclass(frozen=True)
class QualificationAggroPilot:
    """Aggressive, fully explicit policy for the synthetic Krenko lifecycle pod."""

    name: str = "qualification-aggro"
    version: str = "1"

    def choose(self, observation: Mapping[str, Any]) -> PilotChoice:
        pending = observation.get("pendingDecision")
        if isinstance(pending, Mapping) and pending.get("requiresStructuredResponse") is True:
            raise PilotContractError(
                "qualification pilot does not support structured decisions"
            )

        legal = _legal_actions(observation)

        attack = _unique_kind(legal, "DeclareAttackers")
        if attack is not None:
            attackers = attack.get("validAttackers")
            targets = attack.get("validAttackTargets")
            if not isinstance(attackers, list) or not all(
                isinstance(value, str) and value for value in attackers
            ):
                raise PilotContractError("DeclareAttackers is missing validAttackers")
            if not isinstance(targets, list) or not all(
                isinstance(value, str) and value for value in targets
            ):
                raise PilotContractError("DeclareAttackers is missing validAttackTargets")
            if attackers and not targets:
                raise PilotContractError("attackers exist without a legal attack target")
            params = (
                {"attackers": {attacker: targets[0] for attacker in attackers}}
                if attackers
                else {}
            )
            return _choice(attack, params=params, policy="attack-all-first-target")

        block = _unique_kind(legal, "DeclareBlockers")
        if block is not None:
            mandatory = block.get("mandatoryBlockerAssignments")
            if isinstance(mandatory, Mapping) and mandatory:
                raise PilotContractError(
                    "qualification pilot cannot resolve mandatory blocker assignments"
                )
            return _choice(block, policy="declare-no-blockers")

        land = _deterministic_equivalent_kind(legal, "PlayLand")
        if land is not None:
            if land.get("affordable") is False:
                raise PilotContractError("qualification land action is not affordable")
            if (
                land.get("hasXCost") is True
                or land.get("requiresDamageDistribution") is True
            ):
                raise PilotContractError(
                    "qualification pilot does not support parameterized PlayLand"
                )
            if land.get("targetEntityIds"):
                raise PilotContractError(
                    "qualification pilot does not support targeted PlayLand"
                )
            return _choice(land, policy="play-land-deterministic-equivalent")

        for kind, policy in (("CastSpell", "cast-spell"),):
            action = _unique_kind(legal, kind)
            if action is None:
                continue
            if action.get("affordable") is False:
                continue
            if action.get("hasXCost") is True or action.get("requiresDamageDistribution") is True:
                raise PilotContractError(
                    f"qualification pilot does not support parameterized {kind}"
                )
            if action.get("targetEntityIds"):
                raise PilotContractError(
                    f"qualification pilot does not support targeted {kind}"
                )
            return _choice(action, policy=policy)

        activation = _unique_non_mana_activation(legal)
        if activation is not None:
            if activation.get("affordable") is False:
                raise PilotContractError(
                    "qualification non-mana activation is not affordable"
                )
            if (
                activation.get("hasXCost") is True
                or activation.get("requiresDamageDistribution") is True
            ):
                raise PilotContractError(
                    "qualification pilot does not support parameterized ActivateAbility"
                )
            if activation.get("targetEntityIds"):
                raise PilotContractError(
                    "qualification pilot does not support targeted ActivateAbility"
                )
            return _choice(activation, policy="activate-non-mana-ability")

        decision_options = [
            action for action in legal if action.get("isDecisionOption") is True
        ]
        keep_options = [
            action
            for action in decision_options
            if "keep" in str(action.get("description", "")).lower()
        ]
        if len(keep_options) == 1:
            return _choice(keep_options[0], policy="keep-opening-hand")
        if len(decision_options) == 1:
            return _choice(decision_options[0], policy="forced-decision-option")
        if decision_options:
            raise PilotContractError(
                "qualification pilot found an unsupported strategic decision option"
            )

        passed = _unique_kind(legal, "PassPriority")
        if passed is not None:
            return _choice(passed, policy="pass-no-supported-progress")

        kinds = sorted(
            {
                str(action.get("kind"))
                for action in legal
                if action.get("kind") is not None
            }
        )
        raise PilotContractError(
            f"qualification pilot does not support current action kinds: {kinds}"
        )
