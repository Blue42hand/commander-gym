import unittest
from urllib.error import URLError

from commander_gym.model_service import ModelServiceEndpoint
from commander_gym.service_doctor import (
    check_argentum_service,
    check_model_service,
    check_services,
)


class FakeArgentumBackend:
    def __init__(self, *, schema_hash="schema-v1", health="ok"):
        self.schema_hash_value = schema_hash
        self.health_value = health

    def health(self):
        return {"status": self.health_value}

    def status(self):
        return {
            "service": "argentum-gym-server",
            "schemaHash": self.schema_hash_value,
            "buildRevision": "build-123",
        }

    def schema_hash(self):
        return {"schemaHash": self.schema_hash_value}


class FakeResponse:
    def __init__(self, status=200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def getcode(self):
        return self.status


class ServiceDoctorTests(unittest.TestCase):
    def test_argentum_accepts_matching_schema_and_build(self):
        health = check_argentum_service(
            FakeArgentumBackend(),
            expected_schema_hash="schema-v1",
            expected_build_revision="build-123",
        )

        self.assertTrue(health.ok)
        self.assertEqual(health.service, "argentum-gym-server")
        self.assertEqual(health.schema_hash, "schema-v1")
        self.assertEqual(health.build_revision, "build-123")
        self.assertIsNone(health.error)

    def test_argentum_fails_closed_on_schema_mismatch(self):
        health = check_argentum_service(
            FakeArgentumBackend(),
            expected_schema_hash="schema-v2",
        )

        self.assertFalse(health.ok)
        self.assertIn("unexpected Argentum schema", health.error)

    def test_disabled_model_service_is_healthy(self):
        health = check_model_service(None)

        self.assertFalse(health.configured)
        self.assertTrue(health.ok)
        self.assertIsNone(health.base_url)

    def test_model_probe_uses_configured_auth_without_exposing_token(self):
        endpoint = ModelServiceEndpoint(
            "https://models.example.test/v1",
            bearer_token="secret-token",
        )
        captured = {}

        def opener(request, *, timeout):
            captured["url"] = request.full_url
            captured["authorization"] = request.get_header("Authorization")
            captured["timeout"] = timeout
            return FakeResponse(200)

        health = check_model_service(endpoint, opener=opener)

        self.assertTrue(health.ok)
        self.assertTrue(health.configured)
        self.assertEqual(captured["url"], "https://models.example.test/v1/models")
        self.assertEqual(captured["authorization"], "Bearer secret-token")
        self.assertEqual(captured["timeout"], 60.0)
        self.assertNotIn("secret-token", repr(health))

    def test_model_probe_fails_closed_on_connection_error(self):
        endpoint = ModelServiceEndpoint("http://127.0.0.1:11434/v1")

        def opener(_request, *, timeout):
            self.assertEqual(timeout, 60.0)
            raise URLError("connection refused")

        health = check_model_service(endpoint, opener=opener)

        self.assertFalse(health.ok)
        self.assertTrue(health.configured)
        self.assertIn("connection refused", health.error)

    def test_combined_health_requires_both_services(self):
        endpoint = ModelServiceEndpoint("http://127.0.0.1:11434/v1")

        health = check_services(
            FakeArgentumBackend(health="degraded"),
            model_endpoint=endpoint,
            model_opener=lambda *_args, **_kwargs: FakeResponse(200),
        )

        self.assertFalse(health.ok)
        self.assertFalse(health.argentum.ok)
        self.assertTrue(health.model_service.ok)


if __name__ == "__main__":
    unittest.main()
