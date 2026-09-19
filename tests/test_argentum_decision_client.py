import json
import unittest
from unittest.mock import patch

from commander_gym.argentum_client import ArgentumGymClient


class FakeResponse:
    def __init__(self, payload):
        self._raw = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._raw


class ArgentumDecisionClientTests(unittest.TestCase):
    @patch("commander_gym.argentum_client.urlopen")
    def test_submit_decision_matches_native_gym_server_surface(self, mocked_urlopen):
        mocked_urlopen.return_value = FakeResponse({"stateDigest": "after"})
        client = ArgentumGymClient("http://localhost:8081")
        response = {
            "type": "ChooseTargetsResponse",
            "decisionId": "routing-nonce-1",
            "targets": ["target-1"],
        }

        result = client.submit_decision("env/1", response)

        self.assertEqual(result, {"stateDigest": "after"})
        request = mocked_urlopen.call_args.args[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.full_url, "http://localhost:8081/envs/env%2F1/decision")
        self.assertEqual(json.loads(request.data.decode("utf-8")), response)
        self.assertEqual(mocked_urlopen.call_count, 1)


if __name__ == "__main__":
    unittest.main()
