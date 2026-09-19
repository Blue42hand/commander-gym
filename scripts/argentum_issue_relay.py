#!/usr/bin/env python3
"""Execute one owner-authored /argentum issue command against the remote gateway."""

from __future__ import annotations

import os
from pathlib import Path

from commander_gym.argentum_client import ArgentumGymClient
from commander_gym.argentum_issue_relay import ArgentumRelay, parse_comment, render_result


def main() -> int:
    url = os.environ.get("ARGENTUM_GATEWAY_URL", "").strip()
    token = os.environ.get("ARGENTUM_GATEWAY_TOKEN", "").strip()
    comment = os.environ.get("ARGENTUM_RELAY_COMMENT", "")
    command_file = os.environ.get("ARGENTUM_RELAY_COMMAND_FILE", "").strip()
    output = Path(os.environ.get("ARGENTUM_RELAY_OUTPUT", "relay-result.md"))

    if command_file:
        comment = "/argentum " + Path(command_file).read_text(encoding="utf-8").strip()

    if not url or not token:
        raise SystemExit("ARGENTUM_GATEWAY_URL and ARGENTUM_GATEWAY_TOKEN are required")
    if not comment:
        raise SystemExit("ARGENTUM_RELAY_COMMENT or ARGENTUM_RELAY_COMMAND_FILE is required")

    client = ArgentumGymClient(url, bearer_token=token, timeout=20)
    relay = ArgentumRelay(client)
    try:
        result = relay.execute(parse_comment(comment))
        output.write_text(render_result(result), encoding="utf-8")
    except Exception as exc:
        output.write_text(
            render_result({"status": "error", "type": type(exc).__name__, "message": str(exc)}),
            encoding="utf-8",
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
