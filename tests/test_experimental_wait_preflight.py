from contextlib import ExitStack
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from commander_gym import experimental_wait_preflight as experiment
from commander_gym import qualified_v7_preflight as v7
from commander_gym.game_server_binding_openai_sidecar import BUILTIN_FORGE_EXPLICIT_WAIT_COMPONENT_REF
from commander_gym.identity import Binding, Pilot
from tests.test_game_server_binding_openai_sidecar import synthetic_catalog, write_json


class ExperimentalWaitPreflightTests(unittest.TestCase):
    def fixture(self, root, stack):
        catalog = synthetic_catalog(root)
        roster = json.loads(catalog.read_text())
        original = Binding.from_dict(json.loads((root / "bindings/seat-a.json").read_text()))
        old_pilot = Pilot.from_dict(json.loads((root / "pilots/pilot.json").read_text()))
        write_json(root / v7.PILOT_PATH, old_pilot.to_dict())
        new_pilot = replace(old_pilot, revision="2026-10-07.1", escalation_provider=BUILTIN_FORGE_EXPLICIT_WAIT_COMPONENT_REF)
        write_json(root / experiment.PILOT_PATH, new_pilot.to_dict())
        roster["pilots"] = [str(v7.PILOT_PATH)]
        roster["bindings"] = []
        for old_id, new_id in zip(v7.BINDING_FINGERPRINTS, experiment.PROFILES):
            old_binding = replace(original, binding_id=old_id, pilot=old_pilot.ref())
            old_path = f"bindings/{old_id}.json"
            write_json(root / old_path, old_binding.to_dict())
            roster["bindings"].append(old_path)
            new_binding = replace(old_binding, binding_id=new_id, revision="2026-10-07.1", pilot=new_pilot.ref(),
                                  metadata={**old_binding.metadata, "display": {
                                      **old_binding.metadata["display"],
                                      "name": old_id.split("-forge-")[0].title() + " — Argentum Native — Explicit Wait and Recovery",
                                      "description": "Opt-in explicit named waits with bounded recovery on qualified foundation deck and knowledge"}})
            write_json(root / f"bindings/{new_id}.json", new_binding.to_dict())
        roster["active_bindings"] = list(v7.BINDING_FINGERPRINTS)
        write_json(root / v7.ROSTER_PATH, roster)
        new_roster = {**roster, "active_bindings": list(experiment.PROFILES),
                      "pilots": [*roster["pilots"], str(experiment.PILOT_PATH)],
                      "bindings": [*roster["bindings"], *(f"bindings/{i}.json" for i in experiment.PROFILES)]}
        write_json(root / experiment.ROSTER_PATH, new_roster)
        foundation_paths = {v7.ROSTER_PATH, v7.PILOT_PATH} | {Path(p) for p in roster["bindings"]}
        for entry in roster["decks"]:
            foundation_paths.update(Path(entry[field]) for field in ("manifest", "payload"))
        stack.enter_context(patch.object(v7, "_closure_paths", return_value=foundation_paths))
        stack.enter_context(patch.object(v7, "verify_qualified_v7_catalog", return_value={"catalogClosureSha256": "fixture"}))
        stack.enter_context(patch.object(experiment, "ROSTER_SHA256", v7._sha((root / experiment.ROSTER_PATH).read_bytes())))
        digest = hashlib.sha256()
        for relative in sorted(experiment._paths(roster)):
            content = (root / relative).read_bytes()
            digest.update(str(relative).encode() + b"\0" + len(content).to_bytes(8, "big") + content)
        stack.enter_context(patch.object(experiment, "CLOSURE_SHA256", digest.hexdigest()))

    def test_stages_canonical_closure_and_excludes_unlisted_private_files(self):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            root = Path(temporary) / "source"
            run = Path(temporary) / "run"
            run.mkdir()
            self.fixture(root, stack)
            (root / "unlisted-private.txt").write_text("must not copy")
            staged, catalog, receipt = experiment.stage_experimental_wait_catalog(
                root, root / experiment.ROSTER_PATH, run, experiment.PROFILES, 2)
            self.assertFalse(receipt["qualification"])
            self.assertEqual(receipt["maxAttempts"], 2)
            self.assertFalse((staged / "unlisted-private.txt").exists())
            self.assertEqual(catalog.stat().st_mode & 0o777, 0o600)
            self.assertEqual(experiment.verify_experimental_wait_catalog(staged, catalog, experiment.PROFILES, 2), receipt)
            binding = staged / f"bindings/{experiment.PROFILES[0]}.json"
            altered = json.loads(binding.read_text())
            altered["overrides"] = {"strategic": "changed"}
            write_json(binding, altered)
            with self.assertRaises(ValueError):
                experiment.verify_experimental_wait_catalog(staged, catalog, experiment.PROFILES, 2)

    def test_rejects_old_roster_alternate_order_and_broader_attempts_before_read(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for path, profiles, attempts in ((v7.ROSTER_PATH, experiment.PROFILES, 2),
                                            (experiment.ROSTER_PATH, experiment.PROFILES[::-1], 2),
                                            (experiment.ROSTER_PATH, experiment.PROFILES, 3)):
                with self.subTest(path=path, attempts=attempts), self.assertRaisesRegex(ValueError, "roster/order/attempts"):
                    experiment.verify_experimental_wait_catalog(root, root / path, profiles, attempts)

    def test_foundation_failure_stops_experiment_before_new_catalog_read(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            v7, "verify_qualified_v7_catalog", side_effect=v7.QualifiedV7PreflightError("foundation differs")
        ), self.assertRaisesRegex(ValueError, "foundation differs"):
            root = Path(temporary)
            experiment.verify_experimental_wait_catalog(root, root / experiment.ROSTER_PATH, experiment.PROFILES, 2)
