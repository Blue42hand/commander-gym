"""Loopback-only bearer-authenticated gateway for Argentum gym-server.

This is intentionally a narrow Milestone-1 control surface. It does not implement
rules, legality, or environment semantics; it only authenticates and allowlists a
small subset of Argentum's existing HTTP API before forwarding to a loopback
gym-server.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen


MAX_REQUEST_BYTES = 1024 * 1024
_ENV_PATH = re.compile(r"^/envs/[^/]+$")
_STEP_PATH = re.compile(r"^/envs/[^/]+/step$")
_DECISION_PATH = re.compile(r"^/envs/[^/]+/decision$")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class ArgentumGatewayConfigurationError(RuntimeError):
    """Raised when the gateway would start with an unsafe configuration."""


@dataclass(frozen=True)
class ArgentumGatewayConfig:
    token: str
    upstream_url: str = "http://127.0.0.1:8081"
    bind_host: str = "127.0.0.1"
    port: int = 8082

    def __post_init__(self) -> None:
        if not self.token or not self.token.strip():
            raise ArgentumGatewayConfigurationError("gateway bearer token must not be blank")
        if self.bind_host not in _LOOPBACK_HOSTS:
            raise ArgentumGatewayConfigurationError("gateway must bind to a loopback address")
        if not (1 <= self.port <= 65535):
            raise ArgentumGatewayConfigurationError("gateway port must be between 1 and 65535")

        parsed = urlparse(self.upstream_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in _LOOPBACK_HOSTS
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ArgentumGatewayConfigurationError(
                "upstream must be a bare loopback http URL such as http://127.0.0.1:8081"
            )


def _allowed_request(method: str, raw_path: str) -> bool:
    parsed = urlparse(raw_path)
    path = parsed.path
    query = parse_qs(parsed.query, keep_blank_values=True)

    if (method, path) in {
        ("GET", "/health"),
        ("GET", "/status"),
        ("GET", "/schema-hash"),
        ("GET", "/envs"),
        ("POST", "/envs"),
        ("DELETE", "/envs"),
    }:
        return not query

    if method == "GET" and _ENV_PATH.fullmatch(path):
        if not query:
            return True
        if not set(query).issubset({"revealAll", "perspectivePlayerId"}):
            return False
        if "revealAll" in query and (
            len(query["revealAll"]) != 1
            or query["revealAll"][0] not in {"true", "false"}
        ):
            return False
        if "perspectivePlayerId" in query and (
            len(query["perspectivePlayerId"]) != 1
            or not query["perspectivePlayerId"][0]
        ):
            return False
        return True

    if method == "POST" and (_STEP_PATH.fullmatch(path) or _DECISION_PATH.fullmatch(path)):
        return not query

    return False


class ArgentumGatewayServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], config: ArgentumGatewayConfig):
        self.gateway_config = config
        super().__init__(server_address, ArgentumGatewayHandler)


class ArgentumGatewayHandler(BaseHTTPRequestHandler):
    server_version = "CommanderGymArgentumGateway/1"

    def do_GET(self) -> None:  # noqa: N802
        self._handle()

    def do_POST(self) -> None:  # noqa: N802
        self._handle()

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle()

    def do_PUT(self) -> None:  # noqa: N802
        self._reject_not_found()

    def do_PATCH(self) -> None:  # noqa: N802
        self._reject_not_found()

    def log_message(self, _format: str, *_args: Any) -> None:
        """Suppress BaseHTTPRequestHandler's unstructured access log.

        Every handled request emits one structured diagnostic record instead.
        """

    def _handle(self) -> None:
        started_at = time.monotonic()
        request_id = self._request_id()
        config = self.server.gateway_config  # type: ignore[attr-defined]

        supplied = self.headers.get("Authorization", "")
        expected = f"Bearer {config.token}"
        if not hmac.compare_digest(supplied, expected):
            self._diagnostic(request_id, "gateway_unauthorized", 401, started_at)
            self._write_json(401, {"error": "unauthorized"}, request_id=request_id)
            return

        if not _allowed_request(self.command, self.path):
            self._diagnostic(request_id, "gateway_route_rejected", 404, started_at)
            self._write_json(404, {"error": "not_found"}, request_id=request_id)
            return

        try:
            body = self._read_body()
        except ValueError as exc:
            self._diagnostic(request_id, "gateway_request_rejected", 413, started_at)
            self._write_json(413, {"error": str(exc)}, request_id=request_id)
            return

        target = config.upstream_url.rstrip("/") + self.path
        headers = {"Accept": "application/json", "X-Request-ID": request_id}
        content_type = self.headers.get("Content-Type")
        if body is not None:
            if content_type and content_type.split(";", 1)[0].strip().lower() != "application/json":
                self._diagnostic(request_id, "gateway_request_rejected", 415, started_at)
                self._write_json(
                    415,
                    {"error": "mutating requests must use application/json"},
                    request_id=request_id,
                )
                return
            headers["Content-Type"] = "application/json"

        request = Request(target, data=body, headers=headers, method=self.command)
        try:
            with urlopen(request, timeout=15) as response:
                payload = response.read()
                status = response.status
                response_type = response.headers.get("Content-Type", "application/json")
        except HTTPError as exc:
            payload = exc.read()
            status = exc.code
            response_type = exc.headers.get("Content-Type", "application/json")
            self._diagnostic(
                request_id,
                "upstream_http_error",
                status,
                started_at,
                upstream_status=status,
            )
        except (URLError, OSError, TimeoutError):
            self._diagnostic(request_id, "upstream_transport_error", 502, started_at)
            self._write_json(
                502,
                {"error": "argentum_upstream_unavailable"},
                request_id=request_id,
            )
            return
        else:
            self._diagnostic(
                request_id,
                "upstream_success",
                status,
                started_at,
                upstream_status=status,
            )

        self.send_response(status)
        self.send_header("Content-Type", response_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Request-ID", request_id)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def _request_id(self) -> str:
        supplied = self.headers.get("X-Request-ID", "").strip()
        if supplied and _REQUEST_ID.fullmatch(supplied):
            return supplied
        return uuid.uuid4().hex

    def _diagnostic(
        self,
        request_id: str,
        outcome: str,
        status: int,
        started_at: float,
        *,
        upstream_status: int | None = None,
    ) -> None:
        record: dict[str, Any] = {
            "event": "argentum_gateway_request",
            "requestId": request_id,
            "method": self.command,
            "path": urlparse(self.path).path,
            "status": status,
            "outcome": outcome,
            "durationMs": round((time.monotonic() - started_at) * 1000, 3),
        }
        if upstream_status is not None:
            record["upstreamStatus"] = upstream_status
        print(json.dumps(record, separators=(",", ":"), sort_keys=True), file=sys.stderr, flush=True)

    def _read_body(self) -> bytes | None:
        if self.command not in {"POST", "DELETE"}:
            return None

        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            return b""
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValueError("invalid Content-Length") from exc
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise ValueError("request body exceeds gateway limit")
        return self.rfile.read(length)

    def _reject_not_found(self) -> None:
        started_at = time.monotonic()
        request_id = self._request_id()
        self._diagnostic(request_id, "gateway_route_rejected", 404, started_at)
        self._write_json(404, {"error": "not_found"}, request_id=request_id)

    def _write_json(self, status: int, payload: dict[str, Any], *, request_id: str) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Request-ID", request_id)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def config_from_environment() -> ArgentumGatewayConfig:
    token = os.environ.get("COMMANDER_GYM_GATEWAY_TOKEN", "")
    upstream = os.environ.get("COMMANDER_GYM_GATEWAY_UPSTREAM", "http://127.0.0.1:8081")
    bind_host = os.environ.get("COMMANDER_GYM_GATEWAY_BIND", "127.0.0.1")
    raw_port = os.environ.get("COMMANDER_GYM_GATEWAY_PORT", "8082")
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ArgentumGatewayConfigurationError("COMMANDER_GYM_GATEWAY_PORT must be an integer") from exc
    return ArgentumGatewayConfig(
        token=token,
        upstream_url=upstream,
        bind_host=bind_host,
        port=port,
    )


def main() -> int:
    config = config_from_environment()
    server = ArgentumGatewayServer((config.bind_host, config.port), config)
    print(
        json.dumps(
            {
                "service": "commander-gym-argentum-gateway",
                "bind": f"{config.bind_host}:{server.server_port}",
                "upstream": config.upstream_url,
                "authentication": "bearer",
            },
            sort_keys=True,
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
