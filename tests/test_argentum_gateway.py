import io
import json
import threading
import unittest
from contextlib import redirect_stderr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from commander_gym.argentum_gateway import (
    ArgentumGatewayConfig,
    ArgentumGatewayConfigurationError,
    ArgentumGatewayServer,
)


class RecordingUpstreamHandler(BaseHTTPRequestHandler):
    requests = []

    def do_GET(self):  # noqa: N802
        self._record()

    def do_POST(self):  # noqa: N802
        self._record()

    def do_DELETE(self):  # noqa: N802
        self._record()

    def _record(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        type(self).requests.append(
            {
                "method": self.command,
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "request_id": self.headers.get("X-Request-ID"),
                "body": body,
            }
        )
        payload = json.dumps(
            {
                "method": self.command,
                "path": self.path,
                "body": body.decode("utf-8"),
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        pass


class ArgentumGatewayTests(unittest.TestCase):
    def setUp(self):
        RecordingUpstreamHandler.requests = []
        self.upstream = ThreadingHTTPServer(("127.0.0.1", 0), RecordingUpstreamHandler)
        self.upstream_thread = threading.Thread(target=self.upstream.serve_forever, daemon=True)
        self.upstream_thread.start()

        config = ArgentumGatewayConfig(
            token="secret-token",
            upstream_url=f"http://127.0.0.1:{self.upstream.server_port}",
            port=8082,
        )
        self.gateway = ArgentumGatewayServer(("127.0.0.1", 0), config)
        self.gateway_thread = threading.Thread(target=self.gateway.serve_forever, daemon=True)
        self.gateway_thread.start()
        self.base_url = f"http://127.0.0.1:{self.gateway.server_port}"

    def tearDown(self):
        self.gateway.shutdown()
        self.gateway.server_close()
        self.upstream.shutdown()
        self.upstream.server_close()

    def request(
        self,
        method,
        path,
        *,
        token="secret-token",
        body=None,
        content_type="application/json",
        request_id=None,
        include_headers=False,
    ):
        headers = {}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        if request_id is not None:
            headers["X-Request-ID"] = request_id
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = content_type
        request = Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=2) as response:
                result = (response.status, json.loads(response.read().decode("utf-8")))
                if include_headers:
                    return result + (response.headers,)
                return result
        except HTTPError as exc:
            result = (exc.code, json.loads(exc.read().decode("utf-8")))
            if include_headers:
                return result + (exc.headers,)
            return result

    def test_requires_bearer_token_before_forwarding(self):
        status, payload = self.request("GET", "/status", token=None)
        self.assertEqual(status, 401)
        self.assertEqual(payload, {"error": "unauthorized"})
        self.assertEqual(RecordingUpstreamHandler.requests, [])

    def test_forwards_allowed_status_without_forwarding_authorization(self):
        status, payload = self.request("GET", "/status")
        self.assertEqual(status, 200)
        self.assertEqual(payload["path"], "/status")
        self.assertEqual(len(RecordingUpstreamHandler.requests), 1)
        self.assertIsNone(RecordingUpstreamHandler.requests[0]["authorization"])

    def test_forwards_environment_lifecycle_surface(self):
        calls = [
            ("POST", "/envs", {"players": [{"name": "A"}]}),
            ("GET", "/envs/env-1?revealAll=false", None),
            ("POST", "/envs/env-1/step", {"actionId": 7, "params": {}}),
            (
                "POST",
                "/envs/env-1/decision",
                {"decisionId": "decision-1", "response": {"type": "yes-no", "value": True}},
            ),
            ("DELETE", "/envs", {"envIds": ["env-1"]}),
        ]
        for method, path, body in calls:
            status, _ = self.request(method, path, body=body)
            self.assertEqual(status, 200)
        self.assertEqual(
            [(entry["method"], entry["path"]) for entry in RecordingUpstreamHandler.requests],
            [(method, path) for method, path, _ in calls],
        )

    def test_blocks_non_milestone_routes(self):
        for method, path in [
            ("GET", "/swagger-ui.html"),
            ("GET", "/v3/api-docs"),
            ("POST", "/envs/env-1/reset"),
            ("GET", "/envs/env-1?revealAll=maybe"),
        ]:
            status, payload = self.request(method, path, body={} if method == "POST" else None)
            self.assertEqual(status, 404)
            self.assertEqual(payload, {"error": "not_found"})
        self.assertEqual(RecordingUpstreamHandler.requests, [])

    def test_rejects_non_json_mutations(self):
        status, payload = self.request(
            "POST",
            "/envs",
            body={"players": []},
            content_type="text/plain",
        )
        self.assertEqual(status, 415)
        self.assertIn("application/json", payload["error"])
        self.assertEqual(RecordingUpstreamHandler.requests, [])

    def test_structured_diagnostics_correlate_without_logging_secrets_or_bodies(self):
        diagnostics = io.StringIO()
        with redirect_stderr(diagnostics):
            status, _, headers = self.request(
                "POST",
                "/envs",
                body={"privateMarker": "do-not-log"},
                request_id="cg-test-123",
                include_headers=True,
            )

        self.assertEqual(status, 200)
        self.assertEqual(headers.get("X-Request-ID"), "cg-test-123")
        self.assertEqual(RecordingUpstreamHandler.requests[0]["request_id"], "cg-test-123")

        rendered = diagnostics.getvalue()
        record = json.loads(rendered.strip().splitlines()[-1])
        self.assertEqual(record["event"], "argentum_gateway_request")
        self.assertEqual(record["requestId"], "cg-test-123")
        self.assertEqual(record["method"], "POST")
        self.assertEqual(record["path"], "/envs")
        self.assertEqual(record["status"], 200)
        self.assertEqual(record["upstreamStatus"], 200)
        self.assertEqual(record["outcome"], "upstream_success")
        self.assertGreaterEqual(record["durationMs"], 0)
        self.assertNotIn("secret-token", rendered)
        self.assertNotIn("do-not-log", rendered)

    def test_unauthorized_diagnostic_is_gateway_scoped_and_correlated(self):
        diagnostics = io.StringIO()
        with redirect_stderr(diagnostics):
            status, payload, headers = self.request(
                "GET",
                "/status",
                token=None,
                request_id="cg-denied-1",
                include_headers=True,
            )

        self.assertEqual(status, 401)
        self.assertEqual(payload, {"error": "unauthorized"})
        self.assertEqual(headers.get("X-Request-ID"), "cg-denied-1")
        record = json.loads(diagnostics.getvalue().strip().splitlines()[-1])
        self.assertEqual(record["requestId"], "cg-denied-1")
        self.assertEqual(record["outcome"], "gateway_unauthorized")
        self.assertEqual(record["status"], 401)
        self.assertNotIn("secret-token", diagnostics.getvalue())
        self.assertEqual(RecordingUpstreamHandler.requests, [])

    def test_configuration_refuses_public_bind_or_non_loopback_upstream(self):
        with self.assertRaises(ArgentumGatewayConfigurationError):
            ArgentumGatewayConfig(token="secret", bind_host="0.0.0.0")
        with self.assertRaises(ArgentumGatewayConfigurationError):
            ArgentumGatewayConfig(token="secret", upstream_url="https://example.com")
        with self.assertRaises(ArgentumGatewayConfigurationError):
            ArgentumGatewayConfig(token="  ")


if __name__ == "__main__":
    unittest.main()
