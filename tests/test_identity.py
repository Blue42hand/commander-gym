import unittest

from commander_gym.deck_package import ArtifactRef, DeckPackage
from commander_gym.identity import (
    Binding,
    Deck,
    DeckKnowledge,
    IdentityError,
    Pilot,
    migrate_deck_package_v2,
)


class CanonicalIdentityTests(unittest.TestCase):
    def make_package(self, **overrides):
        data = dict(
            package_id="synthetic-binding",
            format_id="commander",
            deck=ArtifactRef(
                "deck", "synthetic-list", "1", digest="sha256:deck"
            ),
            format_metadata={"commander": "Synthetic Commander"},
            deck_knowledge=ArtifactRef("primer", "synthetic-primer", "1"),
            deterministic_policy=ArtifactRef("policy", "synthetic-lines", "1"),
            generalist_base=ArtifactRef("model", "generalist", "v1"),
            specialist=None,
            training_generation="pilot-v1",
            compatibility={"argentum_schema": "v1"},
        )
        data.update(overrides)
        return DeckPackage(**data)

    def test_deck_identity_is_independent_of_pilot(self):
        base = migrate_deck_package_v2(self.make_package())
        changed = migrate_deck_package_v2(
            self.make_package(
                generalist_base=ArtifactRef("model", "generalist", "v2")
            )
        )

        self.assertEqual(base.deck.fingerprint(), changed.deck.fingerprint())
        self.assertNotEqual(base.pilot.fingerprint(), changed.pilot.fingerprint())
        self.assertNotEqual(base.binding.fingerprint(), changed.binding.fingerprint())

    def test_pilot_identity_is_independent_of_deck(self):
        base = migrate_deck_package_v2(self.make_package())
        changed = migrate_deck_package_v2(
            self.make_package(
                deck=ArtifactRef(
                    "deck", "other-list", "7", digest="sha256:other"
                ),
                format_metadata={"commander": "Other Commander"},
            )
        )

        self.assertNotEqual(base.deck.fingerprint(), changed.deck.fingerprint())
        self.assertEqual(base.pilot.fingerprint(), changed.pilot.fingerprint())
        self.assertNotEqual(base.binding.fingerprint(), changed.binding.fingerprint())

    def test_deck_knowledge_changes_independently(self):
        base = migrate_deck_package_v2(self.make_package())
        changed = migrate_deck_package_v2(
            self.make_package(
                deck_knowledge=ArtifactRef("primer", "synthetic-primer", "2")
            )
        )

        self.assertEqual(base.deck.fingerprint(), changed.deck.fingerprint())
        self.assertEqual(base.pilot.fingerprint(), changed.pilot.fingerprint())
        self.assertNotEqual(
            base.deck_knowledge.fingerprint(),
            changed.deck_knowledge.fingerprint(),
        )
        self.assertNotEqual(base.binding.fingerprint(), changed.binding.fingerprint())

    def test_binding_references_exact_component_revisions(self):
        migrated = migrate_deck_package_v2(self.make_package())

        self.assertEqual(migrated.binding.deck, migrated.deck.ref())
        self.assertEqual(migrated.binding.pilot, migrated.pilot.ref())
        self.assertEqual(
            migrated.binding.deck_knowledge,
            migrated.deck_knowledge.ref(),
        )
        self.assertEqual(len(migrated.binding.fingerprint()), 64)

    def test_binding_label_does_not_change_exact_combination_fingerprint(self):
        first = migrate_deck_package_v2(self.make_package(package_id="friendly-a"))
        second = migrate_deck_package_v2(self.make_package(package_id="friendly-b"))

        self.assertEqual(first.binding.fingerprint(), second.binding.fingerprint())

    def test_each_identity_round_trips_and_rejects_tampering(self):
        migrated = migrate_deck_package_v2(self.make_package())
        cases = (
            (Deck, migrated.deck),
            (DeckKnowledge, migrated.deck_knowledge),
            (Pilot, migrated.pilot),
            (Binding, migrated.binding),
        )

        for cls, value in cases:
            raw = value.to_dict()
            self.assertEqual(cls.from_dict(raw), value)
            raw["fingerprint"] = "0" * 64
            with self.assertRaises(IdentityError):
                cls.from_dict(raw)

    def test_legacy_migration_is_explicitly_bounded_to_deck_package(self):
        with self.assertRaises(IdentityError):
            migrate_deck_package_v2({"schema_version": 2})


if __name__ == "__main__":
    unittest.main()
