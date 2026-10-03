from __future__ import annotations

from types import SimpleNamespace
import unittest

from commander_gym.binding_resolver import _KnowledgeBoundPilot
from scripts.audit_scripted_privacy_capture import (
    _NeverRunPilot, _assert_case_coverage, _assert_library_visibility,
    _bound_observation, _count_exact,
)


class ScriptedPrivacyAuditTests(unittest.TestCase):
    def test_capture_at_bound_pilot_preserves_canonical_knowledge(self):
        adapter = SimpleNamespace(_pilot=_KnowledgeBoundPilot(_NeverRunPilot(), "canonical primer"))
        observation = {"knownDeck": {"cards": ["A"]}}
        self.assertEqual(_bound_observation(adapter, observation)["deckKnowledge"], "canonical primer")
        self.assertNotIn("deckKnowledge", observation)

    def test_hidden_identity_in_mapping_key_is_detected(self):
        hidden = "client-hidden-library-slot:private"
        self.assertEqual(_count_exact({hidden: {"public": [hidden]}}, {hidden}), 2)

    def test_unrevealed_named_library_card_is_rejected(self):
        state = {"cards": {"private-id": {"name": "Secret card"}}, "zones": [{
            "zoneId": {"zoneType": "Library", "ownerId": "viewer"},
            "cardIds": ["private-id"], "isVisible": False,
        }]}
        with self.assertRaisesRegex(AssertionError, "unrevealed library identity"):
            _assert_library_visibility(state, "viewer")
        state["zones"][0]["isVisible"] = True
        with self.assertRaisesRegex(AssertionError, "unrevealed library identity"):
            _assert_library_visibility(state, "opponent")

    def test_hidden_slot_card_object_and_name_are_rejected(self):
        hidden = "client-hidden-library-slot:private"
        state = {"cards": {hidden: {"name": "Secret card"}}, "zones": [{
            "zoneId": {"zoneType": "Library", "ownerId": "opponent"},
            "cardIds": [hidden], "isVisible": False,
        }]}
        with self.assertRaises(AssertionError):
            _assert_library_visibility(state, "viewer")

    def test_missing_critical_case_fails_closed(self):
        rows = [{"structuredDecision": "SelectCardsDecision", "combatPresent": True,
                 "visibleOwnLibraryIds": 2}, {"structuredDecision": "YesNoDecision"}]
        selected = [({"profileId": "a"},), ({"profileId": "b"},)]
        reconcealment = [{"previouslyKnownIds": 2}]
        _assert_case_coverage(rows, reconcealment, selected, ["a", "b"], 2)
        with self.assertRaises(AssertionError):
            _assert_case_coverage(rows, [], selected, ["a", "b"], 2)
        with self.assertRaises(AssertionError):
            _assert_case_coverage(rows[:1], reconcealment, selected, ["a", "b"], 2)
        with self.assertRaises(AssertionError):
            _assert_case_coverage([{**row, "visibleOwnLibraryIds": 0} for row in rows],
                                  reconcealment, selected, ["a", "b"], 2)


if __name__ == "__main__":
    unittest.main()
