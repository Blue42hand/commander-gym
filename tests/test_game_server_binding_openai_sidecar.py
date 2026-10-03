from __future__ import annotations

import json
import hashlib
import socket
import tempfile
import unittest
from pathlib import Path

from commander_gym.deck_package import ArtifactRef
from commander_gym.game_server_binding_openai_sidecar import (
    BUILTIN_ALL_UNAFFORDABLE_PASS_COMPONENT_REF,
    BUILTIN_DELEGATED_AUTOPASS_COMPONENT_REF,
    BUILTIN_FORGE_CONDITIONAL_WAIT_COMPONENT_REF,
    BUILTIN_FORGE_CONDITIONAL_WAIT_V3_COMPONENT_REF,
    BUILTIN_FORGE_CONDITIONAL_WAIT_COMPACT_COMPONENT_REF,
    BUILTIN_FORCED_PARAMETERLESS_COMPONENT_REF,
    BUILTIN_NATIVE_NO_CHOICE_COMPONENT_REF,
    BUILTIN_NATIVE_UNAFFORDABLE_PASS_COMPONENT_REF,
    BUILTIN_STANDING_MANA_ONLY_PASS_COMPONENT_REF,
    BUILTIN_OPENAI_RESPONSES_COMPONENT_REF,
    BindingOpenAIGameServerConfig,
    OpenAIBindingPilotComponentResolver,
    binding_openai_game_server_config_from_environment,
    build_binding_openai_game_server_sidecar,
)
from commander_gym.game_server_openai_sidecar import (
    OpenAIGameServerSidecarConfig,
    OpenAIGameServerSidecarConfigurationError,
)
from commander_gym.game_server_sidecar import UnknownProfileError
from commander_gym.identity import Binding, Deck, Pilot
from commander_gym.pilot_composition import PilotSubsystemSpec
from commander_gym.delegated_autopass import DelegatedAutopassPilot
from commander_gym.pilot_routing import AllUnaffordablePassHandler
from commander_gym.openai_run_budget import OpenAIRunBudget


class FakeClient:
    class Responses:
        def create(self, **kwargs):  # pragma: no cover - forced-pass smoke avoids a model wake.
            raise AssertionError("synthetic catalog smoke must not call the model")

    def __init__(self):
        self.responses = self.Responses()


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def synthetic_catalog(
    root: Path,
    *,
    active_binding: str = "seat-a",
    pilot: Pilot | None = None,
) -> Path:
    payload = {
        "commander": "Synthetic Commander",
        "cards": {"Synthetic Commander": 1, "Forest": 99},
    }
    deck_artifact = ArtifactRef(
        kind="decklist",
        artifact_id="synthetic-decklist",
        version="r1",
        digest="sha256:" + hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode("utf-8")
        ).hexdigest(),
    )
    deck = Deck(
        deck_id="synthetic-deck",
        revision="r1",
        format_id="commander",
        deck_artifact=deck_artifact,
        format_metadata={"commander": "Synthetic Commander"},
    )
    if pilot is None:
        pilot = Pilot(
            pilot_id="synthetic-openai-pilot",
            revision="r1",
            deterministic_policy=BUILTIN_FORCED_PARAMETERLESS_COMPONENT_REF,
            escalation_provider=BUILTIN_OPENAI_RESPONSES_COMPONENT_REF,
        )
    binding = Binding(
        binding_id="seat-a",
        revision="r1",
        deck=deck.ref(),
        pilot=pilot.ref(),
        metadata={
            "display": {
                "name": "Synthetic Binding",
                "deck_name": "Synthetic Deck",
            }
        },
    )

    write_json(root / "bindings" / "seat-a.json", binding.to_dict())
    write_json(root / "decks" / "deck.json", deck.to_dict())
    write_json(root / "decks" / "payload.json", payload)
    write_json(root / "pilots" / "pilot.json", pilot.to_dict())
    catalog = root / "instance" / "bindings.json"
    write_json(
        catalog,
        {
            "schema_version": 1,
            "bindings": ["bindings/seat-a.json"],
            "decks": [
                {
                    "manifest": "decks/deck.json",
                    "payload": "decks/payload.json",
                }
            ],
            "pilots": ["pilots/pilot.json"],
            "deck_knowledge": [],
            "active_bindings": [active_binding],
        },
    )
    return catalog


