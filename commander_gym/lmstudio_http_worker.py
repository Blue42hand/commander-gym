"""Single-use, credential-free LM Studio HTTP worker (no SDK or ambient proxy)."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main() -> int:
    try:
        envelope = json.load(sys.stdin)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        payload = envelope.get("body")
        request = urllib.request.Request(
            envelope["url"],
            data=None if payload is None else json.dumps(payload, allow_nan=False).encode(),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with opener.open(request, timeout=envelope["timeout"]) as response:
                data = response.read(MAX_RESPONSE_BYTES + 1)
                if len(data) > MAX_RESPONSE_BYTES:
                    result = {"error": "response_too_large"}
                else:
                    result = {"response": json.loads(data)}
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
