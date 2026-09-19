import json
import unittest
from urllib.error import URLError

from commander_gym.deck_source_fetch import (
    DeckSourceFetchError,
    _ArchidektSameOriginRedirect,
    fetch_archidekt_snapshot,
)


class _Response:
    def __init__(self, payload):
        self.payload = payload
        self.read_limit = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, limit):
        self.read_limit = limit
        return self.payload


class _Opener:
    def __init__(self, payload=None, error=None):
        self.response = _Response(payload or b"")
        self.error = error
        self.request = None
        self.timeout = None
        self.calls = 0

    def open(self, request, timeout):
        self.calls += 1
        self.request = request
        self.timeout = timeout
        if self.error is not None:
            raise self.error
        return self.response


class DeckSourceFetchTests(unittest.TestCase):
    def fixture(self, *, private=False):
        return {
            "id": 1,
            "name": "Public Fixture",
            "deckFormat": 3,
            "private": private,
            "description": "Synthetic public source.",
            "categories": [
                {"id": 1, "name": "Commander", "includedInDeck": True, "isPremier": True},
                {"id": 2, "name": "Main", "includedInDeck": True},
            ],
            "cards": [
                {
                    "id": 10,
                    "quantity": 1,
                    "categories": [1],
                    "card": {
                        "id": 110,
                        "uid": "printing-commander",
                        "oracleCard": {
                            "uid": "oracle-commander",
                            "name": "Synthetic Commander",
                        },
                    },
                },
                {
                    "id": 20,
                    "quantity": 99,
                    "categories": [2],
                    "card": {
                        "id": 120,
                        "uid": "printing-island",
                        "oracleCard": {"uid": "oracle-island", "name": "Island"},
                    },
                },
            ],
        }

    def fetch(self, payload=None, **kwargs):
        raw = json.dumps(self.fixture() if payload is None else payload).encode("utf-8")
        opener = _Opener(raw)
        snapshot = fetch_archidekt_snapshot(
            source_id="1",
            source_url="https://archidekt.com/decks/1/public-fixture",
            opener=opener,
            **kwargs,
        )
        return snapshot, opener

    def test_fetches_fixed_read_only_endpoint_without_credentials(self):
        snapshot, opener = self.fetch()
        self.assertEqual(snapshot.provider, "archidekt")
        self.assertEqual(snapshot.source_id, "1")
        self.assertEqual(sum(entry.count for entry in snapshot.entries), 100)
        self.assertEqual(opener.request.full_url, "https://archidekt.com/api/decks/1/")
        self.assertEqual(opener.request.get_method(), "GET")
        headers = {key.casefold(): value for key, value in opener.request.header_items()}
        self.assertEqual(headers["accept"], "application/json")
        self.assertNotIn("authorization", headers)
        self.assertEqual(opener.timeout, 35.0)

    def test_accepts_wrapped_provider_payload_and_preserves_normalized_identity(self):
        direct, _ = self.fetch()
        wrapped, _ = self.fetch({"data": self.fixture()})
        self.assertEqual(direct.fingerprint(), wrapped.fingerprint())

    def test_refuses_private_content_before_normalization(self):
        opener = _Opener(json.dumps(self.fixture(private=True)).encode("utf-8"))
        with self.assertRaisesRegex(DeckSourceFetchError, "private"):
            fetch_archidekt_snapshot(
                source_id="1",
                source_url="https://archidekt.com/decks/1/public-fixture",
                opener=opener,
            )

    def test_bounds_response_and_reads_only_limit_plus_one(self):
        opener = _Opener(b"x" * 9)
        with self.assertRaisesRegex(DeckSourceFetchError, "size limit"):
            fetch_archidekt_snapshot(
                source_id="1",
                source_url="https://archidekt.com/decks/1/public-fixture",
                opener=opener,
                max_bytes=8,
            )
        self.assertEqual(opener.response.read_limit, 9)

    def test_rejects_nonmatching_or_non_https_source_before_network_access(self):
        for url in (
            "http://archidekt.com/decks/1/public-fixture",
            "https://example.com/decks/1/public-fixture",
            "https://archidekt.com/decks/2/public-fixture",
            "https://user@archidekt.com/decks/1/public-fixture",
            "https://archidekt.com/decks/1/public-fixture?token=secret",
        ):
            opener = _Opener(json.dumps(self.fixture()).encode("utf-8"))
            with self.assertRaises(DeckSourceFetchError):
                fetch_archidekt_snapshot(source_id="1", source_url=url, opener=opener)
            self.assertEqual(opener.calls, 0)

    def test_redirect_handler_refuses_cross_origin_or_downgrade(self):
        handler = _ArchidektSameOriginRedirect()
        for url in (
            "https://example.com/api/decks/1/",
            "http://archidekt.com/api/decks/1/",
            "https://user@archidekt.com/api/decks/1/",
        ):
            with self.assertRaises(DeckSourceFetchError):
                handler.redirect_request(None, None, 302, "Found", {}, url)

    def test_transport_failure_is_fail_closed(self):
        opener = _Opener(error=URLError("synthetic outage"))
        with self.assertRaisesRegex(DeckSourceFetchError, "complete response"):
            fetch_archidekt_snapshot(
                source_id="1",
                source_url="https://archidekt.com/decks/1/public-fixture",
                opener=opener,
            )

    def test_invalid_limits_fail_before_network_access(self):
        for max_bytes in (0, -1, True, 1.5):
            opener = _Opener(json.dumps(self.fixture()).encode("utf-8"))
            with self.assertRaises(DeckSourceFetchError):
                fetch_archidekt_snapshot(
                    source_id="1",
                    source_url="https://archidekt.com/decks/1/public-fixture",
                    opener=opener,
                    max_bytes=max_bytes,
                )
            self.assertEqual(opener.calls, 0)


if __name__ == "__main__":
    unittest.main()
