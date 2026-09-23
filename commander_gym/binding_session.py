"""Resolve canonical Bindings into one authoritative Argentum Gym launch plan.

This module is the Gym-side execution seam for issue #76. Callers provide only
Binding IDs plus game-level settings; the exact deck payload and ArtificialPlayer
for every seat come from :class:`BindingResolver`.

Argentum still owns format legality, rules, and authoritative state. The small
Commander adapter below only translates the existing public exact-deck artifact
shape into Argentum's explicit-player configuration. Other formats may supply a
format-specific player-config factory without changing Binding or pilot identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from .binding_resolver import BindingResolutionError, BindingResolver, ResolvedSeatBinding
from .full_game import FullGameResult, run_full_game
from .orchestration import ArgentumOrchestrator
from .pilot_session import PilotSeat


class BindingLaunchError(BindingResolutionError):
    """Raised when canonical Bindings cannot produce one exact Gym launch."""


ArgentumPlayerFactory = Callable[[ResolvedSeatBinding, str], Mapping[str, Any]]


@dataclass(frozen=True)
class BindingLaunchPlan:
    """Exact four-seat game configuration produced only from canonical Bindings."""

    config: Mapping[str, Any]
    seats: tuple[PilotSeat, ...]
    resolved: tuple[ResolvedSeatBinding, ...]


def _nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise BindingLaunchError(f"{label} must be a non-empty string")
    return value


def _positive_card_counts(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping) or not value:
        raise BindingLaunchError("exact deck payload cards must be a non-empty mapping")
    result: dict[str, int] = {}
    for name, count in value.items():
        card_name = _nonempty_string(name, "exact deck payload card name")
        if type(count) is not int or count <= 0:
            raise BindingLaunchError(
                f"exact deck payload count for {card_name!r} must be a positive integer"
            )
        if card_name in result:
            raise BindingLaunchError(f"duplicate exact deck payload card {card_name!r}")
        result[card_name] = count
    return result


def default_argentum_player_config(
    resolved: ResolvedSeatBinding,
    player_name: str,
) -> Mapping[str, Any]:
    """Translate one resolved exact deck payload into an Argentum player config.

    A deck artifact may publish a prebuilt ``argentum_player`` mapping when its
    format needs a different adapter. Otherwise the current public Commander deck
    artifact shape (``cards`` plus ``commander``) is translated to Argentum's
    ``Explicit`` deck representation. No legality or rules validation happens here.
    """

    player_name = _nonempty_string(player_name, "player_name")
    payload = resolved.exact_deck_payload
    if not isinstance(payload, Mapping):
        raise BindingLaunchError("resolved exact_deck_payload must be a mapping")

    prebuilt = payload.get("argentum_player")
    if prebuilt is not None:
        if not isinstance(prebuilt, Mapping):
            raise BindingLaunchError("exact deck payload argentum_player must be a mapping")
        player = dict(prebuilt)
        # Seat naming is runtime configuration, not deck identity. Bindings remain
        # the only source of deck/pilot selection.
        player["name"] = player_name
        if "deck" not in player:
            raise BindingLaunchError("exact deck payload argentum_player is missing deck")
        return player

    if resolved.format_id != "commander":
        raise BindingLaunchError(
            f"no default Argentum player adapter for format {resolved.format_id!r}"
        )

    commander = payload.get("commander")
    if commander is None:
        commander = resolved.format_metadata.get("commander")
    commander = _nonempty_string(commander, "Commander exact deck payload commander")
    cards = _positive_card_counts(payload.get("cards"))

    # Public exact-deck artifacts include the commander in their total card map,
    # whereas Argentum's explicit library payload carries commanders separately.
    commander_count = cards.get(commander)
    if commander_count is None:
        raise BindingLaunchError(
            "Commander exact deck payload must include the commander in cards"
        )
    if commander_count == 1:
        cards.pop(commander)
    else:
        cards[commander] = commander_count - 1

    return {
        "name": player_name,
        "deck": {"type": "Explicit", "cards": cards},
        "commanderCardName": commander,
    }


def _seat_name(resolved: ResolvedSeatBinding) -> str:
    published = resolved.display_metadata.get("player_name")
    if published is not None:
        return _nonempty_string(published, "binding display player_name")
    return resolved.binding.artifact_id


def _pilot_config(resolved: ResolvedSeatBinding) -> dict[str, Any]:
    """Keep exact canonical identity in existing run metadata until #53 envelopes it."""

    return {
        "source": "canonical-binding-v1",
        "binding": resolved.binding.to_dict(),
        "deck": resolved.deck.to_dict(),
        "deck_knowledge": (
            resolved.deck_knowledge.to_dict()
            if resolved.deck_knowledge is not None
            else None
        ),
        "pilot": resolved.pilot.to_dict(),
    }


