"""Expose canonical Commander Gym Bindings through Argentum's generic profile seam.

Argentum knows only an opaque provider profile id plus an optional fixed deck preset.
This module keeps Binding/Pilot/DeckKnowledge semantics on the Commander Gym side and
uses the same :class:`BindingResolver` as the Gym launch path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .binding_resolver import BindingResolutionError, BindingResolver, ResolvedSeatBinding
from .binding_session import BindingLaunchError, default_argentum_player_config
from .game_server_seat import GameServerSeatAdapter, ProvenanceSink
from .game_server_sidecar import GameServerSidecarConfig, GameServerSidecarServer


class GameServerBindingError(BindingResolutionError):
    """Raised when a Binding cannot become one exact game-server seat preset."""


@dataclass(frozen=True)
class GameServerBindingProfile:
    """One canonical Binding projected onto Argentum's generic provider profile."""

    binding_id: str
    display_name: str
    description: str | None
    deck_label: str
    deck_list: Mapping[str, int]
    commander: str | None

    def to_wire(self) -> dict[str, Any]:
        deck: dict[str, Any] = {
            "label": self.deck_label,
            "cards": dict(self.deck_list),
        }
        if self.commander is not None:
            deck["commander"] = self.commander
        result: dict[str, Any] = {
            "id": self.binding_id,
            "displayName": self.display_name,
            "deck": deck,
        }
        if self.description is not None:
            result["description"] = self.description
        return result


class GameServerBindingRegistry:
    """Resolve an explicit public set of Binding IDs for game-server selection.

    Resolution is eager for advertised metadata/decks so a stale Binding cannot be
    shown as selectable. Pilot construction remains per seat: each selected seat is
    resolved again and receives its own ArtificialPlayer instance.
    """

    def __init__(self, resolver: BindingResolver, binding_ids: Sequence[str]) -> None:
        if not isinstance(resolver, BindingResolver):
            raise GameServerBindingError("resolver must be a BindingResolver")
        if isinstance(binding_ids, (str, bytes)) or not isinstance(binding_ids, Sequence):
            raise GameServerBindingError("binding_ids must be a sequence")
        if not binding_ids:
            raise GameServerBindingError("binding_ids must not be empty")

        profiles: dict[str, GameServerBindingProfile] = {}
        for raw_id in binding_ids:
            if not isinstance(raw_id, str) or not raw_id.strip():
                raise GameServerBindingError("binding_ids must contain non-empty strings")
            binding_id = raw_id.strip()
            if binding_id in profiles:
                raise GameServerBindingError(f"duplicate Binding profile {binding_id!r}")
            try:
                resolved = resolver.resolve(binding_id)
                profile = self._profile_for(resolved)
            except (BindingResolutionError, BindingLaunchError) as exc:
                raise GameServerBindingError(str(exc)) from exc
            profiles[binding_id] = profile

        self._resolver = resolver
        self._profiles = profiles

    @staticmethod
    def _profile_for(resolved: ResolvedSeatBinding) -> GameServerBindingProfile:
        player = default_argentum_player_config(resolved, resolved.binding.artifact_id)
        deck = player.get("deck")
        if not isinstance(deck, Mapping) or deck.get("type") != "Explicit":
            raise GameServerBindingError(
                "game-server Binding profiles currently require an Argentum Explicit deck"
            )
        cards = deck.get("cards")
        if not isinstance(cards, Mapping) or not cards:
            raise GameServerBindingError("Binding profile deck cards must be a non-empty mapping")
        exact_cards: dict[str, int] = {}
        for name, count in cards.items():
            if not isinstance(name, str) or not name or type(count) is not int or count <= 0:
                raise GameServerBindingError("Binding profile deck cards must have positive counts")
            exact_cards[name] = count

        commander = player.get("commanderCardName")
        if commander is not None and (not isinstance(commander, str) or not commander):
            raise GameServerBindingError("Binding profile commander must be a non-empty string")

        display = resolved.display_metadata
        display_name = display.get("name", resolved.binding.artifact_id)
        deck_label = display.get("deck_name", resolved.deck.artifact_id)
        description = display.get("description")
        for value, label in ((display_name, "name"), (deck_label, "deck_name")):
            if not isinstance(value, str) or not value:
                raise GameServerBindingError(f"Binding display {label} must be a non-empty string")
        if description is not None and (not isinstance(description, str) or not description):
            raise GameServerBindingError("Binding display description must be a non-empty string")
        if description is None:
            pilot = resolved.artificial_player
            description = f"{pilot.name} {pilot.version}"

        return GameServerBindingProfile(
            binding_id=resolved.binding.artifact_id,
            display_name=display_name,
            description=description,
            deck_label=deck_label,
            deck_list=exact_cards,
            commander=commander,
        )

    @property
    def profiles(self) -> tuple[GameServerBindingProfile, ...]:
        return tuple(self._profiles.values())

    def profile_payloads(self) -> tuple[dict[str, Any], ...]:
        return tuple(profile.to_wire() for profile in self.profiles)

    def create_seat(
        self,
        player_id: str,
        binding_id: str,
        *,
        provenance_sink: ProvenanceSink | None = None,
    ) -> GameServerSeatAdapter:
        if binding_id not in self._profiles:
            raise GameServerBindingError(f"unknown Binding profile {binding_id!r}")
        try:
            resolved = self._resolver.resolve(binding_id)
        except BindingResolutionError as exc:
            raise GameServerBindingError(str(exc)) from exc
        return GameServerSeatAdapter(
            resolved.artificial_player,
            player_id,
            provenance_sink=provenance_sink,
        )


def build_binding_game_server_sidecar(
    config: GameServerSidecarConfig,
    registry: GameServerBindingRegistry,
    *,
    provenance_sink: ProvenanceSink | None = None,
) -> GameServerSidecarServer:
    """Build a Binding-only sidecar: callbacks without an explicit profile fail closed."""

    if not isinstance(registry, GameServerBindingRegistry):
        raise GameServerBindingError("registry must be a GameServerBindingRegistry")
    return GameServerSidecarServer(
        (config.bind_host, config.port),
        config,
        profiles=registry.profile_payloads(),
        profile_seat_factory=lambda player_id, profile_id: registry.create_seat(
            player_id,
            profile_id,
            provenance_sink=provenance_sink,
        ),
    )
