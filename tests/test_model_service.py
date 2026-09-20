from __future__ import annotations

import unittest

from commander_gym.model_service import (
    ModelServiceConfigurationError,
    ModelServiceEndpoint,
    model_service_from_environment,
)


class ModelServiceEndpointTests(unittest.TestCase):
    def test_unset_environment_leaves_model_service_disabled(self) -> None:
        self.assertIsNone(model_service_from_environment({}))

    def test_loopback_http_is_allowed_without_authentication(self) -> None:
        endpoint = model_service_from_environment(
            {
                "COMMANDER_GYM_MODEL_URL": "http://127.0.0.1:11434/v1/",
                "COMMANDER_GYM_MODEL_TIMEOUT": "15",
            }
        )

        assert endpoint is not None
        self.assertEqual(endpoint.base_url, "http://127.0.0.1:11434/v1")
        self.assertIsNone(endpoint.bearer_token)
        self.assertEqual(endpoint.timeout, 15.0)

    def test_remote_endpoint_requires_https_and_bearer_authentication(self) -> None:
        with self.assertRaisesRegex(
            ModelServiceConfigurationError,
            "must use HTTPS",
        ):
            ModelServiceEndpoint("http://models.example.test/v1", bearer_token="secret")

        with self.assertRaisesRegex(
            ModelServiceConfigurationError,
            "require bearer authentication",
        ):
            ModelServiceEndpoint("https://models.example.test/v1")

        endpoint = ModelServiceEndpoint(
            "https://models.example.test/v1/",
            bearer_token="secret",
        )
        self.assertEqual(endpoint.base_url, "https://models.example.test/v1")

    def test_credentials_do_not_appear_in_repr(self) -> None:
        endpoint = ModelServiceEndpoint(
            "https://models.example.test/v1",
            bearer_token="top-secret-token",
        )

        self.assertNotIn("top-secret-token", repr(endpoint))
        self.assertNotIn("bearer_token", repr(endpoint))

    def test_url_credentials_query_and_fragment_are_rejected(self) -> None:
        unsafe_urls = (
            "https://user:pass@models.example.test/v1",
            "https://models.example.test/v1?token=secret",
            "https://models.example.test/v1#secret",
        )
        for url in unsafe_urls:
            with self.subTest(url=url):
                with self.assertRaises(ModelServiceConfigurationError):
                    ModelServiceEndpoint(url, bearer_token="secret")

    def test_blank_token_and_invalid_timeout_fail_closed(self) -> None:
        with self.assertRaisesRegex(ModelServiceConfigurationError, "must not be blank"):
            ModelServiceEndpoint(
                "https://models.example.test/v1",
                bearer_token="   ",
            )

        for timeout in (0, -1, True):
            with self.subTest(timeout=timeout):
                with self.assertRaises(ModelServiceConfigurationError):
                    ModelServiceEndpoint(
                        "http://localhost:11434",
                        timeout=timeout,
                    )

        with self.assertRaisesRegex(
            ModelServiceConfigurationError,
            "COMMANDER_GYM_MODEL_TIMEOUT must be numeric",
        ):
            model_service_from_environment(
                {
                    "COMMANDER_GYM_MODEL_URL": "http://localhost:11434",
                    "COMMANDER_GYM_MODEL_TIMEOUT": "not-a-number",
                }
            )


if __name__ == "__main__":
    unittest.main()
