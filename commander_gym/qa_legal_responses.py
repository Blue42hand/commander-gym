"""Bounded QA fake responses from current masked native offers only.

This is a narrow keep/pass/bottom fixture, not a strategic pilot or legality
engine. Unsupported offers fail; ordinary pilot and native validators remain
responsible for freshness and authoritative application.
"""
import hashlib
import json
import threading
import time
from types import SimpleNamespace

PREFIX = "Return one JSON object for this observation:\n"
MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_CALLS = 128
MAX_SECONDS = 900
CALLBACK_SCHEMA_HASH = hashlib.sha256(b"argentum-ai-callback-seat-evidence-v2").hexdigest()


def _require(value):
    if not value:
        raise ValueError("qa_legal_fixture_unsupported")


def _observation(request):
    raw = request.get("input")
    _require(len(json.dumps(request, allow_nan=False).encode()) <= MAX_REQUEST_BYTES)
    if isinstance(raw, str):
        _require(raw.startswith(PREFIX))
        data = json.loads(raw[len(PREFIX):])
    elif isinstance(raw, list):
        from .cache_friendly_input import reconstruct_cache_friendly_observation
        data = reconstruct_cache_friendly_observation(raw)
    else:
        _require(False)
    _require(isinstance(data, dict))
    observation = data.get("observation", data)
    _require(isinstance(observation, dict)
             and observation.get("type") == "GameServerSeat"
             and observation.get("schemaHash") == CALLBACK_SCHEMA_HASH
             and isinstance(observation.get("stateDigest"), str)
             and len(observation["stateDigest"]) == 64)
    return observation


def legal_fixture_value(request):
    observation = _observation(request)
    format_ = request.get("text", {}).get("format", {})
    kind = observation.get("callbackKind")
    if kind == "chooseBottomCards":
        pending = observation.get("pendingDecision")
        _require(isinstance(pending, dict) and pending.get("kind") == "BottomCards")
        count, hand = pending.get("cardsToPutOnBottom"), pending.get("hand")
        _require(type(count) is int and 0 <= count <= 7
                 and isinstance(hand, list) and len(hand) <= 7
                 and all(isinstance(card, str) and card for card in hand)
                 and len(set(hand)) == len(hand) and count <= len(hand)
                 and format_.get("name") == "commander_gym_native_decision"
                 and pending.get("responseSpec") == {
                     "responseType": "CardsSelectedResponse",
                     "requiredFields": {"selectedCards": "ENTITY_ID_ARRAY"}})
        return {"channel": "decision", "response": {
            "type": "CardsSelectedResponse", "selectedCards": sorted(hand)[:count]}}
    _require(kind in {"decideMulligan", "chooseAction"}
             and observation.get("pendingDecision") is None
             and format_.get("name") == "commander_gym_native_action")
    desired = "KeepHand" if kind == "decideMulligan" else "PassPriority"
    actions = observation.get("legalActions")
    _require(isinstance(actions, list) and 0 < len(actions) <= 512)
    candidates = []
    for offer in actions:
        _require(isinstance(offer, dict))
        if offer.get("actionType", offer.get("kind")) != desired:
            continue
        _require(isinstance(offer.get("action"), dict)
                 and offer["action"].get("type") == desired
                 and offer.get("parameterSpec") == {"allowedFields": {}}
                 and isinstance(offer.get("semanticId"), str) and offer["semanticId"])
        candidates.append(offer)
    _require(len(candidates) == 1)
    semantic = candidates[0]["semanticId"]
    variants = format_.get("schema", {}).get("properties", {}).get("choice", {}).get("anyOf", [])
    matching = [variant for variant in variants
                if variant.get("properties", {}).get("semanticId", {}).get("const") == semantic]
    _require(len(matching) == 1
             and matching[0]["properties"].get("params", {}).get("properties") == {})
    return {"channel": "action", "choice": {"semanticId": semantic, "params": {}}}


class NativeLegalFixtureClient:
    def __init__(self):
        self.responses = self
        self.calls = 0
        self._started = time.monotonic()
        self._lock = threading.Lock()

    def create(self, **request):
        with self._lock:
            if self.calls >= MAX_CALLS or time.monotonic() - self._started >= MAX_SECONDS:
                raise ValueError("qa_legal_fixture_bound")
            self.calls += 1
            ordinal = self.calls
        try:
            value = legal_fixture_value(request)
        except (KeyError, TypeError, IndexError, RecursionError, ValueError):
            raise ValueError("qa_legal_fixture_unsupported") from None
        return SimpleNamespace(id=f"qa-legal-fixture-{ordinal}", output_text=json.dumps(value),
                               usage=SimpleNamespace(input_tokens=0, output_tokens=0, total_tokens=0))
