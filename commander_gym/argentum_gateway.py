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
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen


MAX_REQUEST_BYTES = 1024 * 1024
_ENV_PATH = re.compile(r"^/envs/[^/]+$")
_STEP_PATH = re.compile(r"^/envs/[^/]+/step$")
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
        return (
            set(query) == {"revealAll"}
            and len(query["revealAll"]) == 1
            and query["revealAll"][0] in {"true", "false"}
        )

    if method == "POST" and _STEP_PATH.fullmatch(path):
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

    def _handle(self) -> None:
        config = self.server.gateway_config  # type: ignore[attr-defined]

        supplied = self.headers.get("Authorization", "")
        expected = f"Bearer {config.token}"
        if not hmac.compare_digest(supplied, expected):
            self._write_json(401, {"error": "unauthorized"})
            return

        if not _allowed_request(self.command, self.path):
            self._reject_not_found()
            return

        try:
            body = self._read_body()
        except ValueError as exc:
            self._write_json(413, {"error": str(exc)})
            return

        target = config.upstream_url.rstrip("/") + self.path
        headers = {"Accept": "application/json"}
        content_type = self.headers.get("Content-Type")
        if body is not None:
            if content_type and content_type.split(";", 1)[0].strip().lower() != "application/json":
                self._write_json(415, {"error": "mutating requests must use application/json"})
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
        except (URLError, OSError, TimeoutError):
            self._write_json(502, {"error": "argentum_upstream_unavailable"})
            return

        self.send_response(status)
        self.send_header("Content-Type", response_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)

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
        self._write_json(404, {"error": "not_found"})

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
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
