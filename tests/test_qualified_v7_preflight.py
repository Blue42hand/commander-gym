import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from commander_gym import qualified_v7_preflight as qualified
from commander_gym.game_server_binding_openai_sidecar import (
    BUILTIN_FORGE_DECLARATIVE_CONTINUATION_COMPONENT_REF,
    OpenAIBindingPilotComponentResolver,
)
from commander_gym.identity import Pilot
from commander_gym.pilot_composition import PilotSubsystemSpec


class QualifiedV7PreflightTests(unittest.TestCase):
    def test_complete_offline_verification_and_staging_on_synthetic_catalog(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "source"
            run = Path(temporary) / "run"
            root.mkdir()
            run.mkdir()
            roster_path = Path("rosters/fixture.json")
            pilot_path = Path("pilots/fixture.json")
            binding_ids = ("krenko-fixture", "talrand-fixture", "sythis-fixture", "lathril-fixture")
            binding_fingerprints = {name: f"fixture-{name}" for name in binding_ids}
            pilot = Pilot(
                pilot_id="fixture", revision="2026-10-04.2",
                escalation_provider=BUILTIN_FORGE_DECLARATIVE_CONTINUATION_COMPONENT_REF,
            )
            paths = {roster_path, pilot_path}

            def write(relative, payload):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(payload, sort_keys=True))

            write(roster_path, {
                "roster_id": "fixture", "roster_revision": "2026-10-04.2",
                "active_bindings": list(binding_ids),
            })
            write(pilot_path, pilot.to_dict())
            for name, fingerprint in binding_fingerprints.items():
                relative = Path("bindings") / f"{name}.json"
                paths.add(relative)
                write(relative, {"fingerprint": fingerprint,
                                 "pilot": {"fingerprint": pilot.fingerprint(),
                                           "revision": "2026-10-04.2"}})
            with patch.object(qualified, "_closure_paths", return_value=paths):
                closure_digest = qualified._closure(root, {})
            with patch.object(qualified, "ROSTER_PATH", roster_path), patch.object(
                qualified, "PILOT_PATH", pilot_path,
            ), patch.object(qualified, "BINDING_FINGERPRINTS", binding_fingerprints), patch.object(
                qualified, "PILOT_FINGERPRINT", pilot.fingerprint(),
            ), patch.object(qualified, "SELECTED_PROFILES", binding_ids[:2]), patch.object(
                qualified, "ROSTER_SHA256", qualified._sha((root / roster_path).read_bytes()),
            ), patch.object(qualified, "_closure_paths", return_value=paths), patch.object(
                qualified, "CLOSURE_SHA256", closure_digest,
            ), patch.object(qualified, "load_binding_catalog", return_value=SimpleNamespace(
                binding_ids=binding_ids,
            )):
                staged, catalog, receipt = qualified.stage_qualified_v7_catalog(
                    root, root / roster_path, run, *binding_ids[:2], 2,
                )
                self.assertEqual(receipt["maxAttempts"], 2)
                self.assertEqual(receipt["configSha256"], qualified.CONFIG_SHA256)
                self.assertEqual(catalog, staged / roster_path)
                self.assertEqual(set(path.relative_to(staged) for path in staged.rglob("*.json")), paths)
                self.assertEqual((staged / pilot_path).stat().st_mode & 0o777, 0o600)
                (staged / pilot_path).write_text("{}")
                with self.assertRaisesRegex(qualified.QualifiedV7PreflightError,
                                            "catalog closure digest changed"):
                    qualified.verify_qualified_v7_catalog(
                        staged, catalog, *binding_ids[:2], 2,
                    )

    def test_actual_max_attempts_three_rejects_two_attempt_qualification(self):
        spec = PilotSubsystemSpec(
            role="frontier_escalation", ordinal=0,
            ref=BUILTIN_FORGE_DECLARATIVE_CONTINUATION_COMPONENT_REF,
        )
        for attempts in (2, 3):
            resolver = OpenAIBindingPilotComponentResolver(
                config=SimpleNamespace(model="gpt-6-luna", max_attempts=attempts),
                client=SimpleNamespace(responses=qualified._FakeResponses()),
            )
            runtime = resolver.resolve(spec).player
            strategic = runtime.strategic_pilot
            self.assertEqual(strategic.max_attempts, attempts)
            if attempts == 2:
                self.assertEqual(qualified._verified_runtime_config_digest(spec, runtime, strategic),
                                 qualified.CONFIG_SHA256)
            else:
                with self.assertRaisesRegex(qualified.QualifiedV7PreflightError,
                                            "runtime config digest changed"):
                    qualified._verified_runtime_config_digest(spec, runtime, strategic)

    def test_old_catalog_path_fails_before_any_provider_or_file_read(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(qualified.QualifiedV7PreflightError, "exact roster path"):
                qualified.verify_qualified_v7_catalog(
                    root, root / "rosters/foundation.json", "foundation-a", "foundation-b", 2,
                )

    def test_closure_hash_uses_exact_relative_paths_and_file_bytes(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "rosters").mkdir()
            (root / "bindings").mkdir()
            (root / "rosters/test.json").write_text("{}")
            (root / "bindings/a.json").write_text("first")
            with patch.object(qualified, "ROSTER_PATH", Path("rosters/test.json")), patch.object(
                qualified, "_closure_paths", return_value={
                    Path("rosters/test.json"), Path("bindings/a.json"),
                },
            ):
                first = qualified._closure(root, {})
                (root / "bindings/a.json").write_text("second")
                self.assertNotEqual(first, qualified._closure(root, {}))
                (root / "bindings/a.json").write_text("first")
                self.assertEqual(first, qualified._closure(root, {}))

    def test_stage_copies_only_reviewed_closure_into_private_run(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "source"
            run = Path(temporary) / "run"
            (root / "rosters").mkdir(parents=True)
            (root / "bindings").mkdir()
            run.mkdir()
            roster = root / "rosters/test.json"
            roster.write_text(json.dumps({"bindings": ["bindings/a.json"]}))
            (root / "bindings/a.json").write_text("reviewed")
            (root / "unlisted-secret").write_text("do not copy")
            receipt = {"catalogClosureSha256": "approved"}
            with patch.object(qualified, "ROSTER_PATH", Path("rosters/test.json")), patch.object(
                qualified, "_closure_paths", return_value={
                    Path("rosters/test.json"), Path("bindings/a.json"),
                },
            ), patch.object(qualified, "verify_qualified_v7_catalog", return_value=receipt) as verify:
                staged, catalog, result = qualified.stage_qualified_v7_catalog(
                    root, roster, run, "a", "b", 2,
                )
            self.assertEqual(result, receipt)
            self.assertEqual(verify.call_count, 2)
            self.assertEqual(verify.call_args_list[0].args[-1], 2)
            self.assertEqual(verify.call_args_list[1].args[-1], 2)
            self.assertEqual(catalog, staged / "rosters/test.json")
            self.assertEqual((staged / "bindings/a.json").read_text(), "reviewed")
            self.assertFalse((staged / "unlisted-secret").exists())
            self.assertEqual((staged / "bindings/a.json").stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
