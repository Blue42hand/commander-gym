"""Forced priority pass from a verified, complete native callback menu.

The certificate is process-local: JSON fields supplied by a caller or a provider
cannot confer it. The seat adapter issues it only after checking Argentum's full
enumerated observation against the callback. Native delivery still owns freshness.
"""

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping

from .captured_choices import CALLBACK_SCHEMA_HASH, SCHEMA_HASH
from .pilot_composition import SubsystemDecision
from .pilot_routing import AllUnaffordablePassHandler


def _digest(observation: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(
        observation, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


class _VerifiedNativeMenu(dict):
    """JSON-compatible observation whose complete contents were verified in-process."""

    def __init__(self, observation):
        super().__init__(observation)
        self.verified_digest = _digest(self)


@dataclass(frozen=True)
class NativeForcedPassSubsystem:
    name: str = "native-certified-forced-pass"
    version: str = "1"

    def try_choose(self, observation: Mapping[str, Any]) -> SubsystemDecision:
        if not isinstance(observation, _VerifiedNativeMenu):
            return SubsystemDecision(reason="native-menu-evidence-missing")
        if _digest(observation) != observation.verified_digest:
            return SubsystemDecision(reason="native-menu-evidence-mismatch")
        if (
            observation.get("schemaHash") not in {SCHEMA_HASH, CALLBACK_SCHEMA_HASH}
            or observation.get("type") != "GameServerSeat"
            or observation.get("callbackKind") not in {None, "chooseAction"}
            or observation.get("nativePaymentError") is not None
        ):
            return SubsystemDecision(reason="unsupported-native-callback")
        legal = observation.get("legalActions")
        if not isinstance(legal, list) or not legal:
            return SubsystemDecision(reason="uncertified-native-menu")
        # This is forced-pass certification, not the older optional mana-setup
        # policy. Every affordable alternative (including mana) stays strategic.
        for offer in legal:
            if not isinstance(offer, Mapping):
                return SubsystemDecision(reason="uncertified-native-menu")
            if offer.get("kind") != "PassPriority":
                if offer.get("affordable") is True or offer.get("isAffordable") is True:
                    return SubsystemDecision(reason="affordable-non-pass-action")
                if offer.get("affordable") is not False or offer.get("isAffordable") is not False:
                    return SubsystemDecision(reason="native-affordability-unknown")
        choice = AllUnaffordablePassHandler(
            allow_unaffordable_cycling=True, allow_unaffordable_flashback=True,
        ).choose(observation)
        if choice is None:
            return SubsystemDecision(reason="uncertified-native-menu")
        return SubsystemDecision(choice=choice, metadata={
            "reason": "native-only-affordable-pass",
            "nativeSchemaHash": observation["schemaHash"],
            "nativeStateDigest": observation["stateDigest"],
        })
