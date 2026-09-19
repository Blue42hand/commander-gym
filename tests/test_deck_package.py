import json
from pathlib import Path
import unittest

from commander_gym.deck_package import (
    ArtifactRef,
    DeckPackage,
    DeckPackageError,
    validate_commander_package,
)


class DeckPackageV2Tests(unittest.TestCase):
    def make_package(self, **overrides):
        data = dict(
            package_id="synthetic-commander-v1",
            format_id="commander",
            deck=ArtifactRef("deck", "synthetic-list", "1", digest="sha256:deck"),
            format_metadata={"commander": "Synthetic Commander"},
            deck_knowledge=ArtifactRef("primer", "synthetic-primer", "1"),
            deterministic_policy=ArtifactRef("policy", "synthetic-lines", "1"),
            generalist_base=ArtifactRef("model", "generalist", "v1"),
            specialist=None,
            training_provenance=("synthetic-fixture",),
            evaluation_refs=("public-benchmark-v1",),
            metapool_refs=("synthetic-pool-v1",),
            compatibility={"argentum_schema": "v1"},
        )
        data.update(overrides)
        return DeckPackage(**data)

    def test_commander_package_accepts_format_specific_commander(self):
        package = self.make_package()
        validate_commander_package(package)
        self.assertEqual(len(package.fingerprint()), 64)

    def test_non_commander_package_does_not_require_commander(self):
        package = self.make_package(
            package_id="synthetic-modern-v1",
            format_id="modern",
            format_metadata={"sideboard_cards": 15},
        )
        package.validate()
        self.assertEqual(package.format_id, "modern")

    def test_commander_validator_fails_closed_without_commander(self):
        package = self.make_package(format_metadata={})
        with self.assertRaises(DeckPackageError):
            validate_commander_package(package)

    def test_specialist_is_optional_and_changes_identity_when_present(self):
        base = self.make_package()
        specialized = self.make_package(
            specialist=ArtifactRef("specialist", "synthetic-adapter", "generation-2")
        )
        self.assertIsNone(base.specialist)
        self.assertFalse(base.same_experiment(specialized))

    def test_package_id_is_not_semantic_identity(self):
        left = self.make_package(package_id="friendly-name")
        right = self.make_package(package_id="other-label")
        self.assertTrue(left.same_experiment(right))

    def test_format_and_format_metadata_are_semantic_identity(self):
        base = self.make_package()
        other_format = self.make_package(format_id="legacy")
        other_commander = self.make_package(
            format_metadata={"commander": "Another Synthetic Commander"}
        )
        self.assertFalse(base.same_experiment(other_format))
        self.assertFalse(base.same_experiment(other_commander))

    def test_round_trip_preserves_identity(self):
        package = self.make_package()
        loaded = DeckPackage.from_dict(package.to_dict())
        self.assertEqual(package.fingerprint(), loaded.fingerprint())
        self.assertTrue(package.same_experiment(loaded))

    def test_checked_in_public_fixture_is_valid_and_stable(self):
        fixture_path = (
            Path(__file__).resolve().parents[1]
            / "fixtures"
            / "deck_package_v2_commander.json"
        )
        raw = json.loads(fixture_path.read_text(encoding="utf-8"))
        package = DeckPackage.from_dict(raw)

        validate_commander_package(package)
        self.assertEqual(package.package_id, "public-synthetic-commander-v1")
        self.assertEqual(package.fingerprint(), raw["fingerprint"])

    def test_mismatched_fingerprint_is_rejected(self):
        raw = self.make_package().to_dict()
        raw["fingerprint"] = "0" * 64
        with self.assertRaises(DeckPackageError):
            DeckPackage.from_dict(raw)

    def test_unsupported_schema_is_rejected(self):
        raw = self.make_package().to_dict()
        raw["schema_version"] = 999
        raw.pop("fingerprint", None)
        with self.assertRaises(DeckPackageError):
            DeckPackage.from_dict(raw)


if __name__ == "__main__":
    unittest.main()
