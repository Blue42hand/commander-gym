from dataclasses import replace
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

from commander_gym.game_server_binding_lmstudio_sidecar import (
    LocalBindingConfig, build_binding_lmstudio_sidecar, local_component_ref,
)
from commander_gym.game_server_binding_openai_sidecar import BUILTIN_OPENAI_RESPONSES_COMPONENT_REF
from commander_gym.game_server_sidecar import GameServerSidecarConfig, UnknownProfileError
from commander_gym.identity import Pilot
from commander_gym.lmstudio_pilot import LMStudioPilot, LMStudioPilotError
from commander_gym.local_callback_context import local_callback_context
from commander_gym.pilot import PilotContractError
from test_game_server_binding_openai_sidecar import synthetic_catalog, free_port
from test_lmstudio import config, FakeTransport, action_observation


class LocalBindingTests(unittest.TestCase):
    def build(self, root, *, pilot=None, inference=None, transport=None):
        inference = inference or config()
        pilot = pilot or Pilot(pilot_id="qwen-fixture", revision="1",
                               generalist_base=local_component_ref(inference))
        catalog = synthetic_catalog(root, pilot=pilot)
        return build_binding_lmstudio_sidecar(LocalBindingConfig(
            GameServerSidecarConfig("local-fixture", port=free_port()), inference, catalog, root,
        ), transport=transport or FakeTransport())

    def test_explicit_local_binding_routes_independent_seats_without_openai(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            "commander_gym.game_server_binding_openai_sidecar._default_openai_client",
            side_effect=AssertionError("local service must never construct a cloud client"),
        ):
            server = self.build(Path(directory))
            try:
                self.assertEqual(server.controller_profiles[0]["id"], "seat-a")
                first = server.resolve_seat("a", "seat-a", "game1")
                second = server.resolve_seat("b", "seat-a", "game1")
                self.assertIsNot(first, second)
                with self.assertRaises(UnknownProfileError):
                    server.resolve_seat("c", "missing", "game1")
                with self.assertRaises(KeyError):
                    server.resolve_seat("c", None, "game1")
                # The exact canonical Pilot is selected before receiving a masked callback.
                choice = first._pilot.choose(action_observation())
                self.assertEqual(choice.metadata["provider"], "lmstudio")
                self.assertEqual(choice.metadata["binding"]["artifact_id"], "seat-a")
                self.assertEqual(choice.metadata["routing"]["handledBy"]["role"], "local_generalist")
            finally:
                server.server_close()

    def test_cloud_and_stale_model_config_fail_at_startup(self):
        bad = [Pilot(pilot_id="cloud", revision="1", escalation_provider=BUILTIN_OPENAI_RESPONSES_COMPONENT_REF),
               Pilot(pilot_id="fallback", revision="1", generalist_base=local_component_ref(config()),
                     escalation_provider=BUILTIN_OPENAI_RESPONSES_COMPONENT_REF),
               Pilot(pilot_id="wrong-model", revision="1", generalist_base=local_component_ref(config(model="other")))]
        for pilot in bad:
            with self.subTest(pilot=pilot.pilot_id), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises((PilotContractError, ValueError)):
                    self.build(Path(directory), pilot=pilot)

    def test_every_configuration_change_changes_component_identity(self):
        base = config()
        for changed in (replace(base, model="other"), replace(base, endpoint="http://127.0.0.2:12345"),
                        replace(base, timeout_seconds=20), replace(base, max_attempts=1),
                        replace(base, max_output_tokens=512)):
            self.assertNotEqual(local_component_ref(base), local_component_ref(changed))

    def test_native_disconnect_and_shutdown_cancel_before_dispatch(self):
        connection, peer = socket.socketpair()
        stopping = threading.Event()
        transport = FakeTransport()
        pilot = LMStudioPilot(config(), transport=transport)
        try:
            with local_callback_context(connection, stopping):
                peer.close()
                with self.assertRaisesRegex(LMStudioPilotError, "cancelled"):
                    pilot.choose(action_observation())
            self.assertEqual(transport.calls, [])
            # Cancellation context is request-local; another seat still completes.
            self.assertEqual(pilot.choose(action_observation()).action_id, 2)
            stopping.set()
            with local_callback_context(connection, stopping):
                with self.assertRaisesRegex(LMStudioPilotError, "cancelled"):
                    pilot.choose(action_observation())
        finally:
            connection.close()
            peer.close()


if __name__ == "__main__":
    unittest.main()
