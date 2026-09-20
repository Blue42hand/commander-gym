"""Provider-neutral model-service endpoint configuration.

This module owns only connection configuration for an optional model service used by
Commander Gym.  It deliberately does not choose a provider, model, prompt, routing
policy, or pilot implementation; those remain artificial-player concerns.

The configuration is suitable for local services such as an OpenAI-compatible or
Ollama-compatible endpoint.  Loopback HTTP is allowed for same-host development.
Non-loopback endpoints must use HTTPS and bearer authentication so enabling a model
service does not silently create a new unauthenticated remote control surface.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping
from urllib.parse import urlparse


class ModelServiceConfigurationError(ValueError):
    """Raised when model-service connection settings are missing or unsafe."""


_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


@dataclass(frozen=True)
class ModelServiceEndpoint:
    """Transport-only configuration for an optional Commander Gym model service."""

    base_url: str
    bearer_token: str | None = field(default=None, repr=False)
    timeout: float = 60.0

    def __post_init__(self) -> None:
        if not isinstance(self.base_url, str) or not self.base_url.strip():
            raise ModelServiceConfigurationError("model-service base_url must not be blank")

        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ModelServiceConfigurationError(
                "model-service base_url must be an absolute http(s) URL"
            )
        if parsed.username or parsed.password:
            raise ModelServiceConfigurationError(
                "model-service credentials must not be embedded in the URL"
            )
        if parsed.query or parsed.fragment:
            raise ModelServiceConfigurationError(
                "model-service base_url must not contain a query or fragment"
            )

        is_loopback = parsed.hostname in _LOOPBACK_HOSTS
        if not is_loopback and parsed.scheme != "https":
            raise ModelServiceConfigurationError(
                "non-loopback model-service endpoints must use HTTPS"
            )
        if not is_loopback and not self.bearer_token:
            raise ModelServiceConfigurationError(
                "non-loopback model-service endpoints require bearer authentication"
            )
        if self.bearer_token is not None and not self.bearer_token.strip():
            raise ModelServiceConfigurationError("model-service bearer token must not be blank")
        if isinstance(self.timeout, bool) or not isinstance(self.timeout, (int, float)):
            raise ModelServiceConfigurationError("model-service timeout must be numeric")
        if self.timeout <= 0:
            raise ModelServiceConfigurationError("model-service timeout must be positive")

        object.__setattr__(self, "base_url", self.base_url.rstrip("/"))
        object.__setattr__(self, "timeout", float(self.timeout))


def model_service_from_environment(
    environment: Mapping[str, str],
) -> ModelServiceEndpoint | None:
    """Load an optional model-service endpoint from an explicit environment mapping.

    ``COMMANDER_GYM_MODEL_URL`` is the opt-in switch.  If it is absent, no model
    service is configured.  The token is intentionally separate from the URL and is
    never included in ``repr(ModelServiceEndpoint)``.
    """

    base_url = environment.get("COMMANDER_GYM_MODEL_URL")
    if base_url is None:
        return None

    token = environment.get("COMMANDER_GYM_MODEL_TOKEN")
    raw_timeout = environment.get("COMMANDER_GYM_MODEL_TIMEOUT", "60")
    try:
        timeout = float(raw_timeout)
    except (TypeError, ValueError) as exc:
        raise ModelServiceConfigurationError(
            "COMMANDER_GYM_MODEL_TIMEOUT must be numeric"
        ) from exc

    return ModelServiceEndpoint(
        base_url=base_url,
        bearer_token=token,
        timeout=timeout,
    )
