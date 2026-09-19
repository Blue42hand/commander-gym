import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from commander_gym.argentum_client import (
    ArgentumClientConfigurationError,
    ArgentumDeliveryUnknownError,
    ArgentumGymClient,
    ArgentumRemoteError,
)


class FakeResponse:
    def __init__(self, payload=None):
        self._raw = b"" if payload is None else json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._raw


class ArgentumGymClientTests(unittest.TestCase):
    def test_remote_endpoint_requires_https_and_authentication(self):
        with self.assertRaises(ArgentumClientConfigurationError):
            ArgentumGymClient("http://example.com:8081", bearer_token="secret")
        with self.assertRaises(ArgentumClientConfigurationError):
            ArgentumGymClient("https://example.com")

        client = ArgentumGymClient("http://127.0.0.1:8081")
        self.assertEqual(client.base_url, "http://127.0.0.1:8081")

    @patch("commander_gym.argentum_client.urlopen")
    def test_health_uses_gateway_bearer_token(self, mocked_urlopen):
        mocked_urlopen.return_value = FakeResponse({"status": "ok"})
        client = ArgentumGymClient("https://gym.example.test", bearer_token="secret")

        self.assertEqual(client.health(), {"status": "ok"})

        request = mocked_urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://gym.example.test/health")
        self.assertEqual(request.method, "GET")
        self.assertEqual(request.headers["Authorization"], "Bearer secret")
        self.assertEqual(mocked_urlopen.call_count, 1)

    @patch("commander_gym.argentum_client.urlopen")
    def test_create_observe_step_and_dispose_match_argentum_surface(self, mocked_urlopen):
        mocked_urlopen.side_effect = [
            FakeResponse({"envId": "env-1", "observation": {"stateDigest": "a"}}),
            FakeResponse({"stateDigest": "a", "legalActions": [{"actionId": 7}]}),
            FakeResponse({"stateDigest": "b", "legalActions": [{"actionId": 9}]}),
            FakeResponse(None),
        ]
        client = ArgentumGymClient("http://localhost:8081")

        created = client.create_env({"players": [{"name": "A"}, {"name": "B"}]})
        observed = client.observe_env("env-1")
        stepped = client.step_env("env-1", 7)
        client.dispose_envs(["env-1"])

        self.assertEqual(created["envId"], "env-1")
        self.assertEqual(observed["stateDigest"], "a")
        self.assertEqual(stepped["stateDigest"], "b")

        methods = [call.args[0].method for call in mocked_urlopen.call_args_list]
        urls = [call.args[0].full_url for call in mocked_urlopen.call_args_list]
        self.assertEqual(methods, ["POST", "GET", "POST", "DELETE"])
        self.assertEqual(
            urls,
            [
                "http://localhost:8081/envs",
                "http://localhost:8081/envs/env-1",
                "http://localhost:8081/envs/env-1/step",
                "http://localhost:8081/envs",
            ],
        )

    @patch("commander_gym.argentum_client.urlopen")
    def test_explicit_remote_rejection_is_not_retried(self, mocked_urlopen):
        mocked_urlopen.side_effect = HTTPError(
            "http://localhost:8081/envs/env-1/step",
            400,
            "Bad Request",
            {},
            io.BytesIO(b'{"code":"invalid_action","message":"stale action"}'),
        )
        client = ArgentumGymClient("http://localhost:8081")

        with self.assertRaises(ArgentumRemoteError) as raised:
            client.step_env("env-1", 7)

        self.assertEqual(raised.exception.status, 400)
        self.assertIn("stale action", raised.exception.message)
        self.assertEqual(mocked_urlopen.call_count, 1)

    @patch("commander_gym.argentum_client.urlopen")
    def test_mutating_transport_failure_has_unknown_delivery_and_no_retry(self, mocked_urlopen):
        mocked_urlopen.side_effect = URLError("connection reset")
        client = ArgentumGymClient("http://localhost:8081")

        with self.assertRaises(ArgentumDeliveryUnknownError):
            client.step_env("env-1", 7)

        self.assertEqual(mocked_urlopen.call_count, 1)


if __name__ == "__main__":
    unittest.main()
