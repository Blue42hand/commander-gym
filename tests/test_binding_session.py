from __future__ import annotations

import unittest
from typing import Any, Mapping, Sequence

from commander_gym.binding_resolver import BindingResolver
from commander_gym.binding_session import (
    BindingLaunchError,
    build_binding_launch_plan,
    run_binding_full_game,
)
from commander_gym.deck_package import ArtifactRef
from commander_gym.identity import Binding, Deck, Pilot
from commander_gym.orchestration import ArgentumOrchestrator
from commander_gym.pilot import ArgentumActionChoice


class DummyPilot:
    name = "dummy"
    version = "1"

    def choose(self, observation):
        return ArgentumActionChoice(action_id=0)


class BindingFullGameBackend:
    def __init__(self) -> None:
        self.ids = [f"p-{index}" for index in range(4)]
        self.names = [f"binding-{index}" for index in range(4)]
        self.index = 0
        self.envs: set[str] = set()

    def health(self) -> Mapping[str, Any]:
        return {"status": "ok"}

    def status(self) -> Mapping[str, Any]:
        return {
            "service": "argentum-gym-server",
            "schemaHash": "schema-binding-provenance",
            "buildRevision": "build-binding-provenance",
        }

    def schema_hash(self) -> Mapping[str, Any]:
        return {"schemaHash": "schema-binding-provenance"}

    def list_envs(self) -> Sequence[str]:
        return sorted(self.envs)

    def create_env(self, config: Mapping[str, Any]) -> Mapping[str, Any]:
        self.envs.add("env-binding-provenance")
        return {"envId": "env-binding-provenance"}

    def observe_env(
        self,
        env_id: str,
        *,
        reveal_all: bool | None = None,
        perspective_player_id: str | None = None,
    ) -> Mapping[str, Any]:
        return self._observation(perspective_player_id)

    def step_env(
        self,
        env_id: str,
        action_id: int,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        if action_id != 0:
            raise AssertionError("unexpected action")
        self.index += 1
        return self._observation(self.ids[0])

    def submit_decision(
        self,
        env_id: str,
        response: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        raise AssertionError("structured decisions are not used by this fixture")

    def dispose_envs(self, env_ids: Sequence[str]) -> None:
        self.envs.difference_update(env_ids)

    def _observation(self, perspective: str | None) -> Mapping[str, Any]:
        terminal = self.index >= 4
        agent = None if terminal else self.ids[self.index]
        return {
            "type": "Game",
            "schemaHash": "schema-binding-provenance",
            "stateDigest": f"state-{self.index}-{perspective}",
            "perspectivePlayerId": perspective,
            "agentToAct": agent,
            "players": [
                {"id": player_id, "name": name}
                for player_id, name in zip(self.ids, self.names)
            ],
            "pendingDecision": None,
            "legalActions": []
            if terminal
            else [
                {
                    "actionId": 0,
                    "semanticId": f"pass-{self.index}",
                    "kind": "PassPriority",
                    "description": "Pass priority",
                    "affordable": True,
                }
            ],
            "terminated": terminal,
            "winnerId": self.ids[0] if terminal else None,
        }


def resolver_for_four() -> BindingResolver:
    decks = []
    pilots = []
    bindings = []
    payloads = {}
    for index in range(4):
        deck_artifact = ArtifactRef(
            kind="decklist",
            artifact_id=f"deck-payload-{index}",
            version="v1",
            digest=f"sha256:{index:064x}",
        )
        deck = Deck(
            deck_id=f"deck-{index}",
            revision="r1",
            format_id="commander",
            deck_artifact=deck_artifact,
            format_metadata={"commander": f"Commander {index}"},
        )
        pilot = Pilot(pilot_id=f"pilot-{index}", revision="r1")
        binding = Binding(
            binding_id=f"binding-{index}",
            revision="r1",
            deck=deck.ref(),
            pilot=pilot.ref(),
        )
        payloads[deck_artifact.artifact_id] = {
            "schema_version": 1,
            "deck_id": deck.deck_id,
            "name": f"Synthetic Deck {index}",
            "commander": f"Commander {index}",
            "cards": {
                f"Commander {index}": 1,
                "Forest": 99,
            },
        }
        decks.append(deck)
        pilots.append(pilot)
        bindings.append(binding)

    return BindingResolver(
        bindings=bindings,
        decks=decks,
        pilots=pilots,
        deck_payload_loader=lambda ref: payloads[ref.artifact_id],
        pilot_factory=lambda pilot, binding: DummyPilot(),
    )


class BindingSessionTests(unittest.TestCase):
    def test_four_binding_ids_define_both_argentum_decks_and_pilots(self):
        plan = build_binding_launch_plan(
            resolver_for_four(),
            [f"binding-{index}" for index in range(4)],
            {
                "format": {"type": "com.wingedsheep.sdk.core.Format.Commander"},
                "skipMulligans": False,
                "revealAll": False,
            },
        )

        self.assertEqual(len(plan.seats), 4)
        self.assertEqual(len(plan.config["players"]), 4)
        for index, (seat, player, resolved) in enumerate(
            zip(plan.seats, plan.config["players"], plan.resolved)
        ):
            self.assertEqual(seat.player_name, f"binding-{index}")
            self.assertEqual(player["name"], seat.player_name)
            self.assertEqual(player["commanderCardName"], f"Commander {index}")
            self.assertEqual(player["deck"], {"type": "Explicit", "cards": {"Forest": 99}})
            self.assertEqual(seat.deck_id, f"deck-{index}")
            self.assertEqual(seat.deck_version, "r1")
            self.assertEqual(seat.pilot.name, "dummy")
            self.assertEqual(seat.binding, resolved.binding)
            self.assertEqual(
                seat.pilot_config["binding"],
                resolved.binding.to_dict(),
            )
            self.assertEqual(
                seat.pilot_config["pilot"],
                resolved.pilot.to_dict(),
            )

    def test_binding_full_game_keeps_run_and_decision_provenance_aligned(self):
        resolver = resolver_for_four()
        binding_ids = [f"binding-{index}" for index in range(4)]
        expected_bindings = [resolver.resolve(binding_id).binding for binding_id in binding_ids]

        result = run_binding_full_game(
            ArgentumOrchestrator(BindingFullGameBackend()),
            resolver,
            binding_ids,
            {
                "format": {"type": "com.wingedsheep.sdk.core.Format.Commander"},
                "skipMulligans": False,
                "revealAll": False,
            },
            run_id="binding-full-game-provenance",
            max_choices=8,
        )

        self.assertEqual(
            [participant.binding for participant in result.run.participants],
            expected_bindings,
        )
        self.assertEqual(len(result.decisions), 4)
        for decision in result.decisions:
            self.assertEqual(
                decision.binding,
                result.run.participants[decision.seat].binding,
            )

    def test_rejects_independent_player_configuration(self):
        with self.assertRaisesRegex(BindingLaunchError, "must not contain players"):
            build_binding_launch_plan(
                resolver_for_four(),
                [f"binding-{index}" for index in range(4)],
                {"players": []},
            )

    def test_unknown_binding_fails_closed(self):
        with self.assertRaisesRegex(BindingLaunchError, "unknown Binding"):
            build_binding_launch_plan(
                resolver_for_four(),
                ["binding-0", "binding-1", "binding-2", "missing"],
                {},
            )

    def test_requires_exactly_four_binding_ids(self):
        with self.assertRaisesRegex(BindingLaunchError, "exactly four"):
            build_binding_launch_plan(
                resolver_for_four(),
                ["binding-0"],
                {},
            )

    def test_format_specific_factory_can_consume_same_resolved_binding(self):
        def custom_player(resolved, player_name):
            return {
                "name": player_name,
                "deck": {
                    "type": "External",
                    "artifact": resolved.exact_deck_payload["deck_id"],
                },
            }

        plan = build_binding_launch_plan(
            resolver_for_four(),
            [f"binding-{index}" for index in range(4)],
            {},
            player_config_factory=custom_player,
        )
        self.assertEqual(
            plan.config["players"][0]["deck"],
            {"type": "External", "artifact": "deck-0"},
        )


if __name__ == "__main__":
    unittest.main()
