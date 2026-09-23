"""Operational service checks for a portable Commander Gym node.

This module owns deployment compatibility checks only. It does not choose pilots,
models, prompts, game policy, evidence schemas, or storage semantics.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
from typing import Any, Callable, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from .argentum_client import ArgentumClientError, ArgentumGymClient
from .model_service import (
    ModelServiceConfigurationError,
    ModelServiceEndpoint,
    model_service_from_environment,
)
from .orchestration import ArgentumOrchestrator


@dataclass(frozen=True)
class ArgentumServiceHealth:
    ok: bool
    service: str | None = None
    schema_hash: str | None = None
    build_revision: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class ModelServiceHealth:
    configured: bool
    ok: bool
    base_url: str | None = None
    health_url: str | None = None
    http_status: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class ServiceHealth:
    argentum: ArgentumServiceHealth
    model_service: ModelServiceHealth

    @property
    def ok(self) -> bool:
        return self.argentum.ok and self.model_service.ok

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "argentum": asdict(self.argentum),
            "model_service": asdict(self.model_service),
        }


def check_argentum_service(
    client: ArgentumGymClient,
    *,
    expected_schema_hash: str | None = None,
    expected_build_revision: str | None = None,
) -> ArgentumServiceHealth:
    """Verify Argentum health plus service/schema/build compatibility."""

    try:
        inspection = ArgentumOrchestrator(
            client,
            expected_schema_hash=expected_schema_hash,
            expected_build_revision=expected_build_revision,
        ).inspect()
    except Exception as exc:  # fail closed into a serializable doctor result
        return ArgentumServiceHealth(ok=False, error=str(exc))

    return ArgentumServiceHealth(
        ok=True,
        service=inspection.identity.service,
        schema_hash=inspection.identity.schema_hash,
        build_revision=inspection.identity.build_revision,
    )


def _model_health_url(endpoint: ModelServiceEndpoint, health_path: str) -> str:
    if not isinstance(health_path, str) or not health_path.strip():
        raise ModelServiceConfigurationError("model health path must not be blank")
    if "://" in health_path or "?" in health_path or "#" in health_path:
        raise ModelServiceConfigurationError(
            "model health path must be a relative path without query or fragment"
        )
    base = endpoint.base_url.rstrip("/") + "/"
    return urljoin(base, health_path.lstrip("/"))


def check_model_service(
    endpoint: ModelServiceEndpoint | None,
    *,
    health_path: str = "models",
    opener: Callable[..., Any] = urlopen,
) -> ModelServiceHealth:
    """Probe an optional model endpoint without assuming provider-specific payloads.

    The probe only requires a successful HTTP response from one explicitly selected
    endpoint path. It does not inspect model lists or infer provider/model identity.
    """

    if endpoint is None:
        return ModelServiceHealth(configured=False, ok=True)

    health_url: str | None = None
    try:
        health_url = _model_health_url(endpoint, health_path)
        headers = {"Accept": "application/json"}
        if endpoint.bearer_token:
            headers["Authorization"] = f"Bearer {endpoint.bearer_token}"
        request = Request(health_url, headers=headers, method="GET")
        with opener(request, timeout=endpoint.timeout) as response:
            status = int(response.getcode())
        if not (200 <= status < 300):
            return ModelServiceHealth(
                configured=True,
                ok=False,
                base_url=endpoint.base_url,
                health_url=health_url,
                http_status=status,
                error=f"model health endpoint returned HTTP {status}",
            )
        return ModelServiceHealth(
            configured=True,
            ok=True,
            base_url=endpoint.base_url,
            health_url=health_url,
            http_status=status,
        )
    except HTTPError as exc:
        return ModelServiceHealth(
            configured=True,
            ok=False,
            base_url=endpoint.base_url,
            health_url=health_url,
            http_status=exc.code,
            error=f"model health endpoint returned HTTP {exc.code}",
        )
    except (URLError, OSError, ValueError) as exc:
        return ModelServiceHealth(
            configured=True,
            ok=False,
            base_url=endpoint.base_url,
            health_url=health_url,
            error=str(exc),
        )


def check_services(
    argentum_client: ArgentumGymClient,
    *,
    expected_schema_hash: str | None = None,
    expected_build_revision: str | None = None,
    model_endpoint: ModelServiceEndpoint | None = None,
    model_health_path: str = "models",
    model_opener: Callable[..., Any] = urlopen,
) -> ServiceHealth:
    return ServiceHealth(
        argentum=check_argentum_service(
            argentum_client,
            expected_schema_hash=expected_schema_hash,
            expected_build_revision=expected_build_revision,
        ),
        model_service=check_model_service(
            model_endpoint,
            health_path=model_health_path,
            opener=model_opener,
        ),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check Commander Gym node service compatibility and reachability."
    )
    parser.add_argument(
        "--argentum-url",
        default=os.environ.get("COMMANDER_GYM_ARGENTUM_URL"),
        help="Argentum gym-server/gateway base URL",
    )
    parser.add_argument(
        "--argentum-token-env",
        default="COMMANDER_GYM_ARGENTUM_TOKEN",
        help="environment variable containing the Argentum bearer token",
    )
    parser.add_argument("--expected-schema-hash")
    parser.add_argument("--expected-build-revision")
    parser.add_argument(
        "--model-health-path",
        default=os.environ.get("COMMANDER_GYM_MODEL_HEALTH_PATH", "models"),
        help="path beneath COMMANDER_GYM_MODEL_URL used for a provider-neutral HTTP probe",
    )
    parser.add_argument("--argentum-timeout", type=float, default=10.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.argentum_url:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "--argentum-url or COMMANDER_GYM_ARGENTUM_URL is required",
                },
                sort_keys=True,
            )
        )
        return 2

    try:
        client = ArgentumGymClient(
            args.argentum_url,
            bearer_token=os.environ.get(args.argentum_token_env),
            timeout=args.argentum_timeout,
        )
        model_endpoint = model_service_from_environment(os.environ)
    except (ArgentumClientError, ModelServiceConfigurationError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 2

    health = check_services(
        client,
        expected_schema_hash=args.expected_schema_hash,
        expected_build_revision=args.expected_build_revision,
        model_endpoint=model_endpoint,
        model_health_path=args.model_health_path,
    )
    print(json.dumps(health.to_dict(), indent=2, sort_keys=True))
    return 0 if health.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
