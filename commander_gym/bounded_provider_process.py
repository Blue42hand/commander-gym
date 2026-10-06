"""Cancellable whole-request boundary for the opt-in provider recovery Pilot.

The SDK's scalar timeout is an inactivity timeout. A child process gives the
game-server callback a real wall-clock limit: on expiry the child is killed and
reaped before control returns, leaving the already-reserved request ambiguous.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any


class BoundedProviderProcessError(RuntimeError):
    def __init__(self, reason: str, *, status_code: int | None = None,
                 code: str | None = None) -> None:
        super().__init__(reason)
        self.status_code = status_code
        self.body = {"error": {"code": code}} if code is not None else None


class _ResponseDict(dict):
    """Preserve SDK-style attribute access for existing budget settlement."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


class BoundedProcessResponses:
    def __init__(self, api_key: str, *,
                 worker_module: str = "commander_gym.openai_deadline_worker") -> None:
        if not isinstance(api_key, str) or not api_key:
            raise ValueError("bounded provider worker requires the existing API key")
        self._api_key = api_key
        self._worker_module = worker_module

    def create(self, **request: Any) -> dict[str, Any]:
        timeout = request.pop("timeout", None)
        if type(timeout) not in (float, int) or timeout <= 0:
            raise BoundedProviderProcessError("missing whole-request deadline")
        envelope = json.dumps({"request": request, "timeout": timeout},
                              separators=(",", ":")).encode()
        env = os.environ.copy()
        env["OPENAI_API_KEY"] = self._api_key
        child = subprocess.Popen(
            [sys.executable, "-m", self._worker_module],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=env,
        )
        try:
            output, _ = child.communicate(envelope, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            child.kill()
            child.communicate()
            raise BoundedProviderProcessError("whole-request deadline expired") from exc
        if child.returncode != 0:
            raise BoundedProviderProcessError("provider worker exited without a response")
        try:
            result = json.loads(output)
        except (ValueError, TypeError) as exc:
            raise BoundedProviderProcessError("provider worker returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise BoundedProviderProcessError("provider worker returned an invalid result")
        if result.get("kind") == "error":
            status = result.get("status_code")
            code = result.get("code")
            raise BoundedProviderProcessError(
                "provider request failed", status_code=status if type(status) is int else None,
                code=code if isinstance(code, str) else None,
            )
        response = result.get("response")
        if result.get("kind") != "response" or not isinstance(response, dict):
            raise BoundedProviderProcessError("provider worker returned an invalid response")
        return _ResponseDict(response)


class BoundedProcessClient:
    def __init__(self, api_key: str) -> None:
        self.responses = BoundedProcessResponses(api_key)
