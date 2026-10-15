from copy import deepcopy
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from commander_gym.lmstudio_pilot import LMStudioPilot, LMStudioPilotError, load_lmstudio_pilot, _schema_check
from commander_gym.lmstudio_provider import LMStudioConfig, LMStudioError, LMStudioTransport
from commander_gym.openai_responses_pilot import OpenAIResponsesPilot
from commander_gym.full_game import _provider_failure_diagnostic
from commander_gym.game_server_seat import GameServerSeatAdapter
from tests.test_openai_responses_pilot import action_observation as legacy_action_observation, structured_observation, FakeClient, FakeResponse


def action_observation():
    obs = legacy_action_observation()
    obs["legalActions"][0]["parameterSpec"] = {"allowedFields": {}}
    obs["legalActions"][1]["parameterSpec"] = {"allowedFields": {"attackers": "ENTITY_ID_MAP"}}
    return obs


def config(**kwargs):
    return LMStudioConfig(**{"endpoint": "http://127.0.0.1:12345", "model": "fixture-model",
                            "context_window": 8192, "max_prompt_bytes": 6144, **kwargs})


def discovery():
    return {"models": [{"key": "fixture-key", "type": "llm", "format": "gguf",
                        "loaded_instances": [{"id": "fixture-model", "config": {"context_length": 8192}}],
                        "capabilities": {"reasoning": {"allowed_options": ["off", "on"]}}}]}


def completion(content=None, **changes):
    return {"model": "fixture-model", "id": "synthetic-completion",
            "choices": [{"finish_reason": "stop", "message": {
                "role": "assistant", "reasoning_content": "private synthetic reasoning",
                "content": content if content is not None else json.dumps({
                    "channel": "action", "choice": {"semanticId": "argentum-action-v1:pass", "params": {}}}),
            }}], **changes}


class FakeTransport:
    def __init__(self, *responses, models=None):
        self.responses = list(responses) or [completion()]
        self.models = discovery() if models is None else models
        self.calls = []

    def request(self, path, body, **options):
        self.calls.append((path, deepcopy(body), options))
        if path == "/api/v1/models":
            return deepcopy(self.models)
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return deepcopy(result)


