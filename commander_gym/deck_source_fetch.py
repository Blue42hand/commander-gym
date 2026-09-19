"""Read-only acquisition adapters for public external deck sources.

This module deliberately owns only narrow source retrieval. It performs no
remote writes, source registration, local archival, authentication, deck
legality checks, or rules evaluation. Fetched provider data is handed to the
pure normalization layer in :mod:`commander_gym.deck_sources`.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .deck_sources import DeckSourceSnapshot, normalize_archidekt_snapshot

DEFAULT_MAX_SOURCE_BYTES = 16 * 1024 * 1024
DEFAULT_SOURCE_TIMEOUT_SECONDS = 35.0
_ARCHIDEKT_ID = re.compile(r"[1-9][0-9]*")
_ARCHIDEKT_HOST = "archidekt.com"


class DeckSourceFetchError(RuntimeError):
    """Raised when a public source cannot be fetched safely or completely."""


def _validate_positive_number(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise DeckSourceFetchError(f"{field_name} must be positive")
    return float(value)


def _validate_source_identity(source_id: str, source_url: str) -> tuple[str, str]:
    source_id = str(source_id or "").strip()
    if not _ARCHIDEKT_ID.fullmatch(source_id):
        raise DeckSourceFetchError("invalid Archidekt source_id")

    source_url = str(source_url or "").strip()
    parsed = urlsplit(source_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != _ARCHIDEKT_HOST
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith(f"/decks/{source_id}/")
    ):
        raise DeckSourceFetchError("Archidekt source_url does not match the public source_id")
    return source_id, source_url


def _validate_archidekt_redirect(url: str) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != _ARCHIDEKT_HOST
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise DeckSourceFetchError("Archidekt fetch refused a cross-origin redirect")


class _ArchidektSameOriginRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        _validate_archidekt_redirect(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _unwrap_archidekt_payload(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DeckSourceFetchError("Archidekt response must be a JSON object")
    for key in ("deck", "data"):
        nested = value.get(key)
        if isinstance(nested, Mapping):
            return nested
    return value


def fetch_archidekt_snapshot(
    *,
    source_id: str,
    source_url: str,
    timeout: float = DEFAULT_SOURCE_TIMEOUT_SECONDS,
    max_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
    opener: Optional[Any] = None,
) -> DeckSourceSnapshot:
    """Fetch and normalize one public Archidekt deck revision.

    The caller supplies the source identity; there is intentionally no public
    registry of deck IDs. The request uses a fixed HTTPS API endpoint, sends no
    credentials, permits redirects only within ``archidekt.com``, bounds the
    response size, refuses explicitly private content, and performs no writes.

    ``opener`` is injectable for deterministic tests and alternate read-only
    transports that implement ``open(request, timeout=...)``.
    """

    source_id, source_url = _validate_source_identity(source_id, source_url)
    timeout = _validate_positive_number(timeout, "timeout")
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise DeckSourceFetchError("max_bytes must be a positive integer")

    api_url = f"https://{_ARCHIDEKT_HOST}/api/decks/{source_id}/"
    request = Request(
        api_url,
        headers={
            "User-Agent": "CommanderGym/1.0 (public deck-source read)",
            "Accept": "application/json",
        },
        method="GET",
    )
    reader = opener if opener is not None else build_opener(_ArchidektSameOriginRedirect())

    try:
        with reader.open(request, timeout=timeout) as response:
            raw = response.read(max_bytes + 1)
    except HTTPError as exc:
        raise DeckSourceFetchError(f"Archidekt fetch failed with HTTP {exc.code}") from exc
    except (URLError, OSError, TimeoutError) as exc:
        raise DeckSourceFetchError("Archidekt fetch failed before a complete response was read") from exc

    if len(raw) > max_bytes:
        raise DeckSourceFetchError("Archidekt response exceeds the configured size limit")

    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeckSourceFetchError("Archidekt response is not valid JSON") from exc

    payload = _unwrap_archidekt_payload(value)
    if payload.get("private") is True:
        raise DeckSourceFetchError("public Archidekt fetch refuses private deck content")

    return normalize_archidekt_snapshot(
        payload,
        source_id=source_id,
        source_url=source_url,
    )
