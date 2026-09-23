from __future__ import annotations

import unittest

from commander_gym.binding_resolver import BindingResolutionError, BindingResolver
from commander_gym.deck_package import ArtifactRef
from commander_gym.identity import Binding, Deck, DeckKnowledge, IdentityRef, Pilot
from commander_gym.pilot import ArgentumActionChoice


class DummyPilot:
    name = "dummy"
    version = "1"

    def choose(self, observation):
        return ArgentumActionChoice(action_id=0)


def artifacts():
    deck_artifact = ArtifactRef(
        kind="decklist",
        artifact_id="deck-payload",
        version="v1",
        digest="sha256:deck",
    )
    knowledge_artifact = ArtifactRef(
        kind="primer",
        artifact_id="primer-payload",
        version="v1",
        digest="sha256:primer",
    )
    deck = Deck(
        deck_id="deck-a",
        revision="r1",
        format_id="commander",
        deck_artifact=deck_artifact,
        format_metadata={"commander": "Synthetic Commander"},
    )
    knowledge = DeckKnowledge(
        knowledge_id="knowledge-a",
        revision="r1",
        content=knowledge_artifact,
    )
    pilot = Pilot(pilot_id="pilot-a", revision="r1")
    binding = Binding(
        binding_id="seat-a",
        revision="r1",
        deck=deck.ref(),
        deck_knowledge=knowledge.ref(),
        pilot=pilot.ref(),
        metadata={"display": {"label": "Synthetic seat"}},
    )
    return deck, knowledge, pilot, binding


class BindingResolverTests(unittest.TestCase):
    def resolver(self, *, bindings=None, decks=None, pilots=None, knowledge=None):
        deck, deck_knowledge, pilot, binding = artifacts()
        return BindingResolver(
            bindings=list(bindings if bindings is not None else [binding]),
            decks=list(decks if decks is not None else [deck]),
            pilots=list(pilots if pilots is not None else [pilot]),
            deck_knowledge=list(
                knowledge if knowledge is not None else [deck_knowledge]
            ),
            deck_payload_loader=lambda ref: {
                "artifact_id": ref.artifact_id,
                "cards": ["Synthetic Commander", "Forest"],
            },
            pilot_factory=lambda pilot_manifest, binding_manifest: DummyPilot(),
        )

    def test_resolves_exact_binding_components(self):
        deck, knowledge, pilot, binding = artifacts()
        resolved = self.resolver().resolve("seat-a")

        self.assertEqual(resolved.binding, binding.ref())
        self.assertEqual(resolved.deck, deck.ref())
        self.assertEqual(resolved.deck_knowledge, knowledge.ref())
        self.assertEqual(resolved.pilot, pilot.ref())
        self.assertEqual(resolved.format_id, "commander")
        self.assertEqual(
            resolved.format_metadata,
            {"commander": "Synthetic Commander"},
        )
        self.assertEqual(resolved.exact_deck_payload["artifact_id"], "deck-payload")
        self.assertEqual(resolved.display_metadata, {"label": "Synthetic seat"})
        self.assertEqual(resolved.artificial_player.name, "dummy")

    def test_rejects_stale_component_fingerprint(self):
        deck, knowledge, pilot, binding = artifacts()
        stale = Binding(
            binding_id=binding.binding_id,
            revision=binding.revision,
            deck=IdentityRef(
                artifact_type="deck",
                artifact_id=deck.deck_id,
                revision=deck.revision,
                fingerprint="0" * 64,
            ),
            deck_knowledge=knowledge.ref(),
            pilot=pilot.ref(),
        )
        resolver = self.resolver(bindings=[stale])
        with self.assertRaisesRegex(BindingResolutionError, "does not match"):
            resolver.resolve("seat-a")

    def test_missing_knowledge_fails_closed(self):
        _, _, _, binding = artifacts()
        resolver = self.resolver(knowledge=[])
        with self.assertRaisesRegex(BindingResolutionError, "unavailable DeckKnowledge"):
            resolver.resolve(binding.binding_id)

    def test_multiple_binding_revisions_require_explicit_revision(self):
        deck, knowledge, pilot, binding = artifacts()
        binding_r2 = Binding(
            binding_id=binding.binding_id,
            revision="r2",
            deck=deck.ref(),
            deck_knowledge=knowledge.ref(),
            pilot=pilot.ref(),
        )
        resolver = self.resolver(bindings=[binding, binding_r2])
        with self.assertRaisesRegex(BindingResolutionError, "revision is required"):
            resolver.resolve("seat-a")
        resolved = resolver.resolve("seat-a", revision="r2")
        self.assertEqual(resolved.binding.revision, "r2")

    def test_unknown_binding_never_falls_back(self):
        resolver = self.resolver()
        with self.assertRaisesRegex(BindingResolutionError, "unknown Binding"):
            resolver.resolve("missing")


if __name__ == "__main__":
    unittest.main()
