"""Keyless qualification of manual-service configuration and recovery callbacks."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import threading
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from commander_gym.bounded_provider_process import BoundedProcessClient
from commander_gym.game_server_binding_openai_sidecar import (
    BindingOpenAIGameServerConfig, BUILTIN_FORGE_BOUNDED_RECOVERY_COMPONENT_REF,
    BUILTIN_NATIVE_NO_CHOICE_COMPONENT_REF, binding_openai_game_server_config_from_environment,
    build_binding_openai_game_server_sidecar,
)
from commander_gym.game_server_openai_sidecar import OpenAIGameServerSidecarConfigurationError
from commander_gym.identity import Pilot
from commander_gym.openai_responses_pilot import OpenAIResponsesPilot, OpenAIResponsesPilotError
from test_game_server_binding_openai_sidecar import FakeClient, free_port, synthetic_catalog
from test_openai_responses_pilot import (
    FakeClient as ResponseClient, FakeHttpError, FakeResponse, SequenceResponses, action_observation,
)


class ManualLunaServiceTests(unittest.TestCase):
    def environment(self, root):
        return {
            "OPENAI_API_KEY": "offline-placeholder", "COMMANDER_GYM_SIDECAR_TOKEN": "offline-token",
            "COMMANDER_GYM_SIDECAR_PORT": "8083", "COMMANDER_GYM_MANUAL_UNCAPPED": "true",
            "COMMANDER_GYM_BINDING_CATALOG": str(root / "instance/catalog.json"),
            "COMMANDER_GYM_INSTANCE_ROOT": str(root),
        }

    def test_manual_configuration_has_no_spending_or_session_cap(self):
        with TemporaryDirectory() as directory:
            config = binding_openai_game_server_config_from_environment(self.environment(Path(directory)))
            self.assertTrue(config.manual_uncapped)
            self.assertIsNone(config.budget)
            self.assertIsNone(config.prefix_guard)
            self.assertIsNone(config.human_gui_guard)
            self.assertEqual(config.sidecar.bind_host, "127.0.0.1")
            self.assertEqual(config.sidecar.model, "gpt-6-luna")

    def test_experimental_bounds_cannot_leak_into_manual_service(self):
        with TemporaryDirectory() as directory:
            for name in (
                "COMMANDER_GYM_OPENAI_BUDGET_LEDGER", "COMMANDER_GYM_OPENAI_BUDGET_CAP_USD",
                "COMMANDER_GYM_OPENAI_SESSION_MAX_REQUESTS", "COMMANDER_GYM_PREFIX_TURN_LIMIT",
                "COMMANDER_GYM_HUMAN_GUI_DEADLINE_UNIX",
            ):
                with self.subTest(name=name), self.assertRaisesRegex(
                    OpenAIGameServerSidecarConfigurationError, "must not inherit"
                ):
                    binding_openai_game_server_config_from_environment(
                        {**self.environment(Path(directory)), name: "1"}
                    )

    def test_manual_service_preserves_loopback_only_policy_transport(self):
        with TemporaryDirectory() as directory, self.assertRaises(OpenAIGameServerSidecarConfigurationError):
            binding_openai_game_server_config_from_environment(
                {**self.environment(Path(directory)), "COMMANDER_GYM_SIDECAR_HOST": "0.0.0.0"}
            )

    def test_uncapped_authorization_cannot_select_another_model(self):
        with TemporaryDirectory() as directory, self.assertRaisesRegex(
            OpenAIGameServerSidecarConfigurationError, "requires gpt-6-luna"
        ):
            binding_openai_game_server_config_from_environment(
                {**self.environment(Path(directory)), "COMMANDER_GYM_OPENAI_MODEL": "another-model"}
            )

    def test_manual_recovery_uses_process_deadline_without_budget(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = synthetic_catalog(root, pilot=Pilot(
                pilot_id="synthetic-manual", revision="r1",
                deterministic_policy=BUILTIN_NATIVE_NO_CHOICE_COMPONENT_REF,
                escalation_provider=BUILTIN_FORGE_BOUNDED_RECOVERY_COMPONENT_REF,
            ))
            config = binding_openai_game_server_config_from_environment({
                **self.environment(root), "COMMANDER_GYM_BINDING_CATALOG": str(catalog),
                "COMMANDER_GYM_SIDECAR_PORT": str(free_port()),
            })
            with patch("commander_gym.game_server_binding_openai_sidecar._default_openai_client",
                       return_value=FakeClient()) as factory:
                server = build_binding_openai_game_server_sidecar(config)
            try:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                for admission in ({}, {"manualHumanGame": False}, {"manualHumanGame": "true"}):
                    request = Request(f"http://127.0.0.1:{server.server_port}/v1/decide-mulligan",
                                      data=json.dumps({"playerId": "ai", "profileId": "seat-a",
                                                       "gameSessionId": "fixture", **admission}).encode(),
                                      headers={"Authorization": "Bearer offline-token", "Content-Type": "application/json"})
                    with self.assertRaises(HTTPError) as rejected:
                        urlopen(request, timeout=2)
                    self.assertEqual(rejected.exception.code, 422)
                    rejected.exception.close()
                factory.assert_called_once_with(config.sidecar, bounded=True)
                seat = server.resolve_seat("synthetic-seat", "seat-a")
                frontier = next(item for item in seat._pilot.delegate.subsystems
                                if item.spec.role == "frontier_escalation")
                pilot = frontier.implementation.player.strategic_pilot
                self.assertIsInstance(pilot.client, BoundedProcessClient)
                self.assertIsNone(pilot.budget)
                self.assertTrue(pilot.uncapped_manual_recovery)
            finally:
                server.shutdown()
                thread.join(timeout=2)
                server.server_close()

    def test_manual_transient_recovery_retains_attempt_receipts_and_deadline(self):
        client = ResponseClient()
        client.responses = SequenceResponses(FakeHttpError(520), FakeResponse(
            '{"channel":"action","semanticId":"argentum-action-v1:pass","params":{}}'
        ))
        choice = OpenAIResponsesPilot(client=client, model="offline-model", max_attempts=2,
                                      retry_transient_server_errors=True,
                                      uncapped_manual_recovery=True).choose(action_observation())
        self.assertEqual(len(client.responses.calls), 2)
        self.assertTrue(all(0 < call["timeout"] <= 90 for call in client.responses.calls))
        self.assertEqual(choice.metadata["modelIo"]["selectedAttempt"], 1)
        self.assertEqual(len(choice.metadata["modelIo"]["attempts"]), 2)

    def test_manual_nontransient_failure_does_not_retry_or_fallback(self):
        client = ResponseClient()
        client.responses = SequenceResponses(FakeHttpError(401))
        with self.assertRaisesRegex(OpenAIResponsesPilotError, "status=401"):
            OpenAIResponsesPilot(client=client, model="offline-model", max_attempts=2,
                                 retry_transient_server_errors=True,
                                 uncapped_manual_recovery=True).choose(action_observation())
        self.assertEqual(len(client.responses.calls), 1)
