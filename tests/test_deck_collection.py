from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest

from commander_gym.deck_collection import CollectionError, parse_card_list, validate_collection
from commander_gym.identity import Binding, Deck, DeckKnowledge, Pilot
from commander_gym.deck_package import ArtifactRef
import hashlib


class DeckCollectionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "instance"
        self.root.mkdir()
        self.directory = "decks/talrand-test"
        payload = {"schema_version": 1, "deck_id": "talrand-test", "name": "Talrand test",
                   "commander": "Talrand, Sky Summoner", "cards": {"Talrand, Sky Summoner": 1, "Island": 99}}
        blob = (json.dumps(payload, indent=2) + "\n").encode()
        deck = Deck("talrand-test", "d1", "commander",
                    ArtifactRef("decklist", "talrand-cards", "d1", "sha256:" + hashlib.sha256(blob).hexdigest()),
                    format_metadata={"commanders": ["Talrand, Sky Summoner"]})
        primer = b"# Talrand\n\nKeep lands and cheap spells.\n"
        knowledge = DeckKnowledge("talrand-primer", "k1",
                                  ArtifactRef("deck_knowledge", "talrand-primer", "k1",
                                              "sha256:" + hashlib.sha256(primer).hexdigest()),
                                  compatibility={"deck_id": deck.deck_id, "deck_revisions": [deck.revision]})
        pilot = Pilot("test-pilot", "p1")
        binding = Binding("talrand-binding", "b1", deck.ref(), pilot.ref(), knowledge.ref())
        self.deck_paths = {"manifest": self.directory + "/revisions/d1/manifest.json",
                           "payload": self.directory + "/revisions/d1/deck.json",
                           "card_list": self.directory + "/revisions/d1/decklist.txt"}
        self.knowledge_paths = {"manifest": self.directory + "/knowledge/k1/knowledge.json",
                                "payload": self.directory + "/knowledge/k1/primer.md"}
        self.binding_path = "bindings/talrand-binding/revisions/b1/binding.json"
        self.write(self.deck_paths["manifest"], deck.to_dict())
        self.write(self.deck_paths["payload"], blob)
        self.write(self.knowledge_paths["manifest"], knowledge.to_dict())
        self.write(self.knowledge_paths["payload"], primer)
        self.write(self.binding_path, binding.to_dict())
        self.write("pilots/test/revisions/p1/pilot.json", pilot.to_dict())
        self.write(self.directory + "/sources/original.txt", b"1 Talrand, Sky Summoner\n99 Island\n")
        self.entry = {"name": "Talrand test", "directory": self.directory, "status": "imported",
                      "deck": self.deck_paths, "deck_knowledge": self.knowledge_paths, "binding": self.binding_path,
                      "sources": [self.directory + "/sources"],
                      "coverage": {"status": "not-checked", "detail": "No engine coverage claim."},
                      "qualification": {"status": "not-qualified", "detail": "No game was played."},
                      "card_source": {"path": self.directory + "/sources/original.txt", "format": "text"}}
        self.catalog = {"schema_version": 1, "collection_schema_version": 1,
                        "bindings": [self.binding_path], "decks": [{k: self.deck_paths[k] for k in ("manifest", "payload")}],
                        "deck_knowledge": [self.knowledge_paths], "pilots": ["pilots/test/revisions/p1/pilot.json"],
                        "active_bindings": [binding.binding_id], "collection": [self.entry]}
        self.save_catalog()
        validate_collection(self.root, write=True)

    def write(self, path, value):
        if isinstance(value, dict):
            value = (json.dumps(value, indent=2) + "\n").encode()
        p = self.root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(value)

    def save_catalog(self):
        self.write("catalogs/deck-collection.json", self.catalog)

    def test_relocation_and_production_profile_validation_without_provider(self):
        relocated = Path(self.temp.name) / "relocated"
        shutil.copytree(self.root, relocated)
        report = validate_collection(relocated)
        self.assertEqual(report["decks"], 1)
        self.assertEqual(report["bindings"], 1)
        self.assertIn("not-checked", (relocated / "decks/README.md").read_text())
        self.assertIn("not-qualified", (relocated / self.directory / "README.md").read_text())

    def test_text_reader_preserves_repeated_names_split_names_and_tags(self):
        cards = parse_card_list("1 Talrand, Sky Summoner [Commander{top}]\nIsland\n2 Island [Lands]\n"
                                "1 Malakir Rebirth // Malakir Mire [Protection]\n")
        self.assertEqual(cards, {"Talrand, Sky Summoner": 1, "Island": 3,
                                 "Malakir Rebirth // Malakir Mire": 1})
        with self.assertRaises(CollectionError):
            parse_card_list("0 Island")

    def test_stale_generated_card_list_and_index_fail(self):
        for path in (self.deck_paths["card_list"], self.directory + "/index.json", "decks/README.md"):
            with self.subTest(path=path):
                original = (self.root / path).read_bytes()
                self.write(path, b"edited independently\n")
                with self.assertRaisesRegex(CollectionError, "stale generated view"):
                    validate_collection(self.root)
                self.write(path, original)

    def test_tampered_payload_fails_digest_check(self):
        path = self.root / self.deck_paths["payload"]
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "digest does not match"):
            validate_collection(self.root)

    def test_valid_identity_with_wrong_primer_compatibility_fails(self):
        raw = json.loads((self.root / self.knowledge_paths["manifest"]).read_text())
        raw.pop("fingerprint")
        raw["compatibility"]["deck_revisions"] = ["different-deck-revision"]
        knowledge = DeckKnowledge.from_dict(raw)
        self.write(self.knowledge_paths["manifest"], knowledge.to_dict())
        binding_raw = json.loads((self.root / self.binding_path).read_text())
        binding_raw.pop("fingerprint")
        binding_raw["deck_knowledge"] = knowledge.ref().to_dict()
        self.write(self.binding_path, Binding.from_dict(binding_raw).to_dict())
        with self.assertRaisesRegex(CollectionError, "primer does not explicitly cover"):
            validate_collection(self.root)

    def test_unindexed_directory_fails(self):
        (self.root / "decks/forgotten-deck").mkdir()
        with self.assertRaisesRegex(CollectionError, "unindexed deck directories"):
            validate_collection(self.root)

    def test_catalog_and_collection_selection_must_match(self):
        self.catalog["decks"].append(self.catalog["decks"][0])
        self.save_catalog()
        with self.assertRaisesRegex(ValueError, "duplicate playable payload"):
            validate_collection(self.root)

    def test_preserved_source_and_card_equality_are_checked(self):
        source = self.entry["card_source"]["path"]
        blob = (self.root / source).read_bytes()
        git_blob = hashlib.sha1(b"blob " + str(len(blob)).encode() + b"\0" + blob).hexdigest()
        self.catalog["migration"] = "migrations/test.json"
        ledger = {"preserved_files": [{"original_path": "old.txt", "path": source, "git_blob": git_blob}]}
        self.write("migrations/test.json", ledger)
        self.save_catalog()
        validate_collection(self.root)
        self.write(source, b"1 Talrand, Sky Summoner\n98 Island\n")
        with self.assertRaisesRegex(CollectionError, "changed preserved source"):
            validate_collection(self.root)
        new_blob = (self.root / source).read_bytes()
        ledger["preserved_files"][0]["git_blob"] = hashlib.sha1(b"blob " + str(len(new_blob)).encode() + b"\0" + new_blob).hexdigest()
        self.write("migrations/test.json", ledger)
        with self.assertRaisesRegex(CollectionError, "migration changed card quantities"):
            validate_collection(self.root)

    def test_collection_paths_cannot_escape_instance(self):
        self.entry["deck"]["card_list"] = "../outside.txt"
        self.save_catalog()
        with self.assertRaisesRegex(ValueError, "escapes the instance root"):
            validate_collection(self.root, write=True)


if __name__ == "__main__":
    unittest.main()