def build_binding_launch_plan(
    resolver: BindingResolver,
    binding_ids: Sequence[str],
    game_settings: Mapping[str, Any],
    *,
    player_config_factory: ArgentumPlayerFactory = default_argentum_player_config,
) -> BindingLaunchPlan:
    """Resolve four Binding IDs into both Argentum deck config and pilot seats.

    ``game_settings`` contains only game-level configuration. Supplying ``players``
    is rejected so callers cannot silently pair a Binding-selected pilot with an
    independently selected deck.
    """

    if not isinstance(resolver, BindingResolver):
        raise BindingLaunchError("resolver must be a BindingResolver")
    if not isinstance(game_settings, Mapping):
        raise BindingLaunchError("game_settings must be a mapping")
    if "players" in game_settings:
        raise BindingLaunchError(
            "game_settings must not contain players; Binding resolution owns seat decks"
        )
    if not isinstance(binding_ids, Sequence) or isinstance(binding_ids, (str, bytes)):
        raise BindingLaunchError("binding_ids must be a sequence of four Binding IDs")
    if len(binding_ids) != 4:
        raise BindingLaunchError("Binding-first Gym launch requires exactly four Binding IDs")
    if not callable(player_config_factory):
        raise BindingLaunchError("player_config_factory must be callable")

    resolved: list[ResolvedSeatBinding] = []
    seats: list[PilotSeat] = []
    players: list[Mapping[str, Any]] = []
    names: set[str] = set()

    for index, binding_id in enumerate(binding_ids):
        binding_id = _nonempty_string(binding_id, f"binding_ids[{index}]")
        try:
            seat_binding = resolver.resolve(binding_id)
        except BindingResolutionError as exc:
            raise BindingLaunchError(str(exc)) from exc
        player_name = _seat_name(seat_binding)
        if player_name in names:
            raise BindingLaunchError(
                f"resolved Binding player names must be unique; duplicate {player_name!r}"
            )
        names.add(player_name)

        player = player_config_factory(seat_binding, player_name)
        if not isinstance(player, Mapping):
            raise BindingLaunchError("player_config_factory must return a mapping")
        player = dict(player)
        if player.get("name") != player_name:
            raise BindingLaunchError(
                "player_config_factory must preserve the Binding-derived player name"
            )
        if "deck" not in player:
            raise BindingLaunchError("resolved Argentum player config is missing deck")

        seats.append(
            PilotSeat(
                player_name=player_name,
                pilot=seat_binding.artificial_player,
                deck_id=seat_binding.deck.artifact_id,
                deck_version=seat_binding.deck.revision,
                primer_version=(
                    seat_binding.deck_knowledge.revision
                    if seat_binding.deck_knowledge is not None
                    else None
                ),
                pilot_config=_pilot_config(seat_binding),
            )
        )
        players.append(player)
        resolved.append(seat_binding)

    config = dict(game_settings)
    config["players"] = players
    return BindingLaunchPlan(
        config=config,
        seats=tuple(seats),
        resolved=tuple(resolved),
    )


def run_binding_full_game(
    orchestrator: ArgentumOrchestrator,
    resolver: BindingResolver,
    binding_ids: Sequence[str],
    game_settings: Mapping[str, Any],
    *,
    run_id: str,
    max_choices: int = 100_000,
    seed: int | None = None,
    repeated_state_limit: int = 8,
    player_config_factory: ArgentumPlayerFactory = default_argentum_player_config,
) -> FullGameResult:
    """Run the normal full-game path with Bindings as the only seat inputs."""

    plan = build_binding_launch_plan(
        resolver,
        binding_ids,
        game_settings,
        player_config_factory=player_config_factory,
    )
    return run_full_game(
        orchestrator,
        plan.config,
        plan.seats,
        run_id=run_id,
        max_choices=max_choices,
        seed=seed,
        repeated_state_limit=repeated_state_limit,
    )
