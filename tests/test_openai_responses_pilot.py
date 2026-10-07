import hashlib
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from commander_gym.openai_responses_pilot import (
    MODEL_IO_SCHEMA_VERSION,
    EXPLICIT_NAMED_WAIT_INSTRUCTIONS,
    OpenAIResponsesPilot,
    OpenAIResponsesPilotError,
)
from commander_gym.pilot import (
    ArgentumActionChoice,
    ArgentumDecisionChoice,
    choose_for_observation,
)
from commander_gym.openai_run_budget import OpenAIRunBudget
from commander_gym.observation_projection import expand_seat_observation
from commander_gym.cache_friendly_input import (
    cache_friendly_observation_input,
    reconstruct_cache_friendly_observation,
)


class FakeResponse:
    def __init__(
        self,
        output,
        *,
        response_id="resp-1",
        status="completed",
        error=None,
        incomplete_details=None,
    ):
        self.id = response_id
        self.model = "provider-model-snapshot"
        self.status = status
        self.error = error
        self.incomplete_details = incomplete_details
        self.output_text = output
        self.usage = {"input_tokens": 100, "output_tokens": 20}


class FakeResponses:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        if isinstance(self.response, list):
            return self.response.pop(0)
        return self.response


class FakeClient:
    def __init__(self, response=None, error=None):
        self.responses = FakeResponses(response=response, error=error)


class FakeHttpError(Exception):
    def __init__(self, status_code):
        super().__init__("private provider detail must not escape")
        self.status_code = status_code


class SequenceResponses:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def action_observation():
    return {
        "type": "Game",
        "schemaHash": "argentum-gym-contract@v1.7-semantic-state-provenance",
        "stateDigest": "state-a",
        "perspectivePlayerId": "player-1",
        "agentToAct": "player-1",
        "pendingDecision": None,
        "legalActions": [
            {
                "actionId": 2,
                "semanticId": "argentum-action-v1:pass",
                "kind": "PassPriority",
                "description": "Pass priority",
                "affordable": True,
            },
            {
                "actionId": 7,
                "semanticId": "argentum-action-v1:attack",
                "kind": "DeclareAttackers",
                "description": "Declare attackers",
                "affordable": True,
                "validAttackers": ["creature-1"],
                "validAttackTargets": ["player-2"],
            },
        ],
        "terminated": False,
    }


def structured_observation():
    return {
        "type": "Game",
        "schemaHash": "argentum-gym-contract@v1.7-semantic-state-provenance",
        "stateDigest": "state-structured",
        "perspectivePlayerId": "player-1",
        "agentToAct": "player-1",
        "pendingDecision": {
            "decisionId": "routing-live-9",
            "semanticId": "argentum-decision-v1:choose-targets",
            "kind": "CHOOSE_TARGETS",
            "playerId": "player-1",
            "prompt": "Choose one target",
            "requiresStructuredResponse": True,
            "responseSpec": {
                "responseType": "TargetsResponse",
                "requiredFields": {"selectedTargets": "MAP"},
            },
            "legalTargets": {"0": ["target-1"]},
        },
        "legalActions": [],
        "terminated": False,
    }


