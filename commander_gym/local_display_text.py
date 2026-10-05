"""Text-only, no-network status display for a Commander Gym host.

This intentionally does not read seat provenance. The terminal may be shared, so
private observations, choices, and provider details do not belong here.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import unicodedata
from collections.abc import Mapping
from typing import Any

from .local_display import build_status


def _safe(value: Any) -> str:
    """Prevent status text from issuing terminal controls or changing line layout."""
    printable = "".join(ch for ch in str(value) if not unicodedata.category(ch).startswith("C"))
    return printable[:120]


def render_status(status: Mapping[str, Any], *, revisions: Mapping[str, str] | None = None) -> str:
    host = status["host"]
    services = status["services"]
    lines = [
        "COMMANDER GYM  |  LOCAL TEXT DISPLAY",
        "=" * 48,
        "HOST",
        f"  Uptime       {_safe(host['uptime'])}",
        f"  Load         {_safe(host['load'])}",
        f"  Memory       {_safe(host['memory'])}",
        f"  Temperature  {_safe(host['temperature'])}",
        "",
        "SERVICE ACTIVITY (process state, not authenticated health)",
    ]
    lines.extend(f"  {_safe(name):<32} {_safe(state)}" for name, state in sorted(services.items()))
    if revisions:
        lines.extend(["", "PINNED REVISIONS"])
        lines.extend(f"  {_safe(name):<16} {_safe(revision)}" for name, revision in revisions.items())
    lines.extend(["", "Pilot data: off (private seat observations are not read)",
                  "Refresh: 5 seconds  |  Ctrl-C to exit"])
    return "\n".join(lines) + "\n"


def _revisions_from_environment() -> dict[str, str]:
    names = (("Display", "COMMANDER_GYM_DISPLAY_REVISION"),
             ("Gym runtime", "COMMANDER_GYM_RUNTIME_REVISION"),
             ("Argentum", "COMMANDER_GYM_ARGENTUM_REVISION"))
    return {name: os.environ[key] for name, key in names if os.environ.get(key)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Text-only Commander Gym host status")
    parser.add_argument("--once", action="store_true", help="print one snapshot without terminal control")
    args = parser.parse_args(argv)
    if not args.once and not sys.stdout.isatty():
        parser.error("continuous display needs an interactive terminal; use --once")
    revisions = _revisions_from_environment()
    if args.once:
        sys.stdout.write(render_status(build_status(None), revisions=revisions))
        return 0
    sys.stdout.write("\x1b[?25l")  # hide cursor on the local terminal
    try:
        while True:
            frame = render_status(build_status(None), revisions=revisions)
            sys.stdout.write("\x1b[H\x1b[2J" + frame)
            sys.stdout.flush()
            time.sleep(5)
    except KeyboardInterrupt:
        return 0
    finally:
        sys.stdout.write("\x1b[?25h\n")
        sys.stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main())
