"""Single-use OpenAI request worker for bounded whole-request cancellation."""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Mapping


def _code(exc: Exception) -> str | None:
    body = getattr(exc, "body", None)
    if isinstance(body, Mapping):
        error = body.get("error", body)
        if isinstance(error, Mapping) and isinstance(error.get("code"), str):
            return error["code"]
    return None


def main() -> int:
    try:
        envelope = json.load(sys.stdin)
        request = envelope["request"]
        timeout = envelope["timeout"]
        if not isinstance(request, dict) or type(timeout) not in (float, int) or timeout <= 0:
            return 2
        from openai import OpenAI

        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], timeout=timeout,
                        max_retries=0)
        try:
            response = client.responses.create(**request)
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            result: dict[str, Any] = {
                "kind": "error", "status_code": status if type(status) is int else None,
                "code": _code(exc),
            }
        else:
            value = response.model_dump(mode="json")
            value["output_text"] = response.output_text
            result = {"kind": "response", "response": value}
        sys.stdout.write(json.dumps(result, separators=(",", ":")))
        sys.stdout.flush()
        return 0
    except Exception:
        # Never write request, credential, SDK body or traceback to stdout.
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