class OpenAIResponsesPilotTests(unittest.TestCase):
    def test_cache_friendly_layout_is_opt_in_and_preserves_masked_view(self):
        obs = action_observation()
        obs["knownDeck"] = {"cards": [{"name": "Visible deck card"}]}
        obs["state"] = {"deck": {"commander": "Visible commander"},
                        "gameLog": [{"description": f"event {i}"} for i in range(130)],
                        "cards": {"visible": {"id": "visible", "isTapped": False}}}
        output = json.dumps({"channel": "action", "semanticId": "argentum-action-v1:pass",
                             "params": {}})
        client = FakeClient(FakeResponse(output))
        pilot = OpenAIResponsesPilot(client=client, model="gpt-test",
                                    compact_model_observation=True,
                                    cache_friendly_history=True)
        pilot.choose(obs)
        request = client.responses.calls[0]
        self.assertFalse(request["store"])
        self.assertEqual(request["prompt_cache_options"], {"mode": "explicit"})
        self.assertEqual(sum("prompt_cache_breakpoint" in msg["content"][0]
                             for msg in request["input"]), 3)
        restored = expand_seat_observation(
            reconstruct_cache_friendly_observation(request["input"])
        )
        self.assertEqual(restored["state"], obs["state"])
        self.assertEqual(restored["knownDeck"], obs["knownDeck"])
        self.assertEqual(restored["legalActions"][0].get("actionId"), None)
        baseline = FakeClient(FakeResponse(output))
        OpenAIResponsesPilot(client=baseline, model="gpt-test",
                             compact_model_observation=True).choose(obs)
        self.assertIsInstance(baseline.responses.calls[0]["input"], str)
        self.assertNotIn("prompt_cache_options", baseline.responses.calls[0])
        self.assertEqual(request["text"], baseline.responses.calls[0]["text"])
        self.assertEqual(request["instructions"], baseline.responses.calls[0]["instructions"])

    def test_cache_friendly_history_keeps_fixed_prefix_and_missing_log(self):
        obs = action_observation()
        self.assertEqual(reconstruct_cache_friendly_observation(
            cache_friendly_observation_input(obs)),
            {key: value for key, value in obs.items()})
        obs["state"] = {"gameLog": [{"description": f"event {i}"} for i in range(33)]}
        first = cache_friendly_observation_input(obs)
        obs["state"]["gameLog"].append({"description": "event 33"})
        second = cache_friendly_observation_input(obs)
        self.assertEqual(first[:2], second[:2])
        self.assertEqual(reconstruct_cache_friendly_observation(second), obs)

    def test_cache_friendly_retry_keeps_original_masked_history(self):
        obs = action_observation()
        obs["state"] = {"gameLog": [{"description": "masked event"}]}
        valid = json.dumps({"channel": "action", "semanticId": "argentum-action-v1:pass",
                            "params": {}})
        client = FakeClient([FakeResponse("{}"), FakeResponse(valid)])
        choice = OpenAIResponsesPilot(client=client, model="gpt-test",
                                      cache_friendly_history=True).choose(obs)
        first, retry = client.responses.calls
        self.assertEqual(choice.metadata["retryCount"], 1)
        self.assertEqual(retry["input"][:-1], first["input"])
        self.assertIn("previous response was invalid", retry["input"][-1]["content"])
        self.assertEqual(reconstruct_cache_friendly_observation(first["input"]),
                         {**obs, "legalActions": [
                             {key: value for key, value in action.items() if key != "actionId"}
                             for action in obs["legalActions"]]})

    def test_saved_turn_13_attack_target_typo_retries_against_native_offer(self):
        observation = action_observation()
        observation["legalActions"] = [{
            "actionId": 0, "semanticId": "argentum-action-v1:declare-attackers",
            "kind": "DeclareAttackers", "parameterSpec": {
                "allowedFields": {"attackers": "ENTITY_ID_MAP"},
            },
            "validAttackers": ["e212", "e217", "e218", "e219", "e220", "e221"],
            "validAttackTargets": ["ai-f65bc7f7"],
        }]
        def answer(target):
            return json.dumps({"channel": "action", "choice": {
                "semanticId": "argentum-action-v1:declare-attackers",
                "params": {"attackers": {"e212": target}},
            }})
        client = FakeClient([
            FakeResponse(answer("ai-f65bcf7f7")),  # Saved failure: one extra 'f'.
            FakeResponse(answer("ai-f65bc7f7")),
        ])
        choice = OpenAIResponsesPilot(client=client, model="gpt-test").choose(observation)
        self.assertEqual(choice.params, {"attackers": {"e212": "ai-f65bc7f7"}})
        self.assertEqual(choice.metadata["retryCount"], 1)
        self.assertIn("validAttackTargets", client.responses.calls[1]["input"])
        attacker_schema = client.responses.calls[0]["text"]["format"]["schema"]["properties"] \
            ["choice"]["anyOf"][0]["properties"]["params"]["properties"]["attackers"]
        self.assertEqual(attacker_schema["properties"]["e212"]["enum"], ["ai-f65bc7f7"])
        self.assertFalse(attacker_schema["additionalProperties"])

    def test_attackers_reject_unoffered_or_stale_entity_ids(self):
        observation = action_observation()
        observation["legalActions"] = [{
            "actionId": 0, "semanticId": "native-attack", "kind": "DeclareAttackers",
            "parameterSpec": {"allowedFields": {"attackers": "ENTITY_ID_MAP"}},
            "validAttackers": ["current-creature"],
            "validAttackTargets": ["current-opponent"],
        }]
        for attackers in (
            {"old-creature": "current-opponent"},
            {"current-creature": "old-opponent"},
            {"current-creature": "current-oppnonent"},
        ):
            with self.subTest(attackers=attackers):
                client = FakeClient(FakeResponse(json.dumps({"channel": "action", "choice": {
                    "semanticId": "native-attack", "params": {"attackers": attackers},
                }})))
                with self.assertRaisesRegex(OpenAIResponsesPilotError, "outside native validAttackers"):
                    OpenAIResponsesPilot(client=client, model="gpt-test", max_attempts=1).choose(observation)
                self.assertEqual(len(client.responses.calls), 1)

    def test_malformed_native_attack_candidates_fail_before_provider_call(self):
        for field, value in (
            ("validAttackers", "current-creature"),
            ("validAttackTargets", [None]),
        ):
            with self.subTest(field=field, value=value):
                observation = action_observation()
                observation["legalActions"] = [{
                    "actionId": 0, "semanticId": "native-attack", "kind": "DeclareAttackers",
                    "parameterSpec": {"allowedFields": {"attackers": "ENTITY_ID_MAP"}},
                    "validAttackers": ["current-creature"],
                    "validAttackTargets": ["current-opponent"],
                    field: value,
                }]
                client = FakeClient(FakeResponse("{}"))
                with self.assertRaisesRegex(OpenAIResponsesPilotError, "attacker candidates are malformed"):
                    OpenAIResponsesPilot(client=client, model="gpt-test").choose(observation)
                self.assertEqual(client.responses.calls, [])

    def test_attackers_accept_offered_planeswalker_or_battle_target(self):
        observation = action_observation()
        observation["legalActions"] = [{
            "actionId": 0, "semanticId": "native-attack", "kind": "DeclareAttackers",
            "parameterSpec": {"allowedFields": {"attackers": "ENTITY_ID_MAP"}},
            "validAttackers": ["creature-1"],
            "validAttackTargets": ["opponent-1", "planeswalker-1", "battle-1"],
        }]
        for target in ("planeswalker-1", "battle-1"):
            with self.subTest(target=target):
                client = FakeClient(FakeResponse(json.dumps({"channel": "action", "choice": {
                    "semanticId": "native-attack",
                    "params": {"attackers": {"creature-1": target}},
                }})))
                choice = OpenAIResponsesPilot(client=client, model="gpt-test").choose(observation)
                self.assertEqual(choice.params["attackers"], {"creature-1": target})

    def test_no_defender_null_offer_only_allows_empty_attacker_map(self):
        observation = action_observation()
        observation["legalActions"] = [{
            "actionId": 0, "semanticId": "native-attack", "kind": "DeclareAttackers",
            "parameterSpec": {"allowedFields": {"attackers": "ENTITY_ID_MAP"}},
            "validAttackers": ["creature-1"], "validAttackTargets": None,
        }]
        def answer(attackers):
            return json.dumps({"channel": "action", "choice": {
                "semanticId": "native-attack", "params": {"attackers": attackers},
            }})
        client = FakeClient([FakeResponse(answer({"creature-1": "old-opponent"})),
                             FakeResponse(answer({}))])
        choice = OpenAIResponsesPilot(client=client, model="gpt-test").choose(observation)
        self.assertEqual(choice.params["attackers"], {})
        self.assertEqual(choice.metadata["retryCount"], 1)
        attacker_schema = client.responses.calls[0]["text"]["format"]["schema"]["properties"] \
            ["choice"]["anyOf"][0]["properties"]["params"]["properties"]["attackers"]
        self.assertEqual(attacker_schema, {
            "type": "object", "properties": {}, "additionalProperties": False,
        })

    def test_native_block_pairs_constrain_schema_and_retry_an_impossible_pair(self):
        obs = action_observation()
        obs["legalActions"] = [{
            "actionId": 4, "semanticId": "argentum-action-v1:declare-blockers",
            "kind": "DeclareBlockers", "actionType": "DeclareBlockers",
            "parameterSpec": {"allowedFields": {"blockers": "ENTITY_ID_ARRAY_MAP"}},
            "validBlockers": ["drake", "lion"],
            "validBlockTargets": {"drake": ["bear"], "lion": ["piledriver", "bear"]},
            "blockerMaxBlockCounts": {"drake": 1, "lion": 1},
        }]
        invalid = {"channel": "action", "choice": {
            "semanticId": "argentum-action-v1:declare-blockers",
            "params": {"blockers": {"drake": ["piledriver"]}},
        }}
        valid = {"channel": "action", "choice": {
            "semanticId": "argentum-action-v1:declare-blockers",
            "params": {"blockers": {"drake": ["bear"]}},
        }}
        client = FakeClient([FakeResponse(json.dumps(invalid)), FakeResponse(json.dumps(valid))])
        choice = OpenAIResponsesPilot(client=client, model="gpt-test").choose(obs)
        self.assertEqual(choice.action_id, 4)
        self.assertEqual(choice.params, valid["choice"]["params"])
        self.assertEqual(choice.metadata["retryCount"], 1)
        self.assertEqual(len(client.responses.calls), 2)
        block_schema = client.responses.calls[0]["text"]["format"]["schema"]["properties"] \
            ["choice"]["anyOf"][0]["properties"]["params"]["properties"]["blockers"]
        self.assertEqual(block_schema["properties"]["drake"]["items"]["enum"], ["bear"])
        self.assertEqual(block_schema["properties"]["lion"]["items"]["enum"],
                         ["piledriver", "bear"])
        self.assertFalse(block_schema["additionalProperties"])
        self.assertIn("validBlockTargets", client.responses.calls[0]["input"])
        self.assertIn("outside native validBlockTargets", client.responses.calls[1]["input"])

    def test_native_block_pair_contract_fails_closed_when_malformed(self):
        obs = action_observation()
        obs["legalActions"] = [{
            "actionId": 4, "semanticId": "argentum-action-v1:declare-blockers",
            "kind": "DeclareBlockers", "actionType": "DeclareBlockers",
            "parameterSpec": {"allowedFields": {"blockers": "ENTITY_ID_ARRAY_MAP"}},
            "validBlockTargets": {"drake": "piledriver"},
        }]
        client = FakeClient(FakeResponse("{}"))
        with self.assertRaisesRegex(OpenAIResponsesPilotError, "validBlockTargets is malformed"):
            OpenAIResponsesPilot(client=client, model="gpt-test").choose(obs)
        self.assertEqual(client.responses.calls, [])

    def test_native_global_blocker_cap_retries_two_distinct_blockers(self):
        obs = action_observation()
        obs["legalActions"] = [{
            "actionId": 0, "semanticId": "argentum-action-v1:declare-blockers",
            "kind": "DeclareBlockers", "actionType": "DeclareBlockers",
            "parameterSpec": {"allowedFields": {"blockers": "ENTITY_ID_ARRAY_MAP"}},
            "validBlockTargets": {"e270": ["e0"], "e453": ["e0"]},
            "maxTotalBlockers": 1,
        }]
        invalid = {"channel": "action", "choice": {
            "semanticId": "argentum-action-v1:declare-blockers",
            "params": {"blockers": {"e270": ["e0"], "e453": ["e0"]}},
        }}
        valid = {"channel": "action", "choice": {
            "semanticId": "argentum-action-v1:declare-blockers",
            "params": {"blockers": {"e270": ["e0"]}},
        }}
        client = FakeClient([FakeResponse(json.dumps(invalid)), FakeResponse(json.dumps(valid))])
        choice = OpenAIResponsesPilot(client=client, model="gpt-test").choose(obs)
        self.assertEqual(choice.params, valid["choice"]["params"])
        self.assertEqual(choice.metadata["retryCount"], 1)
        schema = client.responses.calls[0]["text"]["format"]["schema"]["properties"] \
            ["choice"]["anyOf"][0]["properties"]["params"]["properties"]["blockers"]
        self.assertIn("at most 1 distinct blocker", schema["description"])
        self.assertIn("exceeds native maxTotalBlockers", client.responses.calls[1]["input"])

    def test_native_global_blocker_cap_rejects_malformed_offer_before_model(self):
        obs = action_observation()
        obs["legalActions"] = [{
            "actionId": 0, "semanticId": "argentum-action-v1:declare-blockers",
            "kind": "DeclareBlockers", "actionType": "DeclareBlockers",
            "parameterSpec": {"allowedFields": {"blockers": "ENTITY_ID_ARRAY_MAP"}},
            "validBlockTargets": {"e270": ["e0"]},
            "maxTotalBlockers": True,
        }]
        client = FakeClient(FakeResponse("{}"))
        with self.assertRaisesRegex(OpenAIResponsesPilotError, "maxTotalBlockers is malformed"):
            OpenAIResponsesPilot(client=client, model="gpt-test").choose(obs)
        self.assertEqual(client.responses.calls, [])

    def test_compact_model_view_preserves_exact_native_observation_in_provenance(self):
        obs = action_observation()
        obs["state"] = {"cards": {
            "visible": {"id": "visible", "name": "Card", "isTapped": False,
                        "targets": [], "rider": None},
        }}
        output = json.dumps({"channel": "action", "semanticId": "argentum-action-v1:pass",
                             "params": {}})
        client = FakeClient(FakeResponse(output))
        pilot = OpenAIResponsesPilot(client=client, model="gpt-test",
                                    compact_model_observation=True)
        choice = pilot.choose(obs)
        request = client.responses.calls[0]
        compact = json.loads(request["input"].split("\n", 1)[1])
        restored = expand_seat_observation(compact)
        self.assertEqual(restored["state"], obs["state"])
        self.assertEqual(restored["legalActions"][0].get("actionId"), None)
        self.assertEqual(obs["legalActions"][0]["actionId"], 2)
        self.assertEqual(choice.action_id, 2)
        self.assertEqual(choice.metadata["modelIo"]["attempts"][0]["request"], request)
        self.assertIn("sparse-cards-v1", request["instructions"])

    def test_default_prompt_retains_qualified_provider_identity(self):
        pilot = OpenAIResponsesPilot(client=FakeClient(), model="gpt-test")
        self.assertEqual(
            hashlib.sha256(pilot.instructions.encode()).hexdigest(),
            "7222009a07b4509944793052c74beba9d1bdb7f5b80c4ab710238a7cdc0b640c",
        )

    def test_priority_delegation_requires_opt_in_and_exact_pass(self):
        directive = {"until": "phase_end", "reason": "Reviewed this main phase"}
        output = json.dumps({
            "channel": "action", "semanticId": "argentum-action-v1:pass",
            "params": {}, "priorityDelegation": directive,
        })
        client = FakeClient(FakeResponse(output))
        pilot = OpenAIResponsesPilot(
            client=client, model="gpt-test", allow_priority_delegation=True,
        )
        choice = pilot.choose(action_observation())
        self.assertEqual(choice.metadata["priorityDelegation"], directive)
        self.assertIn("priorityDelegation", client.responses.calls[0]["instructions"])

        rejected = OpenAIResponsesPilot(
            client=FakeClient(FakeResponse(output)), model="gpt-test", max_attempts=1,
        )
        with self.assertRaisesRegex(OpenAIResponsesPilotError, "not enabled"):
            rejected.choose(action_observation())

        wrong_action = json.dumps({
            "channel": "action", "semanticId": "argentum-action-v1:attack",
            "params": {"attackers": {}}, "priorityDelegation": directive,
        })
        rejected = OpenAIResponsesPilot(
            client=FakeClient(FakeResponse(wrong_action)), model="gpt-test",
            max_attempts=1, allow_priority_delegation=True,
        )
        with self.assertRaisesRegex(OpenAIResponsesPilotError, "requires an exact current PassPriority"):
            rejected.choose(action_observation())

    def test_named_forge_wait_requires_versioned_opt_in(self):
        directive = {"until": "turn_end", "reason": "Defer draw ability this turn",
                     "deferAbilities": [{"sourceId": "stone", "abilityId": "draw"}]}
        output = json.dumps({"channel": "action", "semanticId": "argentum-action-v1:pass",
                             "params": {}, "priorityDelegation": directive})
        client = FakeClient(FakeResponse(output))
        pilot = OpenAIResponsesPilot(client=client, model="gpt-test",
                                    allow_priority_delegation=True, allow_named_deferrals=True)
        self.assertEqual(pilot.choose(action_observation()).metadata["priorityDelegation"], directive)
        self.assertIn("deferAbilities", client.responses.calls[0]["instructions"])
        legacy = OpenAIResponsesPilot(client=FakeClient(FakeResponse(output)), model="gpt-test",
                                      max_attempts=1, allow_priority_delegation=True)
        with self.assertRaises(OpenAIResponsesPilotError):
            legacy.choose(action_observation())

    def test_forge_then_cast_requires_exact_land_selection(self):
        obs = action_observation()
        obs["legalActions"][0]["kind"] = "PlayLand"
        for action in obs["legalActions"]:
            action["parameterSpec"] = {"allowedFields": {}}
        output = json.dumps({"channel": "action", "semanticId": "argentum-action-v1:pass",
                             "params": {}, "thenCast": {"cardId": "hand-2", "reason": "Cast after land"}})
        client = FakeClient(FakeResponse(output))
        pilot = OpenAIResponsesPilot(client=client, model="gpt-test",
                                    allow_priority_delegation=True, allow_named_deferrals=True)
        self.assertEqual(pilot.choose(obs).metadata["thenCast"]["cardId"], "hand-2")
        self.assertIn("thenCast", client.responses.calls[0]["text"]["format"]["schema"]["properties"])

        guarded_client = FakeClient(FakeResponse(output))
        guarded = OpenAIResponsesPilot(
            client=guarded_client, model="gpt-test", allow_priority_delegation=True,
            allow_named_deferrals=True, guarded_then_cast_templates=True,
        )
        self.assertEqual(guarded.choose(obs).metadata["thenCast"]["cardId"], "hand-2")
        instructions = guarded_client.responses.calls[0]["instructions"]
        self.assertIn("hand or command zone", instructions)
        self.assertIn("Never guess a payment or target", instructions)

    def test_declarative_continuation_is_versioned_and_rejects_bad_steps(self):
        obs = action_observation()
        obs["legalActions"][0]["kind"] = "PlayLand"
        for action in obs["legalActions"]:
            action["parameterSpec"] = {"allowedFields": {}}
        step = {"type": "cast", "cardId": "hand-2",
                "when": {"phase": "PRECOMBAT_MAIN", "stackEmpty": True},
                "params": {}}
        output = {"channel": "action", "semanticId": "argentum-action-v1:pass",
                  "params": {}, "continuation": {
                      "reason": "Play then cast", "steps": [step],
                  }}
        client = FakeClient(FakeResponse(json.dumps(output)))
        pilot = OpenAIResponsesPilot(client=client, model="gpt-test",
                                     allow_declarative_continuation=True)
        self.assertEqual(pilot.choose(obs).metadata["continuation"]["steps"], [step])
        self.assertIn("continuation", client.responses.calls[0]["text"]["format"]
                      ["schema"]["properties"])
        self.assertIn("Choose at most one of continuation", client.responses.calls[0]["instructions"])
        with self.assertRaises(OpenAIResponsesPilotError):
            OpenAIResponsesPilot(client=FakeClient(FakeResponse(json.dumps(output))),
                                 model="gpt-test").choose(obs)
        for bad in (
            {**step, "params": {"targets": ["other"]}},
            {**step, "when": {}},
            {**step, "when": {"stackEmpty": "true"}},
            {**step, "when": {"phase": "MAGIC_PHASE"}},
            {**step, "cardId": ""},
            {**step, "extra": True},
            {"type": []},
            {"type": {"kind": "wait"}},
            {"type": "wait", "until": {}, "maxPasses": 1},
            {"type": "wait", "until": [], "maxPasses": 1},
            {"type": "python", "code": "pass"},
        ):
            with self.subTest(bad=bad):
                malformed = {**output, "continuation": {
                    "reason": "Play then cast", "steps": [bad],
                }}
                with self.assertRaises(OpenAIResponsesPilotError):
                    OpenAIResponsesPilot(
                        client=FakeClient(FakeResponse(json.dumps(malformed))),
                        model="gpt-test", allow_declarative_continuation=True,
                    ).choose(obs)

        for bad in ({"type": []}, {"type": "wait", "until": {}, "maxPasses": 1}):
            with self.subTest(retry_bad=bad):
                invalid = {**output, "continuation": {
                    "reason": "Invalid then retry", "steps": [bad],
                }}
                valid = {"channel": "action", "semanticId": "argentum-action-v1:pass",
                         "params": {}}
                client = FakeClient([
                    FakeResponse(json.dumps(invalid), response_id="bad"),
                    FakeResponse(json.dumps(valid), response_id="good"),
                ])
                recovered = OpenAIResponsesPilot(
                    client=client, model="gpt-test", max_attempts=2,
                    allow_declarative_continuation=True,
                ).choose(obs)
                self.assertEqual(len(client.responses.calls), 2)
                self.assertEqual(recovered.metadata["retryCount"], 1)
                attempts = recovered.metadata["modelIo"]["attempts"]
                self.assertEqual(len(attempts), 2)
                self.assertIn("validationError", attempts[0]["response"])
        malformed_native = action_observation()
        malformed_native["legalActions"][0]["kind"] = []
        for action in malformed_native["legalActions"]:
            action["parameterSpec"] = {"allowedFields": {}}
        with self.assertRaises(OpenAIResponsesPilotError):
            OpenAIResponsesPilot(
                client=FakeClient(FakeResponse(json.dumps(output))),
                model="gpt-test", max_attempts=1,
                allow_declarative_continuation=True,
            ).choose(malformed_native)

    def test_native_action_schema_exposes_delegation_only_for_opt_in(self):
        obs = action_observation()
        for action in obs["legalActions"]:
            action["parameterSpec"] = {"allowedFields": {}}
        output = json.dumps({
            "channel": "action", "choice": {
                "semanticId": "argentum-action-v1:pass", "params": {},
            },
        })
        for enabled in (False, True):
            client = FakeClient(FakeResponse(output))
            OpenAIResponsesPilot(
                client=client, model="gpt-test", allow_priority_delegation=enabled,
            ).choose(obs)
            properties = client.responses.calls[0]["text"]["format"]["schema"]["properties"]
            self.assertEqual("priorityDelegation" in properties, enabled)

    def test_bounded_provider_request_is_recorded_in_exact_model_io(self):
        with TemporaryDirectory() as temporary:
            budget = OpenAIRunBudget(Path(temporary) / "budget.json", 5, initialize_new_ledger=True)
            client = FakeClient(FakeResponse(json.dumps({
                "channel": "action", "semanticId": "argentum-action-v1:pass", "params": {},
            })))
            choice = OpenAIResponsesPilot(
                client=client, model="gpt-6-luna", budget=budget,
            ).choose(action_observation())

            self.assertEqual(client.responses.calls[0]["max_output_tokens"], 2048)
            self.assertEqual(
                choice.metadata["modelIo"]["attempts"][0]["request"],
                client.responses.calls[0],
            )
            self.assertEqual(budget.snapshot()["requests"], 1)
            self.assertEqual(budget.snapshot()["inputTokens"], 100)
            self.assertEqual(budget.snapshot()["outputTokens"], 20)

    def test_initial_budget_cap_rejection_has_no_provider_attempt(self):
        with TemporaryDirectory() as temporary:
            budget = OpenAIRunBudget(
                Path(temporary) / "budget.json", 5, max_requests=0,
                initialize_new_ledger=True,
            )
            client = FakeClient(FakeResponse("{}"))
            pilot = OpenAIResponsesPilot(client=client, model="gpt-6-luna", budget=budget)
            with self.assertRaises(OpenAIResponsesPilotError) as caught:
                pilot.choose(action_observation())
            self.assertIsNone(caught.exception.model_io)
            self.assertEqual(client.responses.calls, [])
            self.assertEqual(budget.snapshot()["requests"], 0)

    def test_retry_cap_preserves_first_invalid_provider_attempt(self):
        with TemporaryDirectory() as temporary:
            budget = OpenAIRunBudget(
                Path(temporary) / "budget.json", 5, max_requests=1,
                initialize_new_ledger=True,
            )
            invalid = '{"channel":"action","semanticId":"not-offered","params":{}}'
            client = FakeClient([FakeResponse(invalid), FakeResponse("{}")])
            pilot = OpenAIResponsesPilot(
                client=client, model="gpt-6-luna", budget=budget, max_attempts=2,
            )
            with self.assertRaisesRegex(OpenAIResponsesPilotError, "request limit") as caught:
                pilot.choose(action_observation())
            evidence = caught.exception.model_io
            self.assertEqual(len(client.responses.calls), 1)
            self.assertEqual(budget.snapshot()["requests"], 1)
            self.assertEqual(evidence["selectedAttempt"], None)
            self.assertEqual(len(evidence["attempts"]), 1)
            self.assertEqual(evidence["attempts"][0]["response"]["outputText"], invalid)
            self.assertIn("validationError", evidence["attempts"][0]["response"])

    def test_postdispatch_budget_failure_records_response_not_transport_error(self):
        with TemporaryDirectory() as temporary:
            budget = OpenAIRunBudget(
                Path(temporary) / "budget.json", 5, initialize_new_ledger=True,
            )
            response = FakeResponse('{"channel":"action","semanticId":"argentum-action-v1:pass","params":{}}')
            client = FakeClient(response)
            pilot = OpenAIResponsesPilot(client=client, model="gpt-6-luna", budget=budget)
            # Reservation succeeds, provider returns, then durable settlement fails.
            with patch.object(budget, "_transact", side_effect=[None, RuntimeError("private-ledger-path")]):
                with self.assertRaisesRegex(OpenAIResponsesPilotError, "settlement failed") as caught:
                    pilot.choose(action_observation())
            evidence = caught.exception.model_io
            self.assertEqual(len(client.responses.calls), 1)
            self.assertEqual(evidence["selectedAttempt"], None)
            self.assertEqual(len(evidence["attempts"]), 1)
            recorded = evidence["attempts"][0]["response"]
            self.assertEqual(recorded["outputText"], response.output_text)
            self.assertIn("budgetError", recorded)
            self.assertNotIn("transportError", recorded)
            self.assertNotIn("private-ledger-path", str(caught.exception))

    def test_postdispatch_reservation_overrun_keeps_provider_response(self):
        with TemporaryDirectory() as temporary:
            budget = OpenAIRunBudget(
                Path(temporary) / "budget.json", 5, initialize_new_ledger=True,
            )
            response = FakeResponse("{}")
            response.usage = {"input_tokens": 1_000_000, "output_tokens": 20}
            client = FakeClient(response)
            pilot = OpenAIResponsesPilot(client=client, model="gpt-6-luna", budget=budget)
            with self.assertRaisesRegex(OpenAIResponsesPilotError, "usage exceeded") as caught:
                pilot.choose(action_observation())
            self.assertEqual(len(client.responses.calls), 1)
            evidence = caught.exception.model_io
            self.assertEqual(len(evidence["attempts"]), 1)
            self.assertEqual(evidence["attempts"][0]["response"]["usage"], response.usage)
            self.assertIn("budgetError", evidence["attempts"][0]["response"])

    def test_selects_by_semantic_id_and_hides_live_action_ids_from_model(self):
        raw_output = json.dumps(
            {
                "channel": "action",
                "semanticId": "argentum-action-v1:attack",
                "params": {"attackers": {"creature-1": "player-2"}},
            }
        )
        client = FakeClient(FakeResponse(raw_output))
        pilot = OpenAIResponsesPilot(client=client, model="gpt-test")

        choice = choose_for_observation(pilot, action_observation())

        self.assertIsInstance(choice, ArgentumActionChoice)
        self.assertEqual(choice.action_id, 7)
        self.assertEqual(
            choice.params,
            {"attackers": {"creature-1": "player-2"}},
        )
        self.assertEqual(choice.metadata["provider"], "openai")
        self.assertEqual(choice.metadata["model"], "gpt-test")
        self.assertEqual(choice.metadata["responseId"], "resp-1")
        self.assertEqual(choice.metadata["usage"]["input_tokens"], 100)

        self.assertEqual(len(client.responses.calls), 1)
        request = client.responses.calls[0]
        self.assertTrue(request["input"].startswith("Return one JSON object"))
        model_input = json.loads(request["input"].split("\n", 1)[1])
        self.assertNotIn("actionId", model_input["legalActions"][0])
        self.assertNotIn("actionId", model_input["legalActions"][1])
        self.assertEqual(
            model_input["legalActions"][1]["semanticId"],
            "argentum-action-v1:attack",
        )
        self.assertEqual(request["text"], {"format": {"type": "json_object"}})
        self.assertFalse(request["store"])
        self.assertEqual(choice.metadata["retryCount"], 0)

        model_io = choice.metadata["modelIo"]
        self.assertEqual(model_io["schemaVersion"], MODEL_IO_SCHEMA_VERSION)
        self.assertEqual(model_io["provider"], "openai")
        self.assertEqual(model_io["selectedAttempt"], 0)
        self.assertEqual(model_io["attempts"][0]["request"], request)
        self.assertEqual(model_io["attempts"][0]["response"]["outputText"], raw_output)
        self.assertEqual(
            model_io["attempts"][0]["response"]["usage"],
            {"input_tokens": 100, "output_tokens": 20},
        )

    def test_retries_one_invalid_unsubmitted_choice_and_records_retry(self):
        invalid_output = json.dumps(
            {
                "channel": "action",
                "semanticId": "invented-action",
                "params": {},
            }
        )
        valid_output = json.dumps(
            {
                "channel": "action",
                "semanticId": "argentum-action-v1:pass",
                "params": {},
            }
        )
        client = FakeClient(
            [
                FakeResponse(invalid_output, response_id="resp-invalid"),
                FakeResponse(valid_output, response_id="resp-valid"),
            ]
        )
        pilot = OpenAIResponsesPilot(client=client, model="gpt-test")

        choice = choose_for_observation(pilot, action_observation())

        self.assertEqual(choice.action_id, 2)
        self.assertEqual(choice.metadata["retryCount"], 1)
        self.assertEqual(len(client.responses.calls), 2)
        self.assertIn("previous response was invalid", client.responses.calls[1]["input"])

        model_io = choice.metadata["modelIo"]
        self.assertEqual(model_io["selectedAttempt"], 1)
        self.assertEqual(len(model_io["attempts"]), 2)
        self.assertEqual(model_io["attempts"][0]["request"], client.responses.calls[0])
        self.assertEqual(model_io["attempts"][1]["request"], client.responses.calls[1])
        self.assertEqual(
            model_io["attempts"][0]["response"]["outputText"], invalid_output
        )
        self.assertIn("validationError", model_io["attempts"][0]["response"])
        self.assertEqual(
            model_io["attempts"][1]["response"]["outputText"], valid_output
        )
        self.assertNotIn("validationError", model_io["attempts"][1]["response"])

    def test_engine_action_specs_constrain_each_request_local_choice(self):
        observation = action_observation()
        observation["type"] = "GameServerSeat"
        observation["legalActions"][0]["parameterSpec"] = {"allowedFields": {}}
        observation["legalActions"][1]["parameterSpec"] = {
            "allowedFields": {"attackers": "ENTITY_ID_MAP"}
        }
        client = FakeClient(FakeResponse(json.dumps({
            "channel": "action",
            "choice": {
                "semanticId": "argentum-action-v1:attack",
                "params": {"attackers": {"creature-1": "player-2"}},
            },
        })))

        choice = OpenAIResponsesPilot(client=client, model="gpt-test").choose(observation)

        self.assertEqual(choice.action_id, 7)
        self.assertEqual(choice.params, {"attackers": {"creature-1": "player-2"}})
        schema = client.responses.calls[0]["text"]["format"]["schema"]
        self.assertEqual(schema["type"], "object")
        variants = schema["properties"]["choice"]["anyOf"]
        self.assertEqual(variants[0]["properties"]["params"]["properties"], {})
        self.assertEqual(
            set(variants[1]["properties"]["params"]["properties"]), {"attackers"}
        )
        self.assertFalse(variants[1]["properties"]["params"]["additionalProperties"])

    def test_engine_spec_rejects_stale_kind_based_params_before_submission(self):
        observation = action_observation()
        observation["legalActions"][1]["parameterSpec"] = {"allowedFields": {}}
        client = FakeClient(FakeResponse(json.dumps({
            "channel": "action",
            "semanticId": "argentum-action-v1:attack",
            "params": {"attackers": {"creature-1": "player-2"}},
        })))

        with self.assertRaisesRegex(OpenAIResponsesPilotError, "not allowed by native"):
            OpenAIResponsesPilot(client=client, model="gpt-test", max_attempts=1).choose(
                observation
            )
        self.assertEqual(len(client.responses.calls), 1)

    def test_engine_spec_accepts_declared_field_independent_of_action_kind(self):
        observation = action_observation()
        observation["legalActions"][0]["parameterSpec"] = {
            "allowedFields": {"xValue": "INTEGER"}
        }
        client = FakeClient(FakeResponse(json.dumps({
            "channel": "action",
            "semanticId": "argentum-action-v1:pass",
            "params": {"xValue": 2},
        })))

        choice = OpenAIResponsesPilot(client=client, model="gpt-test").choose(observation)

        self.assertEqual(choice.params, {"xValue": 2})

    def test_engine_spec_offered_exiled_cards_flows_through_request_and_choice(self):
        observation = action_observation()
        observation["type"] = "GameServerSeat"
        observation["legalActions"][0]["parameterSpec"] = {"allowedFields": {}}
        observation["legalActions"][1]["parameterSpec"] = {
            "allowedFields": {"targets": "ENTITY_ID_ARRAY", "exiledCards": "ENTITY_ID_ARRAY"}
        }
        params = {"targets": ["spell-1"], "exiledCards": ["blue-card-1"]}
        client = FakeClient(FakeResponse(json.dumps({
            "channel": "action", "choice": {
                "semanticId": "argentum-action-v1:attack", "params": params,
            },
        })))

        choice = OpenAIResponsesPilot(client=client, model="gpt-test").choose(observation)

        self.assertEqual(choice.action_id, 7)
        self.assertEqual(choice.params, params)
        variants = client.responses.calls[0]["text"]["format"]["schema"]["properties"]["choice"]["anyOf"]
        self.assertEqual(variants[1]["properties"]["params"]["properties"]["exiledCards"], {
            "type": "array", "items": {"type": "string"},
        })
        self.assertNotIn("exiledCards", variants[0]["properties"]["params"]["properties"])

    def test_exiled_cards_requires_native_offer_and_entity_id_array(self):
        for offered, value, error in (
            (False, ["blue-card-1"], "not allowed by native"),
            (True, "blue-card-1", "requires ENTITY_ID_ARRAY"),
            (True, [1], "requires ENTITY_ID_ARRAY"),
        ):
            with self.subTest(offered=offered, value=value):
                observation = action_observation()
                observation["type"] = "GameServerSeat"
                observation["legalActions"][0]["parameterSpec"] = {"allowedFields": {}}
                observation["legalActions"][1]["parameterSpec"] = {
                    "allowedFields": {"exiledCards": "ENTITY_ID_ARRAY"} if offered else {}
                }
                client = FakeClient(FakeResponse(json.dumps({
                    "channel": "action", "choice": {
                        "semanticId": "argentum-action-v1:attack",
                        "params": {"exiledCards": value},
                    },
                })))
                with self.assertRaisesRegex(OpenAIResponsesPilotError, error):
                    OpenAIResponsesPilot(client=client, model="gpt-test", max_attempts=1).choose(
                        observation
                    )

    def test_native_delve_cap_rejects_seven_of_six_before_submission(self):
        observation = action_observation()
        observation["legalActions"][0]["parameterSpec"] = {"allowedFields": {}}
        cast = observation["legalActions"][1]
        cast.update({
            "kind": "CastSpell", "hasDelve": True,
            "validDelveCards": [{"entityId": f"grave-{i}"} for i in range(14)],
            "maxDelveCards": 6,
            "parameterSpec": {"allowedFields": {"delvedCards": "ENTITY_ID_ARRAY"}},
        })

        def choose(cards):
            client = FakeClient(FakeResponse(json.dumps({
                "channel": "action", "choice": {
                    "semanticId": cast["semanticId"],
                    "params": {"delvedCards": cards},
                },
            })))
            pilot = OpenAIResponsesPilot(client=client, model="gpt-test", max_attempts=1)
            return client, pilot

        client, pilot = choose([f"grave-{i}" for i in range(6)])
        self.assertEqual(len(pilot.choose(observation).params["delvedCards"]), 6)
        variants = client.responses.calls[0]["text"]["format"]["schema"]["properties"]["choice"]["anyOf"]
        schema = variants[1]["properties"]["params"]["properties"]["delvedCards"]
        self.assertEqual(schema["maxItems"], 6)
        self.assertNotIn("uniqueItems", schema)
        self.assertEqual(len(schema["items"]["enum"]), 14)

        for cards, error in (
            ([f"grave-{i}" for i in range(7)], "exceeds native maxDelveCards"),
            (["grave-0", "grave-0"], "distinct native offered IDs"),
            (["not-offered"], "distinct native offered IDs"),
        ):
            with self.subTest(cards=cards):
                _, pilot = choose(cards)
                with self.assertRaisesRegex(OpenAIResponsesPilotError, error):
                    pilot.choose(observation)

        cast["maxDelveCards"] = True
        with self.assertRaisesRegex(OpenAIResponsesPilotError, "native maxDelveCards is malformed"):
            choose([])[1].choose(observation)

    def test_native_additional_cost_fields_follow_each_offered_action_spec(self):
        # These are the four ActionParams fields on Argentum's native cost-choice
        # contract. The provider receives only fields declared for this offer.
        for field, candidate in (
            ("tappedPermanents", "creature-1"),
            ("sacrificedPermanents", "permanent-1"),
            ("discardedCards", "hand-card-1"),
            ("exiledCards", "graveyard-card-1"),
        ):
            with self.subTest(field=field):
                observation = action_observation()
                observation["type"] = "GameServerSeat"
                observation["legalActions"][0]["parameterSpec"] = {"allowedFields": {}}
                observation["legalActions"][1]["parameterSpec"] = {
                    "allowedFields": {field: "ENTITY_ID_ARRAY"}
                }
                params = {field: [candidate]}
                client = FakeClient(FakeResponse(json.dumps({
                    "channel": "action", "choice": {
                        "semanticId": "argentum-action-v1:attack", "params": params,
                    },
                })))

                choice = OpenAIResponsesPilot(client=client, model="gpt-test").choose(observation)

                self.assertEqual(choice.params, params)
                variants = client.responses.calls[0]["text"]["format"]["schema"]["properties"]["choice"]["anyOf"]
                self.assertEqual(variants[1]["properties"]["params"]["properties"], {
                    field: {"type": "array", "items": {"type": "string"}},
                })
                self.assertEqual(variants[0]["properties"]["params"]["properties"], {})

    def test_v3_named_wait_schema_and_prompt_exclude_empty_deferrals(self):
        observation = action_observation()
        observation["legalActions"][0]["parameterSpec"] = {"allowedFields": {}}
        observation["legalActions"][1]["parameterSpec"] = {"allowedFields": {}}
        client = FakeClient(FakeResponse(json.dumps({
            "channel": "action", "choice": {
                "semanticId": "argentum-action-v1:pass", "params": {},
            },
        })))
        pilot = OpenAIResponsesPilot(
            client=client, model="gpt-test", allow_priority_delegation=True,
            allow_named_deferrals=True, require_nonempty_named_deferrals=True,
        )

        pilot.choose(observation)

        call = client.responses.calls[0]
        self.assertIn("Never send an empty deferAbilities array", call["instructions"])
        self.assertIn("targets field selects spell or ability targets", call["instructions"])
        self.assertEqual(
            call["text"]["format"]["schema"]["properties"]
                ["priorityDelegation"]["properties"]["deferAbilities"]["minItems"],
            1,
        )

    def test_explicit_wait_guidance_changes_only_action_instructions(self):
        observation = action_observation()
        for action in observation["legalActions"]:
            action["parameterSpec"] = {"allowedFields": {}}
        calls = []
        for enabled in (False, True):
            client = FakeClient(FakeResponse(json.dumps({
                "channel": "action", "choice": {
                    "semanticId": "argentum-action-v1:pass", "params": {},
                },
            })))
            OpenAIResponsesPilot(
                client=client, model="gpt-test", allow_priority_delegation=True,
                allow_named_deferrals=True, require_nonempty_named_deferrals=True,
                explicit_wait_guidance=enabled,
            ).choose(deepcopy(observation))
            calls.append(client.responses.calls[0])
        baseline, revised = calls
        self.assertEqual(revised.pop("instructions"),
                         baseline.pop("instructions") + "\n\n" + EXPLICIT_NAMED_WAIT_INSTRUCTIONS)
        self.assertEqual(baseline, revised)

    def test_explicit_wait_guidance_preserves_offered_ability_choice(self):
        observation = action_observation()
        observation["legalActions"][1] = {
            "actionId": 1, "semanticId": "native-ability", "kind": "ActivateAbility",
            "action": {"sourceId": "own-source", "abilityId": "native-ability-id"},
            "isManaAbility": False, "affordable": True,
            "parameterSpec": {"allowedFields": {}},
        }
        observation["legalActions"][0]["parameterSpec"] = {"allowedFields": {}}
        client = FakeClient(FakeResponse(json.dumps({
            "channel": "action", "choice": {"semanticId": "native-ability", "params": {}},
        })))
        choice = OpenAIResponsesPilot(
            client=client, model="gpt-test", max_attempts=1,
            allow_priority_delegation=True, allow_named_deferrals=True,
            require_nonempty_named_deferrals=True, explicit_wait_guidance=True,
        ).choose(observation)
        self.assertEqual(choice.action_id, 1)
        self.assertNotIn("priorityDelegation", choice.metadata)

    def test_explicit_wait_guidance_requires_named_wait_protocol(self):
        with self.assertRaisesRegex(OpenAIResponsesPilotError, "nonempty named-deferral"):
            OpenAIResponsesPilot(client=FakeClient(), model="gpt-test",
                                 explicit_wait_guidance=True)

    def test_explicit_wait_guidance_does_not_instruct_a_structured_decision_to_wait(self):
        observation = structured_observation()
        client = FakeClient(FakeResponse(json.dumps({
            "channel": "decision", "response": {
                "type": "TargetsResponse", "selectedTargets": {"0": ["target-1"]},
            },
        })))
        OpenAIResponsesPilot(
            client=client, model="gpt-test", allow_priority_delegation=True,
            allow_named_deferrals=True, require_nonempty_named_deferrals=True,
            explicit_wait_guidance=True,
        ).choose(observation)
        self.assertNotIn(EXPLICIT_NAMED_WAIT_INSTRUCTIONS,
                         client.responses.calls[0]["instructions"])

    def test_engine_spec_rejects_wrong_action_param_wire_type(self):
        observation = action_observation()
        observation["legalActions"][1]["parameterSpec"] = {
            "allowedFields": {"attackers": "ENTITY_ID_MAP"}
        }
        client = FakeClient(FakeResponse(json.dumps({
            "channel": "action",
            "semanticId": "argentum-action-v1:attack",
            "params": {"attackers": {"creature-1": ["player-2"]}},
        })))

        with self.assertRaisesRegex(OpenAIResponsesPilotError, "requires ENTITY_ID_MAP"):
            OpenAIResponsesPilot(client=client, model="gpt-test", max_attempts=1).choose(
                observation
            )

    def test_unknown_native_action_param_kind_fails_before_provider_call(self):
        observation = action_observation()
        observation["legalActions"][0]["parameterSpec"] = {
            "allowedFields": {"futureParam": "FUTURE_KIND"}
        }
        client = FakeClient(FakeResponse("{}"))

        with self.assertRaisesRegex(OpenAIResponsesPilotError, "unsupported field"):
            OpenAIResponsesPilot(client=client, model="gpt-test").choose(observation)
        self.assertEqual(client.responses.calls, [])

    def test_legacy_game_server_without_parameter_spec_keeps_json_object_contract(self):
        observation = action_observation()
        observation["type"] = "GameServerSeat"
        client = FakeClient(FakeResponse(json.dumps({
            "channel": "action",
            "semanticId": "argentum-action-v1:pass",
            "params": {},
        })))

        OpenAIResponsesPilot(client=client, model="gpt-test").choose(observation)
        self.assertEqual(
            client.responses.calls[0]["text"]["format"], {"type": "json_object"}
        )

    def test_structured_decision_injects_live_routing_id_locally(self):
        client = FakeClient(
            FakeResponse(
                json.dumps(
                    {
                        "channel": "decision",
                        "response": {
                            "type": "TargetsResponse",
                            "selectedTargets": {"0": ["target-1"]},
                        },
                    }
                )
            )
        )
        pilot = OpenAIResponsesPilot(client=client, model="gpt-test")

        choice = choose_for_observation(pilot, structured_observation())

        self.assertIsInstance(choice, ArgentumDecisionChoice)
        self.assertEqual(
            choice.response,
            {
                "type": "TargetsResponse",
                "selectedTargets": {"0": ["target-1"]},
                "decisionId": "routing-live-9",
            },
        )
        model_input = json.loads(client.responses.calls[0]["input"].split("\n", 1)[1])
        self.assertNotIn("decisionId", model_input["pendingDecision"])
        self.assertEqual(
            model_input["pendingDecision"]["semanticId"],
            "argentum-decision-v1:choose-targets",
        )
        self.assertEqual(choice.metadata["modelIo"]["selectedAttempt"], 0)
        selected_schema = client.responses.calls[0]["text"]["format"]["schema"]
        self.assertEqual(
            selected_schema["properties"]["response"]["properties"]
            ["selectedTargets"]["properties"]["0"]["items"]["enum"],
            ["target-1"],
        )

    def test_retries_target_map_scalar_from_native_snapcaster_decision(self):
        observation = structured_observation()
        client = FakeClient([
            FakeResponse(json.dumps({
                "channel": "decision", "response": {
                    "type": "TargetsResponse", "selectedTargets": {"0": "target-1"},
                },
            })),
            FakeResponse(json.dumps({
                "channel": "decision", "response": {
                    "type": "TargetsResponse", "selectedTargets": {"0": ["target-1"]},
                },
            })),
        ])
        choice = OpenAIResponsesPilot(client=client, model="gpt-test").choose(observation)
        self.assertEqual(choice.response["selectedTargets"], {"0": ["target-1"]})
        self.assertEqual(choice.metadata["modelIo"]["selectedAttempt"], 1)
        self.assertIn("arrays of offered target IDs", client.responses.calls[1]["input"])

    def test_rejects_unoffered_target_or_requirement_index(self):
        for selected in ({"0": ["invented"]}, {"1": ["target-1"]}):
            with self.subTest(selected=selected):
                client = FakeClient(FakeResponse(json.dumps({
                    "channel": "decision", "response": {
                        "type": "TargetsResponse", "selectedTargets": selected,
                    },
                })))
                with self.assertRaisesRegex(OpenAIResponsesPilotError, "offered target IDs"):
                    OpenAIResponsesPilot(
                        client=client, model="gpt-test", max_attempts=1,
                    ).choose(structured_observation())

    def test_empty_native_target_offer_uses_zero_length_schema(self):
        observation = structured_observation()
        observation["pendingDecision"]["legalTargets"] = {"0": []}
        client = FakeClient(FakeResponse(json.dumps({
            "channel": "decision", "response": {
                "type": "TargetsResponse", "selectedTargets": {"0": []},
            },
        })))
        choice = OpenAIResponsesPilot(client=client, model="gpt-test").choose(observation)
        self.assertEqual(choice.response["selectedTargets"], {"0": []})
        target_schema = client.responses.calls[0]["text"]["format"]["schema"] \
            ["properties"]["response"]["properties"]["selectedTargets"]["properties"]["0"]
        self.assertEqual(target_schema, {
            "type": "array", "items": {"type": "string"}, "maxItems": 0,
        })

    def test_card_selection_uses_current_native_options(self):
        observation = structured_observation()
        observation["pendingDecision"].update({
            "kind": "SelectCardsDecision", "options": ["current-card"],
            "responseSpec": {
                "responseType": "CardsSelectedResponse",
                "requiredFields": {"selectedCards": "ENTITY_ID_ARRAY"},
            },
        })
        def answer(card_id):
            return json.dumps({"channel": "decision", "response": {
                "type": "CardsSelectedResponse", "selectedCards": [card_id],
            }})
        client = FakeClient([FakeResponse(answer("old-card")),
                             FakeResponse(answer("current-card"))])
        choice = OpenAIResponsesPilot(client=client, model="gpt-test").choose(observation)
        self.assertEqual(choice.response["selectedCards"], ["current-card"])
        self.assertEqual(choice.metadata["retryCount"], 1)
        selected_schema = client.responses.calls[0]["text"]["format"]["schema"] \
            ["properties"]["response"]["properties"]["selectedCards"]
        self.assertEqual(selected_schema["items"]["enum"], ["current-card"])
        self.assertIn("offered in options", client.responses.calls[1]["input"])

    def test_malformed_native_card_options_fail_before_provider_call(self):
        observation = structured_observation()
        observation["pendingDecision"].update({
            "kind": "SelectCardsDecision", "options": "current-card",
            "responseSpec": {
                "responseType": "CardsSelectedResponse",
                "requiredFields": {"selectedCards": "ENTITY_ID_ARRAY"},
            },
        })
        client = FakeClient(FakeResponse("{}"))
        with self.assertRaisesRegex(OpenAIResponsesPilotError, "card options are malformed"):
            OpenAIResponsesPilot(client=client, model="gpt-test").choose(observation)
        self.assertEqual(client.responses.calls, [])

    def test_empty_card_offers_use_zero_length_schema_and_reject_stale_selection(self):
        for kind in ("SelectCardsDecision", "SearchLibraryDecision"):
            with self.subTest(kind=kind):
                observation = structured_observation()
                observation["pendingDecision"].update({
                    "kind": kind, "options": [], "minSelections": 0, "maxSelections": 1,
                    "responseSpec": {
                        "responseType": "CardsSelectedResponse",
                        "requiredFields": {"selectedCards": "ENTITY_ID_ARRAY"},
                    },
                })
                def answer(cards):
                    return json.dumps({"channel": "decision", "response": {
                        "type": "CardsSelectedResponse", "selectedCards": cards,
                    }})
                client = FakeClient([FakeResponse(answer(["stale-card"])),
                                     FakeResponse(answer([]))])
                choice = OpenAIResponsesPilot(client=client, model="gpt-test").choose(observation)
                self.assertEqual(choice.response["selectedCards"], [])
                self.assertEqual(choice.metadata["retryCount"], 1)
                selected_schema = client.responses.calls[0]["text"]["format"]["schema"] \
                    ["properties"]["response"]["properties"]["selectedCards"]
                self.assertEqual(selected_schema, {
                    "type": "array", "items": {"type": "string"}, "maxItems": 0,
                })

    def test_missing_native_attack_candidates_fail_before_provider_call(self):
        observation = action_observation()
        observation["legalActions"] = [{
            "actionId": 0, "semanticId": "native-attack", "kind": "DeclareAttackers",
            "parameterSpec": {"allowedFields": {"attackers": "ENTITY_ID_MAP"}},
            "validAttackers": ["current-creature"],
        }]
        client = FakeClient(FakeResponse("{}"))
        with self.assertRaisesRegex(OpenAIResponsesPilotError, "attacker candidates are malformed"):
            OpenAIResponsesPilot(client=client, model="gpt-test").choose(observation)
        self.assertEqual(client.responses.calls, [])

    def test_retries_response_that_violates_native_decision_spec(self):
        observation = structured_observation()
        observation["pendingDecision"]["kind"] = "SELECT_CARDS"
        observation["pendingDecision"]["responseSpec"] = {
            "responseType": "CardsSelectedResponse",
            "requiredFields": {"selectedCards": "ENTITY_ID_ARRAY"},
        }
        client = FakeClient([
            FakeResponse(json.dumps({
                "channel": "decision",
                "response": {"type": "SELECT_CARDS", "cardIds": ["card-1"]},
            })),
            FakeResponse(json.dumps({
                "channel": "decision",
                "response": {"type": "CardsSelectedResponse", "selectedCards": ["card-1"]},
            })),
        ])
        choice = OpenAIResponsesPilot(client=client, model="gpt-test").choose(observation)
        self.assertEqual(choice.response, {
            "type": "CardsSelectedResponse",
            "selectedCards": ["card-1"],
            "decisionId": "routing-live-9",
        })
        self.assertEqual(choice.metadata["modelIo"]["selectedAttempt"], 1)
        self.assertIn("CardsSelectedResponse", client.responses.calls[1]["input"])

    def test_rejects_extra_native_decision_fields_before_submission(self):
        client = FakeClient(FakeResponse(json.dumps({
            "channel": "decision",
            "response": {
                "type": "TargetsResponse",
                "selectedTargets": {"0": ["target-1"]},
                "selectedCards": ["card-1"],
            },
        })))
        pilot = OpenAIResponsesPilot(client=client, model="gpt-test", max_attempts=1)

        with self.assertRaisesRegex(OpenAIResponsesPilotError, "unsupported fields"):
            pilot.choose(structured_observation())
        self.assertEqual(len(client.responses.calls), 1)

    def test_retries_mana_source_not_offered_by_current_decision(self):
        observation = structured_observation()
        observation["pendingDecision"].update({
            "kind": "SelectManaSourcesDecision",
            "availableSources": [{"entityId": "e170", "name": "Mountain"}],
            "responseSpec": {
                "responseType": "ManaSourcesSelectedResponse",
                "requiredFields": {
                    "autoPay": "BOOLEAN", "declined": "BOOLEAN",
                    "selectedSources": "ENTITY_ID_ARRAY",
                    "waterbendPermanents": "ENTITY_ID_ARRAY",
                },
            },
        })
        def answer(sources):
            return FakeResponse(json.dumps({
                "channel": "decision", "response": {
                    "type": "ManaSourcesSelectedResponse", "autoPay": False,
                    "declined": False, "selectedSources": sources,
                    "waterbendPermanents": [],
                },
            }))
        client = FakeClient([answer(["e170", "e146"]), answer(["e170"])])
        choice = OpenAIResponsesPilot(client=client, model="gpt-test").choose(observation)
        self.assertEqual(choice.response["selectedSources"], ["e170"])
        self.assertEqual(choice.metadata["modelIo"]["selectedAttempt"], 1)
        self.assertGreaterEqual(choice.metadata["providerWallTimeMs"], 0)
        self.assertTrue(all(
            attempt["response"]["providerWallTimeMs"] >= 0
            for attempt in choice.metadata["modelIo"]["attempts"]
        ))
        self.assertIn("must be offered", client.responses.calls[1]["input"])
        response_schema = client.responses.calls[0]["text"]["format"]["schema"]
        self.assertEqual(response_schema["properties"]["channel"]["const"], "decision")
        self.assertEqual(
            response_schema["properties"]["response"]["properties"]
            ["selectedSources"]["items"]["enum"], ["e170"],
        )

    def test_empty_native_mana_source_offer_uses_zero_length_schema(self):
        observation = structured_observation()
        observation["pendingDecision"].update({
            "kind": "SelectManaSourcesDecision", "availableSources": [],
            "canAutoPayNow": False,
            "responseSpec": {
                "responseType": "ManaSourcesSelectedResponse",
                "requiredFields": {
                    "autoPay": "BOOLEAN", "declined": "BOOLEAN",
                    "selectedSources": "ENTITY_ID_ARRAY",
                    "waterbendPermanents": "ENTITY_ID_ARRAY",
                },
            },
        })
        client = FakeClient(FakeResponse(json.dumps({
            "channel": "decision", "response": {
                "type": "ManaSourcesSelectedResponse", "autoPay": False,
                "declined": True, "selectedSources": [], "waterbendPermanents": [],
            },
        })))
        choice = OpenAIResponsesPilot(client=client, model="gpt-test").choose(observation)
        self.assertEqual(choice.response["selectedSources"], [])
        selected_schema = client.responses.calls[0]["text"]["format"]["schema"] \
            ["properties"]["response"]["properties"]["selectedSources"]
        self.assertEqual(selected_schema, {
            "type": "array", "items": {"type": "string"}, "maxItems": 0,
        })

    def test_mixed_mana_payment_window_keeps_native_mana_action_channel(self):
        observation = structured_observation()
        observation["pendingDecision"].update({
            "kind": "SelectManaSourcesDecision",
            "availableSources": [{"entityId": "e170", "name": "Mountain"}],
            "responseSpec": {
                "responseType": "ManaSourcesSelectedResponse",
                "requiredFields": {
                    "autoPay": "BOOLEAN", "declined": "BOOLEAN",
                    "selectedSources": "ENTITY_ID_ARRAY",
                    "waterbendPermanents": "ENTITY_ID_ARRAY",
                },
            },
        })
        observation["legalActions"] = [{
            "actionId": 0, "kind": "ActivateAbility", "isManaAbility": True,
            "semanticId": "native-mana-ability",
            "parameterSpec": {"allowedFields": {}},
        }]
        client = FakeClient(FakeResponse(json.dumps({
            "channel": "action", "semanticId": "native-mana-ability", "params": {},
        })))
        choice = choose_for_observation(
            OpenAIResponsesPilot(client=client, model="gpt-test"), observation,
        )
        self.assertEqual(choice.action_id, 0)
        self.assertEqual(client.responses.calls[0]["text"]["format"], {"type": "json_object"})
        self.assertIn("offered native mana ability", client.responses.calls[0]["instructions"])

    def test_native_autopay_false_retries_into_offered_mana_ability(self):
        observation = structured_observation()
        observation["pendingDecision"].update({
            "kind": "SelectManaSourcesDecision", "canAutoPayNow": False,
            "availableSources": [{"entityId": "mountain", "name": "Mountain"}],
            "responseSpec": {"responseType": "ManaSourcesSelectedResponse", "requiredFields": {
                "autoPay": "BOOLEAN", "declined": "BOOLEAN",
                "selectedSources": "ENTITY_ID_ARRAY", "waterbendPermanents": "ENTITY_ID_ARRAY",
            }},
        })
        observation["legalActions"] = [{
            "actionId": 0, "kind": "ActivateAbility", "isManaAbility": True,
            "semanticId": "treasure-mana", "parameterSpec": {"allowedFields": {}},
        }]
        invalid = FakeResponse(json.dumps({"channel": "decision", "response": {
            "type": "ManaSourcesSelectedResponse", "autoPay": True,
            "declined": False, "selectedSources": [], "waterbendPermanents": [],
        }}))
        mana_action = FakeResponse(json.dumps({
            "channel": "action", "semanticId": "treasure-mana", "params": {},
        }))
        client = FakeClient([invalid, mana_action])
        choice = choose_for_observation(OpenAIResponsesPilot(client=client, model="gpt-test"), observation)
        self.assertEqual(choice.action_id, 0)
        self.assertEqual(len(client.responses.calls), 2)
        self.assertIn("canAutoPayNow is false", client.responses.calls[1]["input"])
        self.assertIn("canAutoPayNow is false", client.responses.calls[0]["instructions"])

    def test_native_autopay_true_requires_empty_selected_sources(self):
        observation = structured_observation()
        observation["pendingDecision"].update({
            "kind": "SelectManaSourcesDecision", "canAutoPayNow": True,
            "availableSources": [{"entityId": "mountain", "name": "Mountain"}],
            "responseSpec": {"responseType": "ManaSourcesSelectedResponse", "requiredFields": {
                "autoPay": "BOOLEAN", "declined": "BOOLEAN",
                "selectedSources": "ENTITY_ID_ARRAY", "waterbendPermanents": "ENTITY_ID_ARRAY",
            }},
        })
        def answer(sources):
            return FakeResponse(json.dumps({"channel": "decision", "response": {
                "type": "ManaSourcesSelectedResponse", "autoPay": True,
                "declined": False, "selectedSources": sources, "waterbendPermanents": [],
            }}))
        client = FakeClient([answer(["mountain"]), answer([])])
        choice = OpenAIResponsesPilot(client=client, model="gpt-test").choose(observation)
        self.assertEqual(choice.response["selectedSources"], [])
        self.assertTrue(choice.response["autoPay"])
        self.assertIn("cannot be combined", client.responses.calls[1]["input"])

    def test_native_autopay_false_constrains_decision_only_schema(self):
        observation = structured_observation()
        observation["pendingDecision"].update({
            "kind": "SelectManaSourcesDecision", "canAutoPayNow": False,
            "availableSources": [{"entityId": "mountain", "name": "Mountain"}],
            "responseSpec": {"responseType": "ManaSourcesSelectedResponse", "requiredFields": {
                "autoPay": "BOOLEAN", "declined": "BOOLEAN",
                "selectedSources": "ENTITY_ID_ARRAY", "waterbendPermanents": "ENTITY_ID_ARRAY",
            }},
        })
        client = FakeClient(FakeResponse(json.dumps({"channel": "decision", "response": {
            "type": "ManaSourcesSelectedResponse", "autoPay": False,
            "declined": True, "selectedSources": [], "waterbendPermanents": [],
        }})))
        OpenAIResponsesPilot(client=client, model="gpt-test").choose(observation)
        schema = client.responses.calls[0]["text"]["format"]["schema"]
        self.assertEqual(schema["properties"]["response"]["properties"]["autoPay"]["const"], False)

    def test_native_payment_correction_uses_current_offer_and_records_provider_attempt(self):
        observation = structured_observation()
        observation["pendingDecision"].update({
            "kind": "SelectManaSourcesDecision", "decisionId": "fresh-payment",
            "canAutoPayNow": False,
            "availableSources": [{"entityId": "fresh-mountain", "name": "Mountain"}],
            "responseSpec": {"responseType": "ManaSourcesSelectedResponse", "requiredFields": {
                "autoPay": "BOOLEAN", "declined": "BOOLEAN",
                "selectedSources": "ENTITY_ID_ARRAY", "waterbendPermanents": "ENTITY_ID_ARRAY",
            }},
        })
        observation["nativePaymentError"] = "Selected mana sources cannot pay this spell's cost"
        client = FakeClient(FakeResponse(json.dumps({"channel": "decision", "response": {
            "type": "ManaSourcesSelectedResponse", "autoPay": False,
            "declined": True, "selectedSources": [], "waterbendPermanents": [],
        }})))
        choice = OpenAIResponsesPilot(client=client, model="gpt-test").choose(observation)
        self.assertEqual(choice.response["decisionId"], "fresh-payment")
        self.assertEqual(choice.metadata["modelIo"]["selectedAttempt"], 0)
        self.assertEqual(len(client.responses.calls), 1)
        self.assertIn("nativePaymentError", client.responses.calls[0]["input"])
        self.assertIn("fresh-mountain", client.responses.calls[0]["input"])
        self.assertIn("fresh legal action", client.responses.calls[0]["instructions"])

        observation["pendingDecision"]["kind"] = "YesNoDecision"
        with self.assertRaisesRegex(OpenAIResponsesPilotError, "current mana decision"):
            OpenAIResponsesPilot(client=client, model="gpt-test").choose(observation)
        self.assertEqual(len(client.responses.calls), 1)

    def test_native_autopay_unknown_keeps_legacy_decision_usable(self):
        observation = structured_observation()
        observation["pendingDecision"].update({
            "kind": "SelectManaSourcesDecision", "canAutoPayNow": None,
            "availableSources": [],
            "responseSpec": {"responseType": "ManaSourcesSelectedResponse", "requiredFields": {
                "autoPay": "BOOLEAN", "declined": "BOOLEAN",
                "selectedSources": "ENTITY_ID_ARRAY", "waterbendPermanents": "ENTITY_ID_ARRAY",
            }},
        })
        client = FakeClient(FakeResponse(json.dumps({"channel": "decision", "response": {
            "type": "ManaSourcesSelectedResponse", "autoPay": True,
            "declined": False, "selectedSources": [], "waterbendPermanents": [],
        }})))
        choice = OpenAIResponsesPilot(client=client, model="gpt-test").choose(observation)
        self.assertTrue(choice.response["autoPay"])

    def test_rejects_wrong_native_array_element_kind_before_submission(self):
        observation = structured_observation()
        observation["pendingDecision"]["responseSpec"] = {
            "responseType": "ModesChosenResponse",
            "requiredFields": {"selectedModes": "INTEGER_ARRAY"},
        }
        client = FakeClient(FakeResponse(json.dumps({
            "channel": "decision",
            "response": {"type": "ModesChosenResponse", "selectedModes": [True]},
        })))
        pilot = OpenAIResponsesPilot(client=client, model="gpt-test", max_attempts=1)

        with self.assertRaisesRegex(OpenAIResponsesPilotError, "requires INTEGER_ARRAY"):
            pilot.choose(observation)
        self.assertEqual(len(client.responses.calls), 1)

    def test_native_response_spec_wire_shapes_table(self):
        # Each distinct response DTO in Argentum DecisionResponseSpec.kt. Native
        # state still decides card counts, ordering, amounts, and other legality.
        cases = [
            ("TargetsResponse", {"selectedTargets": "MAP"},
             {"selectedTargets": {"0": ["target-1"]}}, {"selectedTargets": {"0": "target-1"}}),
            ("CardsSelectedResponse", {"selectedCards": "ENTITY_ID_ARRAY"},
             {"selectedCards": ["card-1"]}, {"selectedCards": [1]}),
            ("YesNoResponse", {"choice": "BOOLEAN"}, {"choice": True}, {"choice": 1}),
            ("BatchYesNoResponse", {"choice": "BOOLEAN", "applyToAll": "BOOLEAN"},
             {"choice": False, "applyToAll": True}, {"choice": False, "applyToAll": 1}),
            ("ModesChosenResponse", {"selectedModes": "INTEGER_ARRAY"},
             {"selectedModes": [0]}, {"selectedModes": [True]}),
            ("ColorChosenResponse", {"color": "STRING"},
             {"color": "BLUE"}, {"color": 1}),
            ("NumberChosenResponse", {"number": "INTEGER"},
             {"number": 0}, {"number": True}),
            ("DistributionResponse", {"distribution": "MAP"},
             {"distribution": {"target-1": 1}}, {"distribution": {"target-1": "1"}}),
            ("OrderedResponse", {"orderedObjects": "ENTITY_ID_ARRAY"},
             {"orderedObjects": ["card-1"]}, {"orderedObjects": [1]}),
            ("PilesSplitResponse", {"piles": "ENTITY_ID_ARRAY_ARRAY"},
             {"piles": [["card-1"], []]}, {"piles": ["card-1"]}),
            ("OptionChosenResponse", {"optionIndex": "INTEGER"},
             {"optionIndex": 0}, {"optionIndex": False}),
            ("ReplacementChosenResponse", {"fromIndex": "INTEGER", "toIndex": "INTEGER"},
             {"fromIndex": 0, "toIndex": 1}, {"fromIndex": 0, "toIndex": "1"}),
            ("BudgetModalResponse", {"selectedModeIndices": "INTEGER_ARRAY"},
             {"selectedModeIndices": [0]}, {"selectedModeIndices": ["0"]}),
            ("DamageAssignmentResponse", {"assignments": "MAP"},
             {"assignments": {"target-1": 1}}, {"assignments": {"target-1": True}}),
            ("CombatResolutionResponse", {"edges": "DAMAGE_EDGE_AMOUNT_ARRAY"},
             {"edges": [{"edgeId": "edge-1", "amount": 1}]},
             {"edges": [{"edgeId": "edge-1", "amount": "1"}]}),
            ("ManaSourcesSelectedResponse", {
                "selectedSources": "ENTITY_ID_ARRAY", "autoPay": "BOOLEAN",
                "waterbendPermanents": "ENTITY_ID_ARRAY", "declined": "BOOLEAN",
             }, {"selectedSources": ["source-1"], "autoPay": False,
                 "waterbendPermanents": [], "declined": False},
             {"selectedSources": ["source-1"], "autoPay": False,
              "waterbendPermanents": [1], "declined": False}),
        ]
        for response_type, fields, valid, invalid in cases:
            with self.subTest(response_type=response_type):
                observation = structured_observation()
                pending = observation["pendingDecision"]
                pending["responseSpec"] = {"responseType": response_type, "requiredFields": fields}
                if response_type == "ManaSourcesSelectedResponse":
                    pending["availableSources"] = [{"entityId": "source-1"}]

                def answer(values):
                    return FakeResponse(json.dumps({
                        "channel": "decision", "response": {"type": response_type, **values},
                    }))

                client = FakeClient(answer(valid))
                choice = OpenAIResponsesPilot(client=client, model="gpt-test").choose(observation)
                self.assertEqual({k: choice.response[k] for k in fields}, valid)
                response_schema = client.responses.calls[0]["text"]["format"]["schema"]
                response_schema = response_schema["properties"]["response"]
                optional = {"orderedBlockers", "orderedAttackers"} if response_type == "CombatResolutionResponse" else set()
                self.assertEqual(set(response_schema["properties"]), {"type", *fields, *optional})
                self.assertEqual(set(response_schema["required"]), {"type", *fields})
                self.assertEqual(response_schema["properties"]["type"]["const"], response_type)

                bad_client = FakeClient(answer(invalid))
                with self.assertRaises(OpenAIResponsesPilotError):
                    OpenAIResponsesPilot(
                        client=bad_client, model="gpt-test", max_attempts=1,
                    ).choose(observation)
                self.assertEqual(len(bad_client.responses.calls), 1)

    def test_nested_native_response_schema_values(self):
        for response_type, field in (
            ("DistributionResponse", "distribution"),
            ("DamageAssignmentResponse", "assignments"),
        ):
            with self.subTest(response_type=response_type):
                observation = structured_observation()
                observation["pendingDecision"]["responseSpec"] = {
                    "responseType": response_type, "requiredFields": {field: "MAP"},
                }
                client = FakeClient(FakeResponse(json.dumps({
                    "channel": "decision", "response": {"type": response_type, field: {}},
                })))
                OpenAIResponsesPilot(client=client, model="gpt-test").choose(observation)
                schema = client.responses.calls[0]["text"]["format"]["schema"]
                nested = schema["properties"]["response"]["properties"][field]
                self.assertEqual(nested, {"type": "object"})

        observation = structured_observation()
        observation["pendingDecision"]["responseSpec"] = {
            "responseType": "CombatResolutionResponse",
            "requiredFields": {"edges": "DAMAGE_EDGE_AMOUNT_ARRAY"},
        }
        client = FakeClient(FakeResponse(json.dumps({
            "channel": "decision", "response": {"type": "CombatResolutionResponse", "edges": []},
        })))
        OpenAIResponsesPilot(client=client, model="gpt-test").choose(observation)
        schema = client.responses.calls[0]["text"]["format"]["schema"]
        edge = schema["properties"]["response"]["properties"]["edges"]["items"]
        self.assertEqual(set(edge["required"]), {"edgeId", "amount"})
        self.assertEqual(edge["properties"]["amount"]["type"], "integer")

    def test_native_nested_wire_rejects_kotlin_decoder_failures(self):
        bad = [
            ("DistributionResponse", "distribution", {"e12": "one"}),
            ("DamageAssignmentResponse", "assignments", {"e12": True}),
            ("CombatResolutionResponse", "edges", ["e12"]),
            ("CombatResolutionResponse", "edges", [{}]),
            ("CombatResolutionResponse", "edges", [{"edgeId": "edge-1", "amount": 2**31}]),
            ("ColorChosenResponse", "color", "chartreuse"),
            ("NumberChosenResponse", "number", 2**31),
            ("ModesChosenResponse", "selectedModes", [-(2**31) - 1]),
        ]
        for response_type, field, value in bad:
            with self.subTest(response_type=response_type, value=value):
                observation = structured_observation()
                observation["pendingDecision"]["responseSpec"] = {
                    "responseType": response_type,
                    "requiredFields": {field: {
                        "distribution": "MAP", "assignments": "MAP",
                        "edges": "DAMAGE_EDGE_AMOUNT_ARRAY", "color": "STRING",
                        "number": "INTEGER", "selectedModes": "INTEGER_ARRAY",
                    }[field]},
                }
                client = FakeClient(FakeResponse(json.dumps({
                    "channel": "decision", "response": {"type": response_type, field: value},
                })))
                with self.assertRaises(OpenAIResponsesPilotError):
                    OpenAIResponsesPilot(
                        client=client, model="gpt-test", max_attempts=1,
                    ).choose(observation)

    def test_combat_optional_order_maps_match_native_dto(self):
        observation = structured_observation()
        observation["pendingDecision"]["responseSpec"] = {
            "responseType": "CombatResolutionResponse",
            "requiredFields": {"edges": "DAMAGE_EDGE_AMOUNT_ARRAY"},
        }
        def answer(ordered):
            return FakeResponse(json.dumps({
                "channel": "decision", "response": {
                    "type": "CombatResolutionResponse", "edges": [],
                    "orderedBlockers": ordered, "orderedAttackers": {},
                },
            }))
        client = FakeClient(answer({"attacker-1": ["blocker-1"]}))
        choice = OpenAIResponsesPilot(client=client, model="gpt-test").choose(observation)
        self.assertEqual(choice.response["orderedBlockers"], {"attacker-1": ["blocker-1"]})
        schema = client.responses.calls[0]["text"]["format"]["schema"]
        nested = schema["properties"]["response"]["properties"]["orderedBlockers"]
        self.assertEqual(nested, {"type": "object"})
        with self.assertRaisesRegex(OpenAIResponsesPilotError, "ENTITY_ID_ARRAY_MAP"):
            OpenAIResponsesPilot(
                client=FakeClient(answer({"attacker-1": "blocker-1"})),
                model="gpt-test", max_attempts=1,
            ).choose(observation)

    def test_cancel_response_requires_native_cancel_allowed(self):
        for response_type, fields in (
            ("TargetsResponse", {"selectedTargets": "MAP"}),
            ("OptionChosenResponse", {"optionIndex": "INTEGER"}),
        ):
            for allowed in (True, False):
                with self.subTest(response_type=response_type, allowed=allowed):
                    observation = structured_observation()
                    observation["pendingDecision"]["responseSpec"] = {
                        "responseType": response_type, "requiredFields": fields,
                        "cancelAllowed": allowed,
                    }
                    client = FakeClient(FakeResponse(json.dumps({
                        "channel": "decision",
                        "response": {"type": "CancelDecisionResponse"},
                    })))
                    pilot = OpenAIResponsesPilot(client=client, model="gpt-test", max_attempts=1)
                    if allowed:
                        choice = pilot.choose(observation)
                        self.assertEqual(choice.response, {
                            "type": "CancelDecisionResponse", "decisionId": "routing-live-9",
                        })
                        schema = client.responses.calls[0]["text"]["format"]["schema"]
                        self.assertEqual(schema["type"], "object")
                        self.assertEqual(schema["properties"]["response"]["anyOf"][1]
                                         ["properties"]["type"]["const"], "CancelDecisionResponse")
                    else:
                        with self.assertRaisesRegex(OpenAIResponsesPilotError, "requires native"):
                            pilot.choose(observation)

    def test_unknown_semantic_id_fails_closed(self):
        client = FakeClient(
            FakeResponse(
                json.dumps(
                    {
                        "channel": "action",
                        "semanticId": "invented-action",
                        "params": {},
                    }
                )
            )
        )
        pilot = OpenAIResponsesPilot(client=client, model="gpt-test")

        with self.assertRaisesRegex(
            OpenAIResponsesPilotError,
            "not exactly one current Argentum legal action",
        ):
            pilot.choose(action_observation())

    def test_model_cannot_supply_live_structured_routing_id(self):
        client = FakeClient(
            FakeResponse(
                json.dumps(
                    {
                        "channel": "decision",
                        "response": {
                            "type": "TargetsResponse",
                            "decisionId": "invented-routing",
                            "selectedTargets": {"0": ["target-1"]},
                        },
                    }
                )
            )
        )
        pilot = OpenAIResponsesPilot(client=client, model="gpt-test")

        with self.assertRaisesRegex(OpenAIResponsesPilotError, "must not invent"):
            pilot.choose(structured_observation())

    def test_provider_failure_propagates_fail_closed(self):
        client = FakeClient(error=RuntimeError("provider unavailable"))
        pilot = OpenAIResponsesPilot(client=client, model="gpt-test")

        with self.assertRaisesRegex(OpenAIResponsesPilotError, "request failed"):
            pilot.choose(action_observation())

    def test_bounded_520_retries_once_with_separate_reservation_and_receipt(self):
        with TemporaryDirectory() as temporary:
            budget = OpenAIRunBudget(
                Path(temporary) / "budget.json", 5, max_requests=2,
                initialize_new_ledger=True,
            )
            client = FakeClient()
            client.responses = SequenceResponses(
                FakeHttpError(520),
                FakeResponse('{"channel":"action","semanticId":"argentum-action-v1:pass","params":{}}'),
            )
            choice = OpenAIResponsesPilot(
                client=client, model="gpt-6-luna", budget=budget, max_attempts=2,
                retry_transient_server_errors=True,
            ).choose(action_observation())
            evidence = choice.metadata["modelIo"]
            self.assertEqual(len(client.responses.calls), 2)
            self.assertEqual(client.responses.calls[0], client.responses.calls[1])
            self.assertEqual(evidence["selectedAttempt"], 1)
            self.assertEqual(len(evidence["attempts"]), 2)
            self.assertIn("status=520", evidence["attempts"][0]["response"]["transportError"])
            self.assertNotIn("private provider detail", str(evidence))
            self.assertEqual(choice.metadata["retryCount"], 0)
            self.assertEqual(budget.snapshot()["requests"], 2)
            self.assertEqual(budget.snapshot()["unsettledRequests"], 1)

    def test_bounded_520_refuses_retry_when_request_ceiling_is_reached(self):
        with TemporaryDirectory() as temporary:
            budget = OpenAIRunBudget(
                Path(temporary) / "budget.json", 5, max_requests=1,
                initialize_new_ledger=True,
            )
            client = FakeClient()
            client.responses = SequenceResponses(FakeHttpError(520))
            with self.assertRaisesRegex(OpenAIResponsesPilotError, "request limit") as caught:
                OpenAIResponsesPilot(
                    client=client, model="gpt-6-luna", budget=budget,
                    retry_transient_server_errors=True,
                ).choose(action_observation())
            self.assertEqual(len(client.responses.calls), 1)
            self.assertEqual(len(caught.exception.model_io["attempts"]), 1)
            self.assertEqual(budget.snapshot()["unsettledRequests"], 1)

    def test_late_520_does_not_dispatch_past_jvm_callback_deadline(self):
        with TemporaryDirectory() as temporary:
            budget = OpenAIRunBudget(
                Path(temporary) / "budget.json", 5, max_requests=2,
                initialize_new_ledger=True,
            )
            clock = [0.0]

            class LateFailure:
                def __init__(self):
                    self.calls = []

                def create(self, **kwargs):
                    self.calls.append(kwargs)
                    clock[0] = 101.0
                    raise FakeHttpError(520)

            client = FakeClient()
            client.responses = LateFailure()
            with patch("commander_gym.openai_responses_pilot.perf_counter", side_effect=lambda: clock[0]):
                with self.assertRaisesRegex(OpenAIResponsesPilotError, "status=520") as caught:
                    OpenAIResponsesPilot(
                        client=client, model="gpt-6-luna", budget=budget,
                        retry_transient_server_errors=True,
                    ).choose(action_observation())
            self.assertEqual(len(client.responses.calls), 1)
            self.assertLessEqual(client.responses.calls[0]["timeout"], 90.0)
            self.assertEqual(len(caught.exception.model_io["attempts"]), 1)
            self.assertEqual(budget.snapshot()["requests"], 1)

    def test_bounded_nontransient_http_failure_does_not_retry(self):
        with TemporaryDirectory() as temporary:
            budget = OpenAIRunBudget(
                Path(temporary) / "budget.json", 5, initialize_new_ledger=True,
            )
            client = FakeClient()
            client.responses = SequenceResponses(FakeHttpError(400))
            with self.assertRaisesRegex(OpenAIResponsesPilotError, "status=400"):
                OpenAIResponsesPilot(
                    client=client, model="gpt-6-luna", budget=budget,
                    retry_transient_server_errors=True,
                ).choose(action_observation())
            self.assertEqual(len(client.responses.calls), 1)

    def test_repeated_520_stops_at_existing_attempt_ceiling(self):
        with TemporaryDirectory() as temporary:
            budget = OpenAIRunBudget(
                Path(temporary) / "budget.json", 5, initialize_new_ledger=True,
            )
            client = FakeClient()
            client.responses = SequenceResponses(FakeHttpError(520), FakeHttpError(520))
            with self.assertRaisesRegex(OpenAIResponsesPilotError, "status=520") as caught:
                OpenAIResponsesPilot(
                    client=client, model="gpt-6-luna", budget=budget,
                    retry_transient_server_errors=True,
                ).choose(action_observation())
            self.assertEqual(len(client.responses.calls), 2)
            self.assertEqual(len(caught.exception.model_io["attempts"]), 2)
            self.assertIsNone(caught.exception.model_io["selectedAttempt"])
            self.assertEqual(budget.snapshot()["unsettledRequests"], 2)

    def test_unbudgeted_client_does_not_add_a_retry_over_sdk_policy(self):
        client = FakeClient()
        client.responses = SequenceResponses(FakeHttpError(520))
        with self.assertRaisesRegex(OpenAIResponsesPilotError, "status=520"):
            OpenAIResponsesPilot(
                client=client, model="gpt-test", retry_transient_server_errors=True,
            ).choose(action_observation())
        self.assertEqual(len(client.responses.calls), 1)

    def test_existing_bounded_pilot_keeps_fail_closed_transport_contract(self):
        with TemporaryDirectory() as temporary:
            budget = OpenAIRunBudget(
                Path(temporary) / "budget.json", 5, initialize_new_ledger=True,
            )
            client = FakeClient()
            client.responses = SequenceResponses(FakeHttpError(520))
            with self.assertRaisesRegex(OpenAIResponsesPilotError, "status=520"):
                OpenAIResponsesPilot(
                    client=client, model="gpt-6-luna", budget=budget,
                ).choose(action_observation())
            self.assertEqual(len(client.responses.calls), 1)

    def test_incomplete_provider_response_fails_closed(self):
        client = FakeClient(
            FakeResponse(
                "{}",
                status="incomplete",
                incomplete_details={"reason": "max_output_tokens"},
            )
        )
        pilot = OpenAIResponsesPilot(client=client, model="gpt-test")

        with self.assertRaisesRegex(OpenAIResponsesPilotError, "did not complete"):
            pilot.choose(action_observation())

    def test_malformed_provider_output_fails_closed(self):
        client = FakeClient(FakeResponse("not-json"))
        pilot = OpenAIResponsesPilot(client=client, model="gpt-test")

        with self.assertRaisesRegex(OpenAIResponsesPilotError, "not valid JSON"):
            pilot.choose(action_observation())


if __name__ == "__main__":
    unittest.main()
