"""Explicit local-only llmster configuration and cancellable HTTP transport.

No OpenAI environment, SDK, credentials, proxies, DNS or redirects are used.
Discovery never loads/unloads models. A selected model must already be loaded.
"""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import json
import math
from pathlib import Path
import subprocess
import sys
import threading
from time import monotonic
from urllib.parse import urlsplit
from typing import Any, Mapping


class LMStudioError(RuntimeError):
    def __init__(self, code: str, *, status: int | None = None):
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class LMStudioConfig:
    endpoint: str
    model: str
    context_window: int
    max_prompt_bytes: int
    max_output_tokens: int = 1024
    template_reserve_tokens: int = 1024
    timeout_seconds: float = 30.0
    max_attempts: int = 2
    provider: str = "lmstudio"

    def __post_init__(self):
        if self.provider != "lmstudio":
            raise ValueError("local configuration requires provider lmstudio")
        try:
            url = urlsplit(self.endpoint)
            address = ipaddress.ip_address(url.hostname or "")
            allowed = address.is_loopback or any(address in network for network in (
                ipaddress.ip_network("10.0.0.0/8"), ipaddress.ip_network("172.16.0.0/12"),
                ipaddress.ip_network("192.168.0.0/16"), ipaddress.ip_network("fc00::/7"),
            ) if address.version == network.version)
            if (url.scheme not in {"http", "https"} or not allowed
                or address.is_unspecified or address.is_multicast or not url.port
                or "%" in (url.hostname or "")
                or url.username is not None or url.password is not None
                or url.path not in {"", "/"} or url.query or url.fragment
                or self.endpoint != self.endpoint.strip()):
                raise ValueError()
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("endpoint requires an explicit loopback/RFC1918/ULA IP and port origin") from exc
        if not isinstance(self.model, str) or not self.model.strip() or self.model != self.model.strip():
            raise ValueError("model must be an exact discovered nonempty identifier")
        for name in ("context_window", "max_prompt_bytes", "max_output_tokens", "template_reserve_tokens"):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.max_prompt_bytes + self.max_output_tokens + self.template_reserve_tokens > self.context_window:
            raise ValueError("prompt byte ceiling plus output and template reserves exceeds context")
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 2:
            raise ValueError("max_attempts must be 1 or 2")
        if (type(self.timeout_seconds) not in (int, float)
            or not math.isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 100):
            raise ValueError("whole-choice timeout must be finite and at most 100 seconds")


class LMStudioTransport:
    def __init__(self, config: LMStudioConfig):
        self.config = config

    def request(self, path: str, body: Mapping[str, Any] | None, *,
                timeout: float, cancel: threading.Event | None = None) -> dict[str, Any]:
        if path not in {"/api/v1/models", "/v1/chat/completions"}:
            raise LMStudioError("unsupported_endpoint")
        if timeout <= 0 or not math.isfinite(timeout):
            raise LMStudioError("deadline_exceeded")
        if cancel is not None and cancel.is_set():
            raise LMStudioError("cancelled")
        envelope = json.dumps({"url": self.config.endpoint.rstrip("/") + path,
                               "body": body, "timeout": timeout}, allow_nan=False).encode()
        # Absolute script path works outside a checkout cwd. -I and a fresh environment
        # prevent Python hooks, API keys, proxy settings or SDK defaults leaking in.
        try:
            child = subprocess.Popen(
                [sys.executable, "-I", str(Path(__file__).with_name("lmstudio_http_worker.py"))],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env={},
            )
        except OSError as exc:
            raise LMStudioError("worker_start_failed") from exc
        deadline = monotonic() + timeout
        try:
            data = envelope
            while True:
                if cancel is not None and cancel.is_set():
                    raise LMStudioError("cancelled")
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise LMStudioError("deadline_exceeded")
                try:
                    output, _ = child.communicate(data, timeout=min(0.05, remaining))
                    break
                except subprocess.TimeoutExpired:
                    data = None
            if cancel is not None and cancel.is_set():
                raise LMStudioError("cancelled")
            if child.returncode != 0:
                raise LMStudioError("worker_failed")
            try:
                result = json.loads(output)
            except (ValueError, TypeError) as exc:
                raise LMStudioError("invalid_worker_response") from exc
            if not isinstance(result, dict):
                raise LMStudioError("invalid_worker_response")
            if result.get("error"):
                code = result["error"]
                if code not in {"http_error", "response_too_large", "invalid_response_or_transport_failure"}:
                    raise LMStudioError("invalid_worker_response")
                status = result.get("status")
                raise LMStudioError(code, status=status if type(status) is int else None)
            response = result.get("response")
            if not isinstance(response, dict):
                raise LMStudioError("invalid_response")
            return response
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate()


def require_loaded_model(discovery: Mapping[str, Any], config: LMStudioConfig) -> dict[str, Any]:
    models = discovery.get("models")
    if not isinstance(models, list):
        raise LMStudioError("invalid_model_discovery")
    matches = []
    for model in models:
        if not isinstance(model, dict):
            raise LMStudioError("invalid_model_discovery")
        if model.get("type") != "llm":
            continue
        instances = model.get("loaded_instances")
        if not isinstance(instances, list):
            raise LMStudioError("invalid_model_discovery")
        for instance in instances:
            if not isinstance(instance, dict):
                raise LMStudioError("invalid_model_discovery")
            if instance.get("id") == config.model:
                matches.append((model, instance))
    if len(matches) != 1:
        raise LMStudioError("selected_model_not_uniquely_loaded")
    model, instance = matches[0]
    settings = instance.get("config")
    capabilities = model.get("capabilities", {})
    if not isinstance(settings, dict) or not isinstance(capabilities, dict):
        raise LMStudioError("invalid_model_discovery")
    context = settings.get("context_length")
    if type(context) is not int or context < config.context_window:
        raise LMStudioError("loaded_context_below_configured_budget")
    reasoning = capabilities.get("reasoning", {})
    if not isinstance(reasoning, dict) or (reasoning and not isinstance(reasoning.get("allowed_options"), list)):
        raise LMStudioError("invalid_model_discovery")
    if reasoning and "off" not in reasoning["allowed_options"]:
        raise LMStudioError("model_cannot_disable_reasoning")
    return {"modelKey": model.get("key"), "loadedInstanceId": instance["id"],
            "loadedContextLength": context, "format": model.get("format"),
            "quantization": model.get("quantization")}
