import json
import unittest
from commander_gym.lmstudio_pilot import LMStudioPilot, LMStudioPilotError
from test_bounce_payment import observation, bounce_action
from test_lmstudio import config, FakeTransport, completion


class LocalBounceTests(unittest.TestCase):
    def test_bounce_schema_preserves_offered_ids_count_and_native_validation(self):
        for ids, valid in [(["elf-a", "elf-b"], True), (["elf-a", "elf-a"], False),
                           (["elf-a"], False), (["hidden", "elf-b"], False)]:
            answer = {"channel": "action", "choice": {"semanticId": "native-symbiote-ability",
                       "params": {"bouncedPermanents": ids}}}
            transport = FakeTransport(completion(json.dumps(answer)))
            pilot = LMStudioPilot(config(max_attempts=1), transport=transport)
            if valid:
                result = pilot.choose(observation(bounce_action(2)))
                self.assertEqual(result.params["bouncedPermanents"], ids)
            else:
                with self.assertRaises(LMStudioPilotError):
                    pilot.choose(observation(bounce_action(2)))
            schema = transport.calls[1][1]["response_format"]["json_schema"]["schema"]
            params = schema["properties"]["choice"]["anyOf"][0]["properties"]["params"]
            self.assertIn("bouncedPermanents", params["required"])
            self.assertEqual(params["properties"]["bouncedPermanents"]["minItems"], 2)

    def test_invalid_native_offer_fails_as_local_provider_before_dispatch(self):
        action = bounce_action()
        action["additionalCostInfo"]["bounceCount"] = 99
        transport = FakeTransport()
        with self.assertRaises(LMStudioPilotError) as caught:
            LMStudioPilot(config(), transport=transport).choose(observation(action))
        self.assertEqual(caught.exception.model_io["provider"], "lmstudio")
        self.assertEqual(transport.calls, [])
