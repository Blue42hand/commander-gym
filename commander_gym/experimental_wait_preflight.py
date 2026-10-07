"""Offline preflight for the opt-in wait/recovery experiment; never v7 qualification."""
from __future__ import annotations

from dataclasses import replace
import json
import hashlib
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace

from . import qualified_v7_preflight as v7
from .binding_catalog import load_binding_catalog
from .game_server_binding_openai_sidecar import (
    BUILTIN_FORGE_EXPLICIT_WAIT_COMPONENT_REF, OpenAIBindingPilotComponentResolver,
)
from .identity import Binding, Pilot
from .openai_run_budget import OpenAIRunBudget
from .pilot_composition import subsystem_specs_for_pilot

ROSTER_PATH = Path("rosters/forge-explicit-wait-recovery-v8-proposed-four-seat.json")
ROSTER_SHA256 = "6419e6ac89246e17a26ea0a397788d0f3bb5e7a443d3520878c26da96073e54e"
CLOSURE_SHA256 = "b188c5db1848e71375620f1e114a658f2e89e0753ad9488fce98f107babb6738"
PILOT_PATH = Path("pilots/forge-conditional-wait-openai/revisions/2026-10-07.1/pilot.json")
PROFILES = tuple(name.split("-forge-")[0] + "-forge-explicit-wait-recovery-v8-openai"
                 for name in v7.BINDING_FINGERPRINTS)


def verify_experimental_wait_catalog(root: Path, catalog: Path, profiles: tuple[str, ...],
                                     max_attempts: int) -> dict:
    root, catalog = root.resolve(), catalog.resolve()
    if catalog != root / ROSTER_PATH or profiles != PROFILES or max_attempts != 2:
        raise v7.QualifiedV7PreflightError("experimental wait roster/order/attempts differ")
    # Keep the accepted foundation closure and qualification gate intact.
    foundation = v7.verify_qualified_v7_catalog(root, root / v7.ROSTER_PATH,
                                               *v7.SELECTED_PROFILES, 2)
    raw = v7._catalog_file(root, ROSTER_PATH).read_bytes()
    if v7._sha(raw) != ROSTER_SHA256:
        raise v7.QualifiedV7PreflightError("experimental wait roster digest changed")
    roster = json.loads(raw)
    old = json.loads((root / v7.ROSTER_PATH).read_bytes())
    old_pilot = Pilot.from_dict(json.loads((root / v7.PILOT_PATH).read_bytes()))
    expected_pilot = replace(old_pilot, revision="2026-10-07.1",
                             escalation_provider=BUILTIN_FORGE_EXPLICIT_WAIT_COMPONENT_REF)
    pilot = Pilot.from_dict(json.loads(v7._catalog_file(root, PILOT_PATH).read_bytes()))
    if pilot != expected_pilot:
        raise v7.QualifiedV7PreflightError("experimental canonical Pilot differs")
    for old_id, new_id in zip(v7.BINDING_FINGERPRINTS, PROFILES):
        binding = Binding.from_dict(json.loads(v7._catalog_file(root, Path(f"bindings/{new_id}.json")).read_bytes()))
        original = Binding.from_dict(json.loads((root / f"bindings/{old_id}.json").read_bytes()))
        expected = replace(original, binding_id=new_id, revision="2026-10-07.1",
                           pilot=pilot.ref(), metadata={**original.metadata, "display": {
                               **original.metadata.get("display", {}),
                               "name": old_id.split("-forge-")[0].title() + " — Argentum Native — Explicit Wait and Recovery",
                               "description": "Opt-in explicit named waits with bounded recovery on qualified foundation deck and knowledge",
                           }})
        if binding != expected:
            raise v7.QualifiedV7PreflightError("experimental Binding/data provenance differs")
    if (roster["active_bindings"] != list(PROFILES)
        or roster["decks"] != old["decks"] or roster["deck_knowledge"] != old["deck_knowledge"]):
        raise v7.QualifiedV7PreflightError("experimental foundation references differ")
    # A client that cannot dispatch, and an uninitialized disposable ledger.
    with tempfile.TemporaryDirectory() as temporary:
        budget = OpenAIRunBudget(Path(temporary) / "never-dispatched.json", 5)
        def forbidden(**_request):
            raise AssertionError("experimental preflight must not call a provider")
        resolver = OpenAIBindingPilotComponentResolver(
            config=SimpleNamespace(model="gpt-6-luna", max_attempts=2),
            client=SimpleNamespace(responses=SimpleNamespace(create=forbidden)), budget=budget)
        spec = next(s for s in subsystem_specs_for_pilot(pilot) if s.role == "frontier_escalation")
        runtime = resolver.resolve(spec).player
        strategic = runtime.strategic_pilot
        if (runtime.version != "6" or not strategic.explicit_wait_guidance
            or not strategic.retry_transient_server_errors or strategic.cache_friendly_history):
            raise v7.QualifiedV7PreflightError("experimental runtime configuration differs")
        loaded = load_binding_catalog(catalog, instance_root=root,
                                      pilot_factory=lambda _pilot, _binding: strategic)
        if tuple(loaded.binding_ids) != PROFILES:
            raise v7.QualifiedV7PreflightError("experimental resolved seat order differs")
    paths = _paths(old)
    digest = hashlib.sha256()
    for relative in sorted(paths):
        content = v7._catalog_file(root, relative).read_bytes()
        digest.update(str(relative).encode() + b"\0" + len(content).to_bytes(8, "big") + content)
    if digest.hexdigest() != CLOSURE_SHA256:
        raise v7.QualifiedV7PreflightError("experimental closure digest changed")
    return {"experimental": True, "qualification": False, "catalogClosureSha256": digest.hexdigest(),
            "foundationClosureSha256": foundation["catalogClosureSha256"],
            "pilotFingerprint": pilot.fingerprint(), "model": "gpt-6-luna", "maxAttempts": 2}


def _paths(old: dict) -> set[Path]:
    return v7._closure_paths(old) | {ROSTER_PATH, PILOT_PATH} | {
        Path(f"bindings/{name}.json") for name in PROFILES}


def stage_experimental_wait_catalog(root: Path, catalog: Path, run_dir: Path,
                                    profiles: tuple[str, ...], max_attempts: int):
    receipt = verify_experimental_wait_catalog(root, catalog, profiles, max_attempts)
    root = root.resolve()
    staged = run_dir / "experimental-wait-catalog"
    staged.mkdir(mode=0o700)
    for relative in sorted(_paths(json.loads((root / v7.ROSTER_PATH).read_bytes()))):
        destination = staged / relative
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copyfile(v7._catalog_file(root, relative), destination)
        destination.chmod(0o600)
    again = verify_experimental_wait_catalog(staged, staged / ROSTER_PATH, profiles, max_attempts)
    if again != receipt:
        raise v7.QualifiedV7PreflightError("staged experimental closure changed")
    return staged, staged / ROSTER_PATH, receipt
