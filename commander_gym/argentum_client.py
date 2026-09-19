"""Thin fail-closed client for a persistent Argentum ``gym-server``.

This module deliberately exposes only the Milestone-1 control surface Commander Gym
needs. Rules, legality, environment lifecycle, and observations remain Argentum-owned.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen


class ArgentumClientError(RuntimeError):
    """Base class for transport/client failures."""


class ArgentumClientConfigurationError(ArgentumClientError):
    """Raised when a client configuration would expose an unsafe connection."""


class ArgentumRemoteError(ArgentumClientError):
    """Raised when the remote service returns an explicit HTTP error."""

    def __init__(self, status: int, message: str):
        super().__init__(f"Argentum service returned HTTP {status}: {message}")
        self.status = status
        self.message = message


class ArgentumConnectionError(ArgentumClientError):
    """Raised when a read-only request cannot reach the service."""


class ArgentumDeliveryUnknownError(ArgentumClientError):
    """Raised when a mutating request may or may not have reached the service.

    Callers must reconcile authoritative state before attempting another mutation.
    """


class ArgentumGymClient:
    """Narrow Commander Gym client for Argentum's HTTP gym-server.

    Remote endpoints must use HTTPS and bearer authentication. Plain HTTP without a
    token is accepted only for loopback development, where the Argentum server itself
    is expected to remain bound to localhost.
    """

    def __init__(self, base_url: str, *, bearer_token: str | None = None, timeout: float = 10.0):
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ArgentumClientConfigurationError("base_url must be an absolute http(s) URL")

        is_loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        if not is_loopback and parsed.scheme != "https":
            raise ArgentumClientConfigurationError("non-loopback Argentum endpoints must use HTTPS")
        if not is_loopback and not bearer_token:
            raise ArgentumClientConfigurationError(
                "non-loopback Argentum endpoints require bearer authentication"
            )
        if bearer_token is not None and not bearer_token.strip():
            raise ArgentumClientConfigurationError("bearer_token must not be blank")
        if timeout <= 0:
            raise ArgentumClientConfigurationError("timeout must be positive")

        self.base_url = base_url.rstrip("/")
        self.bearer_token = bearer_token
        self.timeout = timeout

    def health(self) -> Mapping[str, Any]:
        return self._request("GET", "/health")

    def schema_hash(self) -> Mapping[str, Any]:
        return self._request("GET", "/schema-hash")

    def list_envs(self) -> Sequence[str]:
        payload = self._request("GET", "/envs")
        if not isinstance(payload, list):
            raise ArgentumClientError("Argentum /envs response must be an array")
        return payload

    def create_env(self, config: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._request("POST", "/envs", body=config, mutating=True)

    def observe_env(self, env_id: str, *, reveal_all: bool | None = None) -> Mapping[str, Any]:
        path = f"/envs/{quote(env_id, safe='')}"
        if reveal_all is not None:
            path += "?" + urlencode({"revealAll": str(reveal_all).lower()})
        return self._request("GET", path)

    def step_env(
        self,
        env_id: str,
        action_id: int,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        if type(action_id) is not int:
            raise ArgentumClientConfigurationError("action_id must be an integer")
        body = {"actionId": action_id, "params": dict(params or {})}
        return self._request(
            "POST",
            f"/envs/{quote(env_id, safe='')}/step",
            body=body,
            mutating=True,
        )

    def dispose_envs(self, env_ids: Sequence[str]) -> None:
        self._request("DELETE", "/envs", body={"envIds": list(env_ids)}, mutating=True)

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: Mapping[str, Any] | None = None,
        mutating: bool = False,
    ) -> Any:
        headers = {"Accept": "application/json"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"

        request = Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            raw = exc.read()
            message = raw.decode("utf-8", errors="replace") if raw else str(exc.reason)
            raise ArgentumRemoteError(exc.code, message) from exc
        except URLError as exc:
            if mutating:
                raise ArgentumDeliveryUnknownError(
                    "mutating request transport failed; delivery/application state is unknown"
                ) from exc
            raise ArgentumConnectionError(f"could not reach Argentum service: {exc.reason}") from exc
        except OSError as exc:
            if mutating:
                raise ArgentumDeliveryUnknownError(
                    "mutating request transport failed; delivery/application state is unknown"
                ) from exc
            raise ArgentumConnectionError(f"could not reach Argentum service: {exc}") from exc

        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArgentumClientError("Argentum service returned invalid JSON") from exc