class LMStudioPilotTests(unittest.TestCase):
    def test_explicit_provider_and_model_selection_ignores_openai_environment(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "local.json"
            values = vars(config())
            path.write_text(json.dumps(values))
            with patch.dict("os.environ", {"OPENAI_API_KEY": "secret", "OPENAI_BASE_URL": "https://api.openai.com/v1"}):
                pilot = load_lmstudio_pilot(path)
            self.assertEqual(pilot.config.model, "fixture-model")
            for provider in (None, "openai", "unknown"):
                path.write_text(json.dumps({**values, "provider": provider}))
                with self.assertRaises(ValueError):
                    load_lmstudio_pilot(path)

    def test_native_choice_and_exact_wire_recording(self):
        transport = FakeTransport(completion(usage={"prompt_tokens": 10, "completion_tokens": 20}))
        obs = action_observation()
        before = deepcopy(obs)
        result = LMStudioPilot(config(), transport=transport).choose(obs)
        self.assertEqual(result.action_id, 2)
        self.assertEqual(obs, before)
        self.assertEqual(result.metadata["provider"], "lmstudio")
        attempt = result.metadata["modelIo"]["attempts"][0]
        self.assertEqual(attempt["request"], transport.calls[1][1])
        self.assertNotIn("text", attempt["request"])
        self.assertEqual(attempt["request"]["response_format"]["type"], "json_schema")
        self.assertEqual(result.metadata["usage"], {"prompt_tokens": 10, "completion_tokens": 20})
        self.assertEqual(attempt["response"]["reasoningContent"], "private synthetic reasoning")
        self.assertNotIn("usage", LMStudioPilot(config(), transport=FakeTransport()).choose(obs).metadata)

    def test_only_seat_observation_and_no_live_routing_or_reasoning_in_retry(self):
        obs = structured_observation()
        obs["state"] = {"cards": {"visible": {"name": "Own card"}}, "opponentHandSize": 4}
        final = json.dumps({"channel": "decision", "response": {"type": "TargetsResponse", "selectedTargets": {"0": ["target-1"]}}})
        transport = FakeTransport(completion("malformed"), completion(final))
        choice = LMStudioPilot(config(), transport=transport).choose(obs)
        self.assertEqual(choice.response["decisionId"], "routing-live-9")
        for path, body, _ in transport.calls:
            if body is not None:
                wire = json.dumps(body)
                self.assertNotIn("routing-live-9", wire)
                self.assertNotIn("private synthetic reasoning", wire)
                self.assertNotIn("opponent-secret", wire)
                self.assertIn("Own card", wire)
        self.assertEqual(choice.metadata["modelIo"]["selectedAttempt"], 1)

    def test_strict_native_action_and_target_validation(self):
        invalid = [
            {"channel": "action", "choice": {"semanticId": "invented", "params": {}}},
            {"channel": "action", "choice": {"semanticId": "argentum-action-v1:pass", "params": {"xValue": 2}}},
            {"channel": "action", "choice": {"semanticId": "argentum-action-v1:attack", "params": {"attackers": {"hidden": "player-2"}}}},
            {"channel": "action", "choice": {"semanticId": "argentum-action-v1:pass", "params": {}}, "extra": True},
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(LMStudioPilotError):
                LMStudioPilot(config(max_attempts=1), transport=FakeTransport(completion(json.dumps(value)))).choose(action_observation())
        bad = {"channel": "decision", "response": {"type": "TargetsResponse", "selectedTargets": {"0": ["hidden-target"]}}}
        with self.assertRaises(LMStudioPilotError):
            LMStudioPilot(config(max_attempts=1), transport=FakeTransport(completion(json.dumps(bad)))).choose(structured_observation())

    def test_malformed_truncated_empty_tools_reasoning_and_duplicate_keys_fail_closed(self):
        bad_responses = [completion(""), completion("{} trailing"), completion("<think>{}</think>"),
                         completion('{"channel":"action","channel":"decision"}'), completion("NaN"),
                         completion(model="wrong-model"), completion(choices=[])]
        for finish in ("length", "tool_calls", "content_filter", None):
            value = completion()
            value["choices"][0]["finish_reason"] = finish
            bad_responses.append(value)
        for field, value in (("content", None), ("tool_calls", [{"function": {"name": "act"}}]), ("refusal", "no")):
            bad = completion()
            bad["choices"][0]["message"][field] = value
            bad_responses.append(bad)
        for response in bad_responses:
            with self.subTest(response=response), self.assertRaises(LMStudioPilotError) as caught:
                LMStudioPilot(config(max_attempts=1), transport=FakeTransport(response)).choose(action_observation())
            self.assertIsNone(caught.exception.model_io["selectedAttempt"])

    def test_retry_bound_no_cloud_fallback_and_failure_diagnostic(self):
        transport = FakeTransport(LMStudioError("http_error", status=503), LMStudioError("http_error", status=503))
        with patch.object(OpenAIResponsesPilot, "choose", side_effect=AssertionError("cloud path called")):
            with self.assertRaises(LMStudioPilotError) as caught:
                LMStudioPilot(config(), transport=transport).choose(action_observation())
        self.assertEqual(len(caught.exception.model_io["attempts"]), 2)
        diagnostic = _provider_failure_diagnostic(caught.exception, stage="pilot", decision_index=0,
                                                  seat=0, observation=action_observation())
        self.assertIsNotNone(diagnostic)
        self.assertEqual(caught.exception.model_io["provider"], "lmstudio")
        for status in (400, 401, 404, 429):
            transport = FakeTransport(LMStudioError("http_error", status=status))
            with self.assertRaises(LMStudioPilotError):
                LMStudioPilot(config(), transport=transport).choose(action_observation())
            self.assertEqual(len(transport.calls), 2)

    def test_failure_receipt_uses_local_provider_and_preserves_seat_boundary(self):
        records = []
        transport = FakeTransport(completion("not JSON"))
        pilot = LMStudioPilot(config(max_attempts=1), transport=transport)
        seat = GameServerSeatAdapter(pilot, "ai", provenance_sink=records.append)
        state = {"viewingPlayerId": "ai", "hand": [{"name": "Visible own card"}]}
        actions = [{"actionType": "PassPriority", "semanticId": "argentum-action-v1:pass",
                    "parameterSpec": {"allowedFields": {}},
                    "action": {"type": "PassPriority", "playerId": "ai"}}]
        with self.assertRaises(LMStudioPilotError):
            seat.choose_action(state, actions, None, [])
        self.assertEqual(records[0].choice["metadata"]["provider"], "lmstudio")
        self.assertEqual(records[0].choice["errorType"], "LMStudioPilotError")
        self.assertNotIn("snapshot", records[0].observation)
        self.assertNotIn("snapshot", json.dumps(transport.calls))

    def test_malformed_discovery_on_retry_preserves_prior_attempt(self):
        for field in ("loaded_instances", "config", "capabilities", "reasoning"):
            models = discovery()
            model = models["models"][0]
            if field == "config": model["loaded_instances"][0][field] = None
            elif field == "reasoning": model["capabilities"][field] = None
            else: model[field] = None
            class ChangingDiscovery(FakeTransport):
                def request(self, path, body, **kwargs):
                    if path == "/api/v1/models" and len(self.calls) >= 2:
                        return models
                    return super().request(path, body, **kwargs)
            with self.subTest(field=field), self.assertRaisesRegex(LMStudioPilotError, "invalid_model_discovery") as caught:
                LMStudioPilot(config(), transport=ChangingDiscovery(completion("bad"))).choose(action_observation())
            self.assertEqual(len(caught.exception.model_io["attempts"]), 1)

    def test_context_discovery_and_schema_fail_before_inference(self):
        for models in ({}, {"models": []}, {"models": [{"type": "llm", "loaded_instances": []}]}):
            t = FakeTransport(models=models)
            with self.assertRaises(LMStudioPilotError):
                LMStudioPilot(config(), transport=t).choose(action_observation())
            self.assertEqual(len(t.calls), 1)
        models = discovery()
        models["models"][0]["loaded_instances"][0]["config"]["context_length"] = 4096
        with self.assertRaisesRegex(LMStudioPilotError, "loaded_context"):
            LMStudioPilot(config(), transport=FakeTransport(models=models)).choose(action_observation())
        t = FakeTransport()
        obs = action_observation()
        obs["state"] = {"large": "x" * 10000}
        with self.assertRaisesRegex(LMStudioPilotError, "byte_limit"):
            LMStudioPilot(config(), transport=t).choose(obs)
        self.assertEqual(t.calls, [])
        obs = structured_observation()
        obs["pendingDecision"]["responseSpec"]["requiredFields"] = {"alien": "UNSUPPORTED"}
        with self.assertRaisesRegex(LMStudioPilotError, "supported native JSON schema"):
            LMStudioPilot(config(), transport=t).choose(obs)
        with self.assertRaisesRegex(LMStudioError, "unsupported_json_schema"):
            _schema_check({"type": "string", "pattern": ".*"})

    def test_cancel_and_late_result_never_select(self):
        cancel = threading.Event()
        cancel.set()
        t = FakeTransport()
        with self.assertRaisesRegex(LMStudioPilotError, "cancelled"):
            LMStudioPilot(config(), transport=t, cancel=cancel).choose(action_observation())
        self.assertEqual(t.calls, [])
        class LateTransport(FakeTransport):
            def request(self, path, body, **kw):
                result = super().request(path, body, **kw)
                if body is not None:
                    time.sleep(.03)
                return result
        with self.assertRaisesRegex(LMStudioPilotError, "deadline") as caught:
            LMStudioPilot(config(timeout_seconds=.01), transport=LateTransport()).choose(action_observation())
        self.assertIsNone(caught.exception.model_io["selectedAttempt"])

    def test_existing_luna_path_unchanged(self):
        client = FakeClient(FakeResponse('{"channel":"action","semanticId":"argentum-action-v1:pass","params":{}}'))
        result = OpenAIResponsesPilot(client=client, model="gpt-6-luna").choose(action_observation())
        self.assertEqual(result.metadata["provider"], "openai")
        self.assertEqual(client.responses.calls[0]["model"], "gpt-6-luna")
        self.assertIn("text", client.responses.calls[0])


class LMStudioTransportTests(unittest.TestCase):
    def test_endpoint_isolation_and_budgets(self):
        for endpoint in ("https://api.openai.com:443", "http://8.8.8.8:1234", "http://localhost:1234",
                         "http://127.0.0.1", "http://0.0.0.0:1234", "http://169.254.169.254:80",
                         "http://user:secret@127.0.0.1:1234", "http://127.0.0.1:1234/v1",
                         "http://127.0.0.1:1234?redirect=yes", "http://[::ffff:8.8.8.8]:1234"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError): config(endpoint=endpoint)
        for kwargs in ({"timeout_seconds": float("nan")}, {"timeout_seconds": 101}, {"max_attempts": 3},
                       {"max_prompt_bytes": 8192}, {"max_output_tokens": True}):
            with self.assertRaises(ValueError): config(**kwargs)

    def _server(self, handler):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def test_real_worker_does_not_follow_redirects_or_inherit_credentials_proxies(self):
        received = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                received.append(dict(self.headers))
                self.send_response(302)
                self.send_header("Location", "https://api.openai.com/v1/models")
                self.end_headers()
            def log_message(self, *args): pass
        server = self._server(Handler)
        transport = LMStudioTransport(config(endpoint=f"http://127.0.0.1:{server.server_port}"))
        children = []
        real_popen = subprocess.Popen
        def capture(*args, **kwargs):
            self.assertEqual(kwargs["env"], {})
            child = real_popen(*args, **kwargs)
            children.append(child)
            return child
        with patch.dict("os.environ", {"HTTP_PROXY": "http://invalid:9", "OPENAI_API_KEY": "secret"}), \
             patch("commander_gym.lmstudio_provider.subprocess.Popen", side_effect=capture):
            with self.assertRaises(LMStudioError) as caught:
                transport.request("/api/v1/models", None, timeout=2)
        self.assertEqual(caught.exception.status, 302)
        self.assertEqual(len(received), 1)
        self.assertNotIn("Authorization", received[0])
        self.assertIsNotNone(children[0].poll())

    def test_real_worker_timeout_and_cancel_kill_and_reap_blocked_or_trickling_request(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                try:
                    for _ in range(20):
                        self.wfile.write(b" ")
                        self.wfile.flush()
                        time.sleep(.05)
                except (BrokenPipeError, ConnectionResetError): pass
            def log_message(self, *args): pass
        server = self._server(Handler)
        transport = LMStudioTransport(config(endpoint=f"http://127.0.0.1:{server.server_port}"))
        for cancelled in (False, True):
            children = []
            cancel = threading.Event()
            real_popen = subprocess.Popen
            def capture(*args, **kwargs):
                child = real_popen(*args, **kwargs)
                children.append(child)
                return child
            timer = threading.Timer(.15, cancel.set)
            if cancelled: timer.start()
            try:
                with patch("commander_gym.lmstudio_provider.subprocess.Popen", side_effect=capture):
                    start = time.monotonic()
                    with self.assertRaisesRegex(LMStudioError, "cancelled" if cancelled else "deadline"):
                        transport.request("/api/v1/models", None, timeout=2 if cancelled else .2, cancel=cancel)
                self.assertLess(time.monotonic() - start, 1)
                self.assertIsNotNone(children[0].poll())
            finally:
                timer.cancel()
