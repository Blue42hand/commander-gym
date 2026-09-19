import unittest

from commander_gym.orchestration import (
    ArgentumOrchestrator,
    OrchestrationCompatibilityError,
    OrchestrationReconciliationError,
)


class FakeBackend:
    def __init__(self):
        self.health_payload = {"status": "ok"}
        self.status_payload = {
            "status": "ok",
            "service": "argentum-gym-server",
            "schemaHash": "schema-v1",
            "buildRevision": "abc123",
        }
        self.schema_payload = {"schemaHash": "schema-v1"}
        self.envs = {}
        self.calls = []

    def health(self):
        self.calls.append(("health",))
        return self.health_payload

    def status(self):
        self.calls.append(("status",))
        return self.status_payload

    def schema_hash(self):
        self.calls.append(("schema_hash",))
        return self.schema_payload

    def list_envs(self):
        self.calls.append(("list_envs",))
        return list(self.envs)

    def create_env(self, config):
        self.calls.append(("create_env", config))
        env_id = f"env-{len(self.envs) + 1}"
        observation = {"stateDigest": f"state-{len(self.envs) + 1}"}
        self.envs[env_id] = observation
        return {"envId": env_id, "observation": observation}

    def observe_env(self, env_id, *, reveal_all=None):
        self.calls.append(("observe_env", env_id, reveal_all))
        return self.envs[env_id]

    def step_env(self, env_id, action_id, *, params=None):
        self.calls.append(("step_env", env_id, action_id, params))
        self.envs[env_id] = {"stateDigest": f"stepped-{action_id}"}
        return self.envs[env_id]

    def submit_decision(self, env_id, response):
        self.calls.append(("submit_decision", env_id, response))
        self.envs[env_id] = {"stateDigest": "decision-applied"}
        return self.envs[env_id]

    def dispose_envs(self, env_ids):
        self.calls.append(("dispose_envs", tuple(env_ids)))
        for env_id in env_ids:
            self.envs.pop(env_id, None)


class ArgentumOrchestratorTests(unittest.TestCase):
    def test_inspect_proves_health_schema_and_build_compatibility(self):
        backend = FakeBackend()
        orchestrator = ArgentumOrchestrator(
            backend,
            expected_schema_hash="schema-v1",
            expected_build_revision="abc123",
        )

        inspection = orchestrator.inspect()

        self.assertEqual(inspection.health_status, "ok")
        self.assertEqual(inspection.identity.service, "argentum-gym-server")
        self.assertEqual(inspection.identity.schema_hash, "schema-v1")
        self.assertEqual(inspection.identity.build_revision, "abc123")
        self.assertEqual(
            backend.calls,
            [("health",), ("status",), ("schema_hash",)],
        )

    def test_inspect_fails_closed_when_status_and_schema_endpoint_disagree(self):
        backend = FakeBackend()
        backend.schema_payload = {"schemaHash": "schema-v2"}
        orchestrator = ArgentumOrchestrator(backend)

        with self.assertRaises(OrchestrationCompatibilityError):
            orchestrator.inspect()

    def test_contract_delegates_native_environment_operations_without_transport_assumptions(self):
        backend = FakeBackend()
        orchestrator = ArgentumOrchestrator(backend)

        created = orchestrator.create_environment({"players": [{"name": "A"}]})
        env_id = created["envId"]
        observed = orchestrator.observe_environment(env_id)
        stepped = orchestrator.step_environment(env_id, 7, params={"x": 1})
        decided = orchestrator.submit_decision(env_id, {"decisionId": "d1", "choice": 0})
        orchestrator.dispose_environment(env_id)

        self.assertEqual(observed["stateDigest"], "state-1")
        self.assertEqual(stepped["stateDigest"], "stepped-7")
        self.assertEqual(decided["stateDigest"], "decision-applied")
        self.assertNotIn(env_id, backend.envs)
        self.assertEqual(
            [call[0] for call in backend.calls],
            ["create_env", "observe_env", "step_env", "submit_decision", "dispose_envs"],
        )

    def test_reconcile_environment_reads_authoritative_state_without_retrying_mutation(self):
        backend = FakeBackend()
        backend.envs["env-1"] = {"stateDigest": "authoritative"}
        orchestrator = ArgentumOrchestrator(backend)

        existing = orchestrator.reconcile_environment("env-1")
        missing = orchestrator.reconcile_environment("env-missing")

        self.assertTrue(existing.exists)
        self.assertEqual(existing.observation["stateDigest"], "authoritative")
        self.assertFalse(missing.exists)
        self.assertIsNone(missing.observation)
        self.assertNotIn("step_env", [call[0] for call in backend.calls])
        self.assertNotIn("submit_decision", [call[0] for call in backend.calls])

    def test_reconcile_creation_fails_closed_unless_exactly_one_new_env_exists(self):
        backend = FakeBackend()
        backend.envs["old"] = {"stateDigest": "old"}
        orchestrator = ArgentumOrchestrator(backend)
        before = orchestrator.list_environments()

        backend.envs["new"] = {"stateDigest": "new"}
        reconciled = orchestrator.reconcile_creation(before)
        self.assertEqual(reconciled.env_id, "new")
        self.assertEqual(reconciled.observation["stateDigest"], "new")

        backend.envs["another"] = {"stateDigest": "another"}
        with self.assertRaises(OrchestrationReconciliationError):
            orchestrator.reconcile_creation(before)

    def test_create_requires_authoritative_environment_identity(self):
        class InvalidCreateBackend(FakeBackend):
            def create_env(self, config):
                return {"observation": {}}

        orchestrator = ArgentumOrchestrator(InvalidCreateBackend())
        with self.assertRaises(OrchestrationCompatibilityError):
            orchestrator.create_environment({})


if __name__ == "__main__":
    unittest.main()
