import unittest

from commander_gym.argentum_issue_relay import (
    ArgentumRelay,
    ArgentumRelayCommandError,
    parse_comment,
)


class FakeClient:
    def __init__(self):
        self.calls = []

    def status(self):
        self.calls.append(("status",))
        return {"status": "ok"}

    def health(self):
        self.calls.append(("health",))
        return {"status": "ok"}

    def schema_hash(self):
        self.calls.append(("schema",))
        return {"schemaHash": "x"}

    def list_envs(self):
        self.calls.append(("list",))
        return ["env-1"]

    def create_env(self, config):
        self.calls.append(("create", dict(config)))
        return {"envId": "env-1"}

    def observe_env(self, env_id, reveal_all=None):
        self.calls.append(("observe", env_id, reveal_all))
        return {"envId": env_id, "legalActions": [{"actionId": 7}]}

    def step_env(self, env_id, action_id, params=None):
        self.calls.append(("step", env_id, action_id, dict(params or {})))
        return {"envId": env_id, "stateDigest": "b"}

    def dispose_envs(self, env_ids):
        self.calls.append(("dispose", list(env_ids)))


class ArgentumRelayTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()
        self.relay = ArgentumRelay(self.client)

    def test_parse_requires_exact_prefix_and_object(self):
        self.assertEqual(parse_comment('/argentum {"op":"status"}'), {"op": "status"})
        with self.assertRaises(ArgentumRelayCommandError):
            parse_comment('argentum {"op":"status"}')
        with self.assertRaises(ArgentumRelayCommandError):
            parse_comment('/argentum []')

    def test_create_observe_step_dispose_are_strict(self):
        self.assertEqual(
            self.relay.execute({"op": "create", "config": {"players": [{"name": "A"}]}}),
            {"envId": "env-1"},
        )
        self.relay.execute({"op": "observe", "envId": "env-1", "revealAll": False})
        self.relay.execute({"op": "step", "envId": "env-1", "actionId": 7, "params": {}})
        self.assertEqual(
            self.relay.execute({"op": "dispose", "envIds": ["env-1"]}),
            {"disposed": ["env-1"]},
        )
        self.assertEqual(
            self.client.calls,
            [
                ("create", {"players": [{"name": "A"}]}),
                ("observe", "env-1", False),
                ("step", "env-1", 7, {}),
                ("dispose", ["env-1"]),
            ],
        )

    def test_unknown_operations_and_extra_keys_fail_closed(self):
        with self.assertRaises(ArgentumRelayCommandError):
            self.relay.execute({"op": "shell", "command": "whoami"})
        with self.assertRaises(ArgentumRelayCommandError):
            self.relay.execute({"op": "status", "url": "https://example.com"})
        with self.assertRaises(ArgentumRelayCommandError):
            self.relay.execute({"op": "step", "envId": "e", "actionId": "7"})


if __name__ == "__main__":
    unittest.main()