class BindingOpenAIGameServerSidecarTests(unittest.TestCase):
    def test_all_unaffordable_pass_requires_exact_new_component_ref(self):
        config = OpenAIGameServerSidecarConfig(
            token="sidecar-secret", api_key="sk-test-secret", port=12345,
        )
        resolver = OpenAIBindingPilotComponentResolver(config=config, client=FakeClient())
        new = resolver.resolve(PilotSubsystemSpec(
            role="deterministic", ordinal=0,
            ref=BUILTIN_ALL_UNAFFORDABLE_PASS_COMPONENT_REF,
        ))
        self.assertIsInstance(new.handler, AllUnaffordablePassHandler)
        old = resolver.resolve(PilotSubsystemSpec(
            role="deterministic", ordinal=0,
            ref=BUILTIN_NATIVE_UNAFFORDABLE_PASS_COMPONENT_REF,
        ))
        self.assertNotIsInstance(old.handler, AllUnaffordablePassHandler)

    def test_standing_mana_pass_requires_new_exact_opt_in_pilot_ref(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pilot = Pilot(
                pilot_id="synthetic-standing-pass", revision="r1",
                deterministic_policy=BUILTIN_STANDING_MANA_ONLY_PASS_COMPONENT_REF,
                escalation_provider=BUILTIN_OPENAI_RESPONSES_COMPONENT_REF,
            )
            catalog = synthetic_catalog(root, pilot=pilot)
            config = BindingOpenAIGameServerConfig(
                sidecar=OpenAIGameServerSidecarConfig(
                    token="sidecar-secret", api_key="sk-test-secret", port=free_port(),
                ), catalog_path=catalog, instance_root=root,
            )
            server = build_binding_openai_game_server_sidecar(config, client=FakeClient())
            try:
                seat = server.resolve_seat("ai-one", "seat-a")
                result = seat.choose_action(
                    {"viewingPlayerId": "ai-one"},
                    [
                        {"kind": "PassPriority", "actionType": "PassPriority",
                         "semanticId": "argentum-action-v1:pass",
                         "affordable": True, "isManaAbility": False,
                         "action": {"type": "PassPriority", "playerId": "ai-one"}},
                        {"kind": "ActivateAbility", "actionType": "ActivateAbility",
                         "semanticId": "argentum-action-v1:mana",
                         "affordable": False, "isManaAbility": True,
                         "action": {"type": "ActivateAbility", "playerId": "ai-one"}},
                    ], None,
                )
                self.assertEqual(result.action["type"], "PassPriority")
                self.assertEqual(result.metadata["routing"]["handledBy"]["component"]["artifactId"],
                                 "standing-mana-only-pass")
                self.assertEqual(result.metadata["pilot"]["revision"], "r1")
            finally:
                server.server_close()

    def test_delegated_autopass_requires_exact_opt_in_provider_ref(self):
        config = OpenAIGameServerSidecarConfig(
            token="sidecar-secret", api_key="sk-test-secret", port=12345,
        )
        resolver = OpenAIBindingPilotComponentResolver(config=config, client=FakeClient())
        subsystem = resolver.resolve(PilotSubsystemSpec(
            role="frontier_escalation", ordinal=0,
            ref=BUILTIN_DELEGATED_AUTOPASS_COMPONENT_REF,
        ))
        self.assertIsInstance(subsystem.player, DelegatedAutopassPilot)
        self.assertTrue(subsystem.player.strategic_pilot.allow_priority_delegation)
        standard = resolver.resolve(PilotSubsystemSpec(
            role="frontier_escalation", ordinal=0,
            ref=BUILTIN_OPENAI_RESPONSES_COMPONENT_REF,
        ))
        self.assertFalse(standard.player.allow_priority_delegation)

    def test_forge_wait_requires_new_exact_provider_ref(self):
        config = OpenAIGameServerSidecarConfig(
            token="sidecar-secret", api_key="sk-test-secret", port=12345,
        )
        resolver = OpenAIBindingPilotComponentResolver(config=config, client=FakeClient())
        subsystem = resolver.resolve(PilotSubsystemSpec(
            role="frontier_escalation", ordinal=0,
            ref=BUILTIN_FORGE_CONDITIONAL_WAIT_COMPONENT_REF,
        ))
        self.assertIsInstance(subsystem.player, DelegatedAutopassPilot)
        self.assertTrue(subsystem.player.allow_named_deferrals)
        self.assertTrue(subsystem.player.strategic_pilot.allow_named_deferrals)
        self.assertEqual(subsystem.player.version, "2")
        mechanical = resolver.resolve(PilotSubsystemSpec(
            role="deterministic", ordinal=0,
            ref=BUILTIN_NATIVE_UNAFFORDABLE_PASS_COMPONENT_REF,
        ))
        self.assertTrue(mechanical.handler.ignore_unaffordable_abilities)

    def test_forge_wait_v3_has_separate_identity_and_nonempty_protocol(self):
        config = OpenAIGameServerSidecarConfig(
            token="sidecar-secret", api_key="sk-test-secret", port=12345,
        )
        resolver = OpenAIBindingPilotComponentResolver(config=config, client=FakeClient())
        v2 = resolver.resolve(PilotSubsystemSpec(
            role="frontier_escalation", ordinal=0,
            ref=BUILTIN_FORGE_CONDITIONAL_WAIT_COMPONENT_REF,
        ))
        v3 = resolver.resolve(PilotSubsystemSpec(
            role="frontier_escalation", ordinal=0,
            ref=BUILTIN_FORGE_CONDITIONAL_WAIT_V3_COMPONENT_REF,
        ))
        self.assertEqual(v2.player.version, "2")
        self.assertFalse(v2.player.strategic_pilot.require_nonempty_named_deferrals)
        self.assertEqual(v3.player.version, "3")
        self.assertTrue(v3.player.strategic_pilot.require_nonempty_named_deferrals)

        compact = resolver.resolve(PilotSubsystemSpec(
            role="frontier_escalation", ordinal=0,
            ref=BUILTIN_FORGE_CONDITIONAL_WAIT_COMPACT_COMPONENT_REF,
        ))
        self.assertTrue(compact.player.strategic_pilot.compact_model_observation)
        self.assertFalse(v3.player.strategic_pilot.compact_model_observation)

    def test_versioned_native_no_choice_component_avoids_model_for_empty_combat(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pilot = Pilot(
                pilot_id="synthetic-optimized-pilot",
                revision="r2",
                deterministic_policy=BUILTIN_NATIVE_NO_CHOICE_COMPONENT_REF,
                escalation_provider=BUILTIN_OPENAI_RESPONSES_COMPONENT_REF,
            )
            catalog = synthetic_catalog(root, pilot=pilot)
            config = BindingOpenAIGameServerConfig(
                sidecar=OpenAIGameServerSidecarConfig(
                    token="sidecar-secret", api_key="sk-test-secret", port=free_port(),
                ),
                catalog_path=catalog,
                instance_root=root,
            )
            server = build_binding_openai_game_server_sidecar(config, client=FakeClient())
            try:
                seat = server.resolve_seat("ai-one", "seat-a")
                result = seat.choose_action(
                    {"viewingPlayerId": "ai-one"},
                    [
                        {"kind": "DeclareAttackers", "actionType": "DeclareAttackers",
                         "validAttackers": [],
                         "action": {"type": "DeclareAttackers", "playerId": "ai-one",
                                    "attackers": {}}},
                    ],
                    None,
                )
                self.assertEqual(result.action["type"], "DeclareAttackers")
                self.assertEqual(result.metadata["routing"]["handledBy"]["component"]["artifactId"], "native-no-choice")
                self.assertEqual(result.metadata["pilot"]["revision"], "r2")
            finally:
                server.server_close()

    def test_environment_accepts_instance_supplied_catalog_and_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = synthetic_catalog(root)
            config = binding_openai_game_server_config_from_environment(
                {
                    "COMMANDER_GYM_SIDECAR_TOKEN": "sidecar-secret",
                    "OPENAI_API_KEY": "sk-test-secret",
                    "COMMANDER_GYM_BINDING_CATALOG": str(catalog),
                    "COMMANDER_GYM_INSTANCE_ROOT": str(root),
                }
            )
            self.assertEqual(config.catalog_path, catalog.resolve())
            self.assertEqual(config.instance_root, root.resolve())

    def test_above_default_budget_requires_explicit_ceiling_and_request_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = synthetic_catalog(root)
            ledger = root / "budget.json"
            OpenAIRunBudget(ledger, 5).snapshot()
            base = {
                "COMMANDER_GYM_SIDECAR_TOKEN": "sidecar-secret",
                "OPENAI_API_KEY": "sk-test-secret",
                "COMMANDER_GYM_BINDING_CATALOG": str(catalog),
                "COMMANDER_GYM_INSTANCE_ROOT": str(root),
                "COMMANDER_GYM_OPENAI_BUDGET_LEDGER": str(ledger),
                "COMMANDER_GYM_OPENAI_BUDGET_CAP_USD": "6",
            }
            with self.assertRaisesRegex(OpenAIGameServerSidecarConfigurationError,
                                        "absolute request limit"):
                binding_openai_game_server_config_from_environment(base)
            with self.assertRaises(ValueError):
                binding_openai_game_server_config_from_environment({
                    **base, "COMMANDER_GYM_OPENAI_BUDGET_MAX_REQUESTS": "610",
                })
            configured = binding_openai_game_server_config_from_environment({
                **base, "COMMANDER_GYM_OPENAI_BUDGET_AUTHORIZED_MAX_USD": "6",
                "COMMANDER_GYM_OPENAI_BUDGET_MAX_REQUESTS": "610",
            })
            self.assertEqual(configured.budget.cap_usd, 6)
            self.assertEqual(configured.budget.max_requests, 610)
            self.assertEqual(json.loads(ledger.read_text())["capUsd"], 5)

    def test_binding_catalog_executes_exact_declared_pilot_graph_without_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = synthetic_catalog(root)
            config = BindingOpenAIGameServerConfig(
                sidecar=OpenAIGameServerSidecarConfig(
                    token="sidecar-secret",
                    api_key="sk-test-secret",
                    port=free_port(),
                ),
                catalog_path=catalog,
                instance_root=root,
            )
            server = build_binding_openai_game_server_sidecar(
                config,
                client=FakeClient(),
            )
            try:
                self.assertEqual(len(server.controller_profiles), 1)
                profile = server.controller_profiles[0]
                self.assertEqual(profile["id"], "seat-a")
                self.assertEqual(profile["deck"]["commander"], "Synthetic Commander")
                self.assertEqual(profile["deck"]["cards"], {"Forest": 99})

                seat = server.resolve_seat("ai-one", "seat-a")
                result = seat.choose_action(
                    {"viewingPlayerId": "ai-one"},
                    [
                        {
                            "kind": "PassPriority",
                            "actionType": "PassPriority",
                            "semanticId": "argentum-action-v1:pass",
                            "description": "Pass priority",
                            "affordable": True,
                            "action": {
                                "type": "PassPriority",
                                "playerId": "ai-one",
                            },
                        }
                    ],
                    None,
                    (),
                )
                self.assertEqual(result.action["type"], "PassPriority")
                self.assertEqual(result.metadata["binding"]["artifact_id"], "seat-a")
                self.assertEqual(
                    result.metadata["pilot"]["artifact_id"],
                    "synthetic-openai-pilot",
                )
                routing = result.metadata["routing"]
                self.assertEqual(routing["path"], "composed")
                self.assertEqual(routing["pilotId"], "synthetic-openai-pilot")
                self.assertEqual(routing["pilotRevision"], "r1")
                self.assertEqual(routing["handledBy"]["role"], "deterministic")
                self.assertEqual(
                    routing["handledBy"]["component"]["artifactId"],
                    BUILTIN_FORCED_PARAMETERLESS_COMPONENT_REF.artifact_id,
                )
                self.assertEqual(
                    routing["attempts"][0]["status"],
                    "handled",
                )

                with self.assertRaises(UnknownProfileError):
                    server.resolve_seat("ai-one", "stale-binding")
            finally:
                server.server_close()

    def test_stale_declared_component_fails_instead_of_using_process_openai_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stale_pilot = Pilot(
                pilot_id="synthetic-openai-pilot",
                revision="r1",
                deterministic_policy=BUILTIN_FORCED_PARAMETERLESS_COMPONENT_REF,
                escalation_provider=ArtifactRef(
                    kind=BUILTIN_OPENAI_RESPONSES_COMPONENT_REF.kind,
                    artifact_id=BUILTIN_OPENAI_RESPONSES_COMPONENT_REF.artifact_id,
                    version=BUILTIN_OPENAI_RESPONSES_COMPONENT_REF.version,
                    digest="sha256:stale-provider-adapter",
                ),
            )
            catalog = synthetic_catalog(root, pilot=stale_pilot)
            config = BindingOpenAIGameServerConfig(
                sidecar=OpenAIGameServerSidecarConfig(
                    token="sidecar-secret",
                    api_key="sk-test-secret",
                    port=free_port(),
                ),
                catalog_path=catalog,
                instance_root=root,
            )

            with self.assertRaisesRegex(
                OpenAIGameServerSidecarConfigurationError,
                "missing exact Binding Pilot component",
            ):
                build_binding_openai_game_server_sidecar(config, client=FakeClient())

    def test_componentless_pilot_fails_instead_of_constructing_generic_openai_pilot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = synthetic_catalog(
                root,
                pilot=Pilot(pilot_id="synthetic-openai-pilot", revision="r1"),
            )
            config = BindingOpenAIGameServerConfig(
                sidecar=OpenAIGameServerSidecarConfig(
                    token="sidecar-secret",
                    api_key="sk-test-secret",
                    port=free_port(),
                ),
                catalog_path=catalog,
                instance_root=root,
            )

            with self.assertRaisesRegex(
                OpenAIGameServerSidecarConfigurationError,
                "contains no executable decision subsystem",
            ):
                build_binding_openai_game_server_sidecar(config, client=FakeClient())

    def test_unresolvable_active_binding_fails_at_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = synthetic_catalog(root, active_binding="missing-binding")
            config = BindingOpenAIGameServerConfig(
                sidecar=OpenAIGameServerSidecarConfig(
                    token="sidecar-secret",
                    api_key="sk-test-secret",
                    port=free_port(),
                ),
                catalog_path=catalog,
                instance_root=root,
            )
            with self.assertRaisesRegex(
                OpenAIGameServerSidecarConfigurationError,
                "missing-binding",
            ):
                build_binding_openai_game_server_sidecar(config, client=FakeClient())

    def test_catalog_path_cannot_escape_instance_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            elsewhere = root.parent / "outside-bindings.json"
            try:
                elsewhere.write_text("{}", encoding="utf-8")
                with self.assertRaisesRegex(
                    OpenAIGameServerSidecarConfigurationError,
                    "below COMMANDER_GYM_INSTANCE_ROOT",
                ):
                    binding_openai_game_server_config_from_environment(
                        {
                            "COMMANDER_GYM_SIDECAR_TOKEN": "sidecar-secret",
                            "OPENAI_API_KEY": "sk-test-secret",
                            "COMMANDER_GYM_BINDING_CATALOG": str(elsewhere),
                            "COMMANDER_GYM_INSTANCE_ROOT": str(root),
                        }
                    )
            finally:
                elsewhere.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
