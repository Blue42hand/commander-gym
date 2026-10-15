from copy import deepcopy
import json
from pathlib import Path
import unittest

from commander_gym.cache_friendly_input import (
    cache_friendly_observation_input, reconstruct_cache_friendly_observation,
)
from commander_gym.observation_projection import compact_seat_observation, expand_seat_observation
from commander_gym.openai_responses_pilot import OpenAIResponsesPilot, _without_live_routing
from test_openai_responses_pilot import FakeClient, FakeResponse


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/observations/cache_layout_held_out_v1.json"


def held_out_cases():
    return json.loads(FIXTURE.read_text())["cases"]


class CacheFriendlyInputTests(unittest.TestCase):
    def test_held_out_information_and_menu_equivalence(self):
        for case in held_out_cases():
            for compact in (False, True):
                with self.subTest(case=case["case"], compact=compact):
                    original = case["observation"]
                    before = deepcopy(original)
                    view = compact_seat_observation(original) if compact else original
                    messages = cache_friendly_observation_input(view)
                    restored = reconstruct_cache_friendly_observation(messages)
                    if compact:
                        restored = expand_seat_observation(restored)
                    # JSON comparison distinguishes false/0 and includes every field.
                    self.assertEqual(json.dumps(restored, sort_keys=True), json.dumps(original, sort_keys=True))
                    self.assertEqual(restored["legalActions"], original["legalActions"])
                    self.assertEqual(restored["pendingDecision"], original["pendingDecision"])
                    self.assertEqual(original, before)
                    self.assertIn(original["deckKnowledge"], messages[0]["content"][0]["text"])
                    self.assertNotIn('"deckKnowledge"', messages[-1]["content"][0]["text"])

    def test_prefix_stability_at_every_history_boundary_and_empty_log(self):
        obs = held_out_cases()[0]["observation"]
        for length in (0, 1, 31, 32, 33, 127, 128, 129, 511, 512, 513):
            with self.subTest(length=length):
                obs["state"]["gameLog"] = [{"description": f"event {i}"} for i in range(length)]
                before = cache_friendly_observation_input(compact_seat_observation(obs))
                later = deepcopy(obs)
                later["stateDigest"] = "fresh-epoch"
                later["legalActions"] = list(reversed(later["legalActions"]))
                later["state"]["gameLog"].append({"description": "fresh public event"})
                after = cache_friendly_observation_input(compact_seat_observation(later))
                for index, message in enumerate(before):
                    if "prompt_cache_breakpoint" in message["content"][0]:
                        self.assertEqual(before[:index + 1], after[:index + 1])
                self.assertEqual(expand_seat_observation(reconstruct_cache_friendly_observation(after)), later)

    def test_missing_null_and_empty_fields_remain_distinct(self):
        for value in (None, "", {}, [], "one seat's knowledge"):
            obs = {"deckKnowledge": value, "knownDeck": value, "perspectivePlayerId": "a"}
            self.assertEqual(reconstruct_cache_friendly_observation(cache_friendly_observation_input(obs)), obs)
        for obs in ({}, {"state": {}}, {"state": {"gameLog": []}}):
            self.assertEqual(reconstruct_cache_friendly_observation(cache_friendly_observation_input(obs)), obs)

    def test_changed_seat_game_or_knowledge_invalidates_prefix_without_reuse(self):
        original = held_out_cases()[0]["observation"]
        first = cache_friendly_observation_input(original)
        for field, value in (("perspectivePlayerId", "another-seat"), ("gameId", "another-game"),
                             ("deckKnowledge", "revised authorized knowledge"), ("knownDeck", {})):
            changed = {**original, field: value}
            second = cache_friendly_observation_input(changed)
            self.assertNotEqual(first[0], second[0])
            self.assertEqual(reconstruct_cache_friendly_observation(second), changed)
        without = {key: value for key, value in original.items() if key not in ("knownDeck", "deckKnowledge")}
        self.assertEqual(reconstruct_cache_friendly_observation(cache_friendly_observation_input(without)), without)

    def test_each_seat_input_contains_only_current_authorized_view(self):
        cases = held_out_cases()
        for case in cases:
            obs = case["observation"]
            encoded = cache_friendly_observation_input(compact_seat_observation(obs))
            wire = json.dumps(encoded)
            for other in cases:
                if other is not case:
                    self.assertNotIn(other["observation"]["deckKnowledge"], wire)
                    self.assertNotIn(other["observation"]["perspectivePlayerId"] + "-card-1", wire)
            restored = expand_seat_observation(reconstruct_cache_friendly_observation(encoded))
            self.assertEqual(restored["state"]["players"][1],
                             {"id": "opponent", "life": 31, "handSize": 4, "librarySize": 65})
            self.assertNotIn("hand", restored["state"]["players"][1])

    def test_provider_settings_menu_and_live_routing_are_unchanged(self):
        for case in held_out_cases()[:2]:
            obs = case["observation"]
            answer = ({"channel": "action", "semanticId": "argentum-action-v1:pass", "params": {}}
                      if case["case"] == "priority" else
                      {"channel": "decision", "response": {"type": "TargetsResponse", "selectedTargets": {"0": ["seat-b-card-0"]}}})
            clients = [FakeClient(FakeResponse(json.dumps(answer))) for _ in range(2)]
            choices = [OpenAIResponsesPilot(client=client, model="offline-test", compact_model_observation=True,
                                           cache_friendly_history=cache).choose(obs)
                       for client, cache in zip(clients, (False, True))]
            baseline, cached = [client.responses.calls[0] for client in clients]
            self.assertEqual({k: v for k, v in baseline.items() if k != "input"},
                             {k: v for k, v in cached.items() if k not in ("input", "prompt_cache_options")})
            restored = expand_seat_observation(reconstruct_cache_friendly_observation(cached["input"]))
            self.assertEqual(restored, _without_live_routing(obs))
            if case["case"] == "priority":
                self.assertEqual(choices[0].action_id, choices[1].action_id)
                self.assertEqual(choices[1].action_id, 5)
            else:
                self.assertEqual(choices[0].response, choices[1].response)
                self.assertEqual(choices[1].response["decisionId"], "live-target-17")


if __name__ == "__main__":
    unittest.main()
