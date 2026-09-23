"""Resolve one canonical Binding into an exact deck payload and pilot runtime.

The resolver is Commander Gym-owned. Argentum receives only the resulting game/deck
configuration and never needs to understand DeckKnowledge, Pilot, or Binding schemas.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from .deck_package import ArtifactRef
from .identity import Binding, Deck, DeckKnowledge, IdentityError, IdentityRef, Pilot
from .pilot import ArtificialPlayer


class BindingResolutionError(IdentityError):
    """Raised when a Binding cannot be resolved exactly and safely."""


DeckPayloadLoader = Callable[[ArtifactRef], Mapping[str, Any]]
PilotFactory = Callable[[Pilot, Binding], ArtificialPlayer]


@dataclass(frozen=True)
class ResolvedSeatBinding:
    """Exact seat configuration produced from one canonical Binding."""

    binding: IdentityRef
    deck: IdentityRef
    pilot: IdentityRef
    deck_knowledge: IdentityRef | None
    exact_deck_payload: Mapping[str, Any]
    format_id: str
    format_metadata: Mapping[str, Any]
    artificial_player: ArtificialPlayer
    display_metadata: Mapping[str, Any]


class BindingResolver:
    """Resolve canonical artifacts without inventing missing lineage or fallbacks."""

    def __init__(
        self,
        *,
        bindings: Sequence[Binding],
        decks: Sequence[Deck],
        pilots: Sequence[Pilot],
        deck_knowledge: Sequence[DeckKnowledge] = (),
        deck_payload_loader: DeckPayloadLoader,
        pilot_factory: PilotFactory,
    ) -> None:
        self._bindings = self._index_bindings(bindings)
        self._decks = self._index_exact(decks, "deck", lambda value: value.ref())
        self._pilots = self._index_exact(pilots, "pilot", lambda value: value.ref())
        self._knowledge = self._index_exact(
            deck_knowledge,
            "deck_knowledge",
            lambda value: value.ref(),
        )
        if not callable(deck_payload_loader):
            raise BindingResolutionError("deck_payload_loader must be callable")
        if not callable(pilot_factory):
            raise BindingResolutionError("pilot_factory must be callable")
        self._deck_payload_loader = deck_payload_loader
        self._pilot_factory = pilot_factory

    @staticmethod
    def _index_exact(
        values: Sequence[Any],
        label: str,
        ref_for: Callable[[Any], IdentityRef],
    ) -> dict[tuple[str, str], Any]:
        result: dict[tuple[str, str], Any] = {}
        for value in values:
            ref = ref_for(value)
            ref.validate()
            key = (ref.artifact_id, ref.revision)
            if key in result:
                raise BindingResolutionError(
                    f"duplicate {label} revision {ref.artifact_id!r}@{ref.revision!r}"
                )
            result[key] = value
        return result

    @staticmethod
    def _index_bindings(bindings: Sequence[Binding]) -> dict[tuple[str, str], Binding]:
        result: dict[tuple[str, str], Binding] = {}
        for binding in bindings:
            if not isinstance(binding, Binding):
                raise BindingResolutionError("bindings must contain Binding values")
            binding.validate()
            key = (binding.binding_id, binding.revision)
            if key in result:
                raise BindingResolutionError(
                    f"duplicate Binding revision {binding.binding_id!r}@{binding.revision!r}"
                )
            result[key] = binding
        return result

    def _binding(self, binding_id: str, revision: str | None) -> Binding:
        binding_id = str(binding_id or "").strip()
        if not binding_id:
            raise BindingResolutionError("binding_id must be non-empty")
        if revision is not None:
            revision = str(revision or "").strip()
            if not revision:
                raise BindingResolutionError("binding revision must be non-empty")
            try:
                return self._bindings[(binding_id, revision)]
            except KeyError as exc:
                raise BindingResolutionError(
                    f"unknown Binding {binding_id!r}@{revision!r}"
                ) from exc

        matches = [
            binding
            for (candidate_id, _), binding in self._bindings.items()
            if candidate_id == binding_id
        ]
        if not matches:
            raise BindingResolutionError(f"unknown Binding {binding_id!r}")
        if len(matches) != 1:
            raise BindingResolutionError(
                f"Binding {binding_id!r} has multiple revisions; revision is required"
            )
        return matches[0]

    @staticmethod
    def _require_exact_ref(
        expected: IdentityRef,
        actual: IdentityRef,
        label: str,
    ) -> None:
        expected.validate()
        actual.validate()
        if expected != actual:
            raise BindingResolutionError(
                f"Binding {label} reference does not match the resolved artifact revision"
            )

    def resolve(
        self,
        binding_id: str,
        *,
        revision: str | None = None,
    ) -> ResolvedSeatBinding:
        """Resolve one Binding ID to exact immutable component revisions.

        No component is selected by friendly name alone. Any missing, stale, or
        mismatched reference fails closed rather than falling back to another deck,
        pilot, knowledge revision, or generic policy.
        """

        binding = self._binding(binding_id, revision)

        deck_key = (binding.deck.artifact_id, binding.deck.revision)
        try:
            deck = self._decks[deck_key]
        except KeyError as exc:
            raise BindingResolutionError("Binding references an unavailable Deck") from exc
        self._require_exact_ref(binding.deck, deck.ref(), "deck")

        pilot_key = (binding.pilot.artifact_id, binding.pilot.revision)
        try:
            pilot = self._pilots[pilot_key]
        except KeyError as exc:
            raise BindingResolutionError("Binding references an unavailable Pilot") from exc
        self._require_exact_ref(binding.pilot, pilot.ref(), "pilot")

        knowledge_ref: IdentityRef | None = None
        if binding.deck_knowledge is not None:
            knowledge_key = (
                binding.deck_knowledge.artifact_id,
                binding.deck_knowledge.revision,
            )
            try:
                knowledge = self._knowledge[knowledge_key]
            except KeyError as exc:
                raise BindingResolutionError(
                    "Binding references unavailable DeckKnowledge"
                ) from exc
            self._require_exact_ref(
                binding.deck_knowledge,
                knowledge.ref(),
                "deck_knowledge",
            )
            knowledge_ref = knowledge.ref()

        payload = self._deck_payload_loader(deck.deck_artifact)
        if not isinstance(payload, Mapping):
            raise BindingResolutionError("deck_payload_loader must return a mapping")
        exact_deck_payload = dict(payload)

        artificial_player = self._pilot_factory(pilot, binding)
        for field_name in ("name", "version"):
            value = getattr(artificial_player, field_name, None)
            if not isinstance(value, str) or not value:
                raise BindingResolutionError(
                    f"resolved artificial player {field_name} must be non-empty"
                )
        if not callable(getattr(artificial_player, "choose", None)):
            raise BindingResolutionError("resolved artificial player must implement choose()")

        display = binding.metadata.get("display", {})
        if display is None:
            display = {}
        if not isinstance(display, Mapping):
            raise BindingResolutionError("binding metadata.display must be a mapping")

        return ResolvedSeatBinding(
            binding=binding.ref(),
            deck=deck.ref(),
            pilot=pilot.ref(),
            deck_knowledge=knowledge_ref,
            exact_deck_payload=exact_deck_payload,
            format_id=deck.format_id,
            format_metadata=dict(deck.format_metadata),
            artificial_player=artificial_player,
            display_metadata=dict(display),
        )
