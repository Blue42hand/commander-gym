from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import threading
import time
import unittest
from unittest.mock import patch
from dataclasses import replace

from commander_gym.lmstudio_http_worker import read_chat_stream
from commander_gym.lmstudio_provider import LMStudioTransport, LMStudioError
from commander_gym.lmstudio_pilot import LMStudioPilot
from commander_gym.evidence import _model_io
import test_evidence
from commander_gym.openai_responses_pilot import _without_live_routing
from test_lmstudio import config, FakeTransport, action_observation


def event(delta=None, finish=None, **extra):
    value = {"id": "stream-1", "model": "fixture-model", "choices": [
        {"index": 0, "delta": delta or {}, "finish_reason": finish}], **extra}
    return b"data: " + json.dumps(value).encode() + b"\n\n"


def stream():
    return (event({"role": "assistant"}) + event({"reasoning_content": "diagnostic-only"})
            + event({"content": '{"channel":"action",'})
            + event({"content": '"choice":{"semanticId":"argentum-action-v1:pass","params":{}}}'})
            + event(finish="stop")
            + event(choices=[], usage={"prompt_tokens": 10, "completion_tokens": 20})
            + b"data: [DONE]\n\n")


class StreamTests(unittest.TestCase):
    def parse(self, data):
        timing = {}
        result = read_chat_stream(BytesIO(data), started=time.monotonic(), timing=timing)
        return result, timing

    def test_complete_stream_preserves_final_and_separate_reasoning_and_usage(self):
        result, timing = self.parse(stream())
        self.assertEqual(result["choices"][0]["finish_reason"], "stop")
        self.assertEqual(json.loads(result["choices"][0]["message"]["content"])["choice"]["params"], {})
        self.assertEqual(result["choices"][0]["message"]["reasoning_content"], "diagnostic-only")
        self.assertEqual(result["usage"]["completion_tokens"], 20)
        self.assertLessEqual(timing["firstReasoningMs"], timing["firstContentMs"])
        self.assertLessEqual(timing["firstContentMs"], timing["bodyCompleteMs"])

    def test_truncation_malformed_identity_tools_and_multiple_choices_fail(self):
        cases = [stream().replace(b"data: [DONE]\n\n", b""),
                 event({"role": "assistant"}) + b"data: [DONE]\n\n",
                 b"data: invalid\n\n", event({"role": "system"}),
                 event({"role": "assistant"}) + event(model="changed"),
                 event({"role": "assistant"}) + event({"tool_calls": [{"id": "x"}]}),
                 event(choices=[{}, {}]), event({"content": []}),
                 event(finish="stop") + event({"content": "late"}),
                 event(choices=[])]
        for data in cases:
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.parse(data)
        with patch("commander_gym.lmstudio_http_worker.MAX_RESPONSE_BYTES", 30):
            with self.assertRaisesRegex(ValueError, "response_too_large"):
                self.parse(stream())

    def test_real_stream_worker_is_bounded_and_does_not_expose_partial_output(self):
        release = threading.Event()
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
                self.wfile.write(event({"role": "assistant", "content": "partial"})); self.wfile.flush()
                release.wait(2)
            def log_message(self, *args): pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        serving = threading.Thread(target=server.serve_forever); serving.start()
        try:
            transport = LMStudioTransport(config(endpoint=f"http://127.0.0.1:{server.server_port}"))
            for cancelled in (False, True):
                cancel = threading.Event(); timer = threading.Timer(.15, cancel.set)
                if cancelled: timer.start()
                try:
                    with self.assertRaisesRegex(LMStudioError, "cancelled" if cancelled else "deadline"):
                        transport.request("/v1/chat/completions", {"stream": True},
                                          timeout=2 if cancelled else .2, cancel=cancel)
                finally: timer.cancel()
        finally:
            release.set(); server.shutdown(); server.server_close(); serving.join()

    def test_stream_is_explicit_and_native_validation_is_unchanged(self):
        obs = action_observation(); before = deepcopy(obs)
        transport = FakeTransport(self.parse(stream())[0])
        choice = LMStudioPilot(config(stream=True), transport=transport).choose(obs)
        self.assertEqual(choice.action_id, 2)
        request = transport.calls[1][1]
        self.assertTrue(request["stream"])
        self.assertEqual(obs, before)
        self.assertEqual(json.loads(request["messages"][1]["content"]),
                         _without_live_routing(obs))
        self.assertEqual(request["reasoning_effort"], "none")
        record = test_evidence.RawEvidenceTests().make_record()
        record = replace(record, metadata={**record.metadata, "pilot_metadata": choice.metadata})
        exported = _model_io(record)["attempts"][0]["response"]
        self.assertIn("discoveryWallTimeMs", exported)
        self.assertIn("providerWallTimeMs", exported)


if __name__ == "__main__":
    unittest.main()
