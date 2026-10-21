"""Single-use, credential-free LM Studio HTTP worker (no SDK or ambient proxy)."""

from __future__ import annotations

import json
import sys
from time import monotonic
import urllib.error
import urllib.request

MAX_RESPONSE_BYTES = 4 * 1024 * 1024


def read_chat_stream(response, *, started, timing):
    """Bounded SSE assembly. Nothing is returned before a complete [DONE].

    Client first-output timing includes queueing, transport, prompt processing
    and first-token generation. It is not a server-side prefill measurement.
    """
    used = 0
    data = []
    identity = {}
    content, reasoning = [], []
    finish = None
    role = None
    usage = None
    stats = None
    while True:
        line = response.readline(MAX_RESPONSE_BYTES + 1)
        used += len(line)
        if used > MAX_RESPONSE_BYTES:
            raise ValueError("response_too_large")
        if not line:
            raise ValueError("incomplete_stream")
        line = line.decode("utf-8").rstrip("\r\n")
        if line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))
            continue
        if line or not data:  # Comments and SSE metadata do not carry output.
            continue
        payload = "\n".join(data)
        data = []
        if payload == "[DONE]":
            if finish is None or role != "assistant" or set(identity) != {"id", "model"}:
                raise ValueError("incomplete_stream")
            timing["bodyCompleteMs"] = round((monotonic() - started) * 1000, 3)
            result = {**identity, "choices": [{"index": 0, "finish_reason": finish,
                      "message": {"role": role, "content": "".join(content),
                                  "reasoning_content": "".join(reasoning)}}]}
            if usage is not None:
                result["usage"] = usage
            if stats is not None:
                result["stats"] = stats
            return result
        chunk = json.loads(payload)
        if not isinstance(chunk, dict) or chunk.get("error"):
            raise ValueError("invalid_stream")
        for key in ("id", "model"):
            value = chunk.get(key)
            if not isinstance(value, str) or not value or (key in identity and identity[key] != value):
                raise ValueError("inconsistent_stream_identity")
            identity[key] = value
        choices = chunk.get("choices")
        if not isinstance(choices, list) or len(choices) > 1:
            raise ValueError("invalid_stream_choices")
        if chunk.get("usage") is not None:
            if usage is not None or not isinstance(chunk["usage"], dict):
                raise ValueError("invalid_stream_usage")
            usage = chunk["usage"]
        if chunk.get("stats") is not None:
            if not isinstance(chunk["stats"], dict):
                raise ValueError("invalid_stream_stats")
            stats = chunk["stats"]
        if not choices:
            if finish is None or usage is None:
                raise ValueError("unexpected_empty_stream_choice")
            continue
        choice = choices[0]
        if not isinstance(choice, dict) or choice.get("index") != 0 or finish is not None:
            raise ValueError("invalid_stream_choice")
        delta = choice.get("delta")
        if (not isinstance(delta, dict) or delta.get("tool_calls")
            or delta.get("function_call") or delta.get("refusal")):
            raise ValueError("unsupported_stream_output")
        if "role" in delta:
            if delta["role"] != "assistant":
                raise ValueError("invalid_stream_role")
            role = "assistant"
        for key, parts, timer in (("content", content, "firstContentMs"),
                                  ("reasoning_content", reasoning, "firstReasoningMs")):
            value = delta.get(key)
            if value is not None:
                if not isinstance(value, str):
                    raise ValueError("invalid_stream_content")
                if value:
                    now = round((monotonic() - started) * 1000, 3)
                    timing.setdefault(timer, now)
                    timing.setdefault("firstOutputMs", now)
                    parts.append(value)
        finish = choice.get("finish_reason")
        if finish is not None and not isinstance(finish, str):
            raise ValueError("invalid_stream_finish")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main() -> int:
    try:
        envelope = json.load(sys.stdin)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        payload = envelope.get("body")
        streaming = isinstance(payload, dict) and payload.get("stream") is True
        request = urllib.request.Request(
            envelope["url"],
            data=None if payload is None else json.dumps(payload, allow_nan=False).encode(),
            headers={"Content-Type": "application/json",
                     "Accept": "text/event-stream" if streaming else "application/json"},
        )
        started = monotonic()
        timing = {"stream": streaming}
        try:
            with opener.open(request, timeout=envelope["timeout"]) as response:
                timing["headersMs"] = round((monotonic() - started) * 1000, 3)
                if streaming:
                    if response.headers.get_content_type() != "text/event-stream":
                        raise ValueError("stream_content_type_required")
                    result = {"response": read_chat_stream(response, started=started, timing=timing)}
                else:
                    data = response.read(MAX_RESPONSE_BYTES + 1)
                    if len(data) > MAX_RESPONSE_BYTES:
                        result = {"error": "response_too_large"}
                    else:
                        result = {"response": json.loads(data)}
                    timing["bodyCompleteMs"] = round((monotonic() - started) * 1000, 3)
                result["transport"] = timing
        except urllib.error.HTTPError as exc:
            # Never echo a remote error body: it can contain the input or secrets.
            result = {"error": "http_error", "status": exc.code}
        except Exception:
            result = {"error": "invalid_response_or_transport_failure"}
        sys.stdout.write(json.dumps(result, allow_nan=False))
        return 0
    except Exception:
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
