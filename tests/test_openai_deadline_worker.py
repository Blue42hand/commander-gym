import io
import json
import types
import unittest
from unittest.mock import patch

from commander_gym.openai_deadline_worker import main


class OpenAIDeadlineWorkerTests(unittest.TestCase):
    def _run(self, create):
        created = []

        class FakeOpenAI:
            def __init__(self, **kwargs):
                created.append(kwargs)
                self.responses = types.SimpleNamespace(create=create)

        output = io.StringIO()
        with patch.dict("sys.modules", {"openai": types.SimpleNamespace(OpenAI=FakeOpenAI)}), \
                patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test-offline"}), \
                patch("sys.stdin", io.StringIO(json.dumps({
                    "request": {"model": "gpt-6-luna", "store": False}, "timeout": 0.5,
                }))), patch("sys.stdout", output):
            status = main()
        return status, json.loads(output.getvalue()), created

    def test_serializes_completed_response_and_keeps_sdk_retries_disabled(self):
        class Response:
            output_text = '{"channel":"action"}'

            def model_dump(self, **kwargs):
                self.mode = kwargs["mode"]
                return {"id": "resp-test", "usage": {"input_tokens": 1, "output_tokens": 1}}

        status, result, created = self._run(lambda **kwargs: Response())
        self.assertEqual(status, 0)
        self.assertEqual(result["kind"], "response")
        self.assertEqual(result["response"]["output_text"], '{"channel":"action"}')
        self.assertEqual(created[0]["max_retries"], 0)
        self.assertEqual(created[0]["timeout"], 0.5)

    def test_transmits_only_structured_failure_status_and_code(self):
        class ProviderError(Exception):
            status_code = 520
            body = {"error": {"code": "upstream_timeout", "message": "private secret"}}

        def failure(**kwargs):
            raise ProviderError("private secret")

        status, result, _ = self._run(failure)
        self.assertEqual(status, 0)
        self.assertEqual(result, {"kind": "error", "status_code": 520,
                                  "code": "upstream_timeout"})
        self.assertNotIn("private secret", json.dumps(result))
