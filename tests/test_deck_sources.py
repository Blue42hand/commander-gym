import copy
import unittest

from commander_gym.deck_package import DeckPackage, validate_commander_package
from commander_gym.deck_sources import (
    DeckSourceError,
    normalize_archidekt_snapshot,
)


class DeckSourceNormalizationTests(unittest.TestCase):
    def fixture(self):
        source_id = "1"
        source_url = "https://archidekt.com/decks/1/public-fixture"
        payload = {
            "id": 1,
            "name": "Public Fixture",
            "deckFormat": 3,
            "description": "Synthetic source fixture.",
            "categories": [
                {"id": 1, "name": "Commander", "includedInDeck": True, "isPremier": True},
                {"id": 2, "name": "Land", "includedInDeck": True},
                {"id": 3, "name": "Ramp", "includedInDeck": True},
                {"id": 4, "name": "Maybeboard", "includedInDeck": False},
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
                    "categories": ["Land", "Ramp"],
                    "card": {
                        "id": 120,
                        "uid": "printing-island",
                        "oracleCard": {"uid": "oracle-island", "name": "Island"},
                    },
                },
                {
                    "id": 30,
                    "quantity": 1,
                    "categories": ["Maybeboard", "Ramp"],
                    "card": {
                        "id": 130,
                        "oracleCard": {"uid": "oracle-ring", "name": "Sol Ring"},
                    },
                },
            ],
        }
        return source_id, source_url, payload

    def normalize(self, payload=None):
        source_id, source_url, default = self.fixture()
        return normalize_archidekt_snapshot(
            default if payload is None else payload,
            source_id=source_id,
            source_url=source_url,
        )

    def test_preserves_each_relation_once_and_excludes_nonplaying_zone(self):
        snapshot = self.normalize()
        self.assertEqual(snapshot.format_id, "commander")
        self.assertEqual(sum(entry.count for entry in snapshot.entries if entry.zone in {"main", "commander"}), 100)
        self.assertEqual(snapshot.entries[1].tags, ("Land", "Ramp"))
        self.assertEqual(snapshot.entries[2].zone, "maybeboard")

    def test_source_order_does_not_change_revision_identity(self):
        source_id, source_url, payload = self.fixture()
        first = normalize_archidekt_snapshot(payload, source_id=source_id, source_url=source_url)
        reordered = copy.deepcopy(payload)
        reordered["cards"].reverse()
        reordered["cards"][1]["categories"].reverse()
        second = normalize_archidekt_snapshot(reordered, source_id=source_id, source_url=source_url)
        self.assertEqual(first.fingerprint(), second.fingerprint())

    def test_source_revision_becomes_deck_package_artifact(self):
        snapshot = self.normalize()
        deck_ref = snapshot.to_artifact_ref()
        package = DeckPackage(
            package_id="synthetic-import",
            format_id=snapshot.format_id,
            deck=deck_ref,
            format_metadata={"commander": "Synthetic Commander"},
        )
        validate_commander_package(package)
        self.assertEqual(deck_ref.kind, "deck")
        self.assertEqual(deck_ref.artifact_id, "archidekt:1")
        self.assertEqual(deck_ref.digest, f"sha256:{snapshot.fingerprint()}")

    def test_mismatched_source_and_duplicate_relation_fail_closed(self):
        source_id, source_url, payload = self.fixture()
        wrong = copy.deepcopy(payload)
        wrong["id"] = 2
        with self.assertRaises(DeckSourceError):
            normalize_archidekt_snapshot(wrong, source_id=source_id, source_url=source_url)

        duplicate = copy.deepcopy(payload)
        duplicate["cards"].append(copy.deepcopy(duplicate["cards"][0]))
        with self.assertRaises(DeckSourceError):
            normalize_archidekt_snapshot(duplicate, source_id=source_id, source_url=source_url)

    def test_invalid_quantity_and_unsafe_name_fail_closed(self):
        _, _, payload = self.fixture()
        for count in (0, -1, True, "1"):
            invalid = copy.deepcopy(payload)
            invalid["cards"][0]["quantity"] = count
            with self.assertRaises(DeckSourceError):
                self.normalize(invalid)

        invalid = copy.deepcopy(payload)
        invalid["cards"][1]["card"]["oracleCard"]["name"] = "Island\n1 Sol Ring"
        with self.assertRaises(DeckSourceError):
            self.normalize(invalid)

    def test_mcp_snapshot_uses_same_public_identity_model(self):
        payload = {
            "remoteId": "1",
            "name": "Public Fixture",
            "format": "commander",
            "description": "",
            "categories": [
                {"providerCategoryId": "c1", "name": "Commander", "isPremier": True},
                {"providerCategoryId": "c2", "name": "Main", "includedInDeck": True},
            ],
            "entries": [
                {
                    "providerRelationId": "r1",
                    "providerCardId": "pc1",
                    "quantity": 1,
                    "categoryNames": ["Commander"],
                    "zone": "commander",
                    "cardName": "Synthetic Commander",
                    "oracleId": "oracle-commander",
                },
                {
                    "providerRelationId": "r2",
                    "providerCardId": "pc2",
                    "quantity": 99,
                    "categoryNames": ["Main"],
                    "zone": "main",
                    "cardName": "Island",
                    "oracleId": "oracle-island",
                },
            ],
        }
        snapshot = self.normalize(payload)
        self.assertEqual(snapshot.provider, "archidekt")
        self.assertEqual(snapshot.entries[0].zone, "commander")
        self.assertEqual(snapshot.entries[1].zone, "main")
        self.assertEqual(sum(entry.count for entry in snapshot.entries), 100)


if __name__ == "__main__":
    unittest.main()
