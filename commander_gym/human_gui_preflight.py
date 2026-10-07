"""Exact canonical three-AI selection for a human Krenko GUI session, offline."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

from . import experimental_wait_preflight as wait
from . import qualified_v7_preflight as v7
from .binding_catalog import load_binding_catalog

ROSTER_PATH = Path("rosters/human-krenko-three-luna-explicit-wait-recovery-v8.json")
ROSTER_SHA256 = "5a0a8121be6ae43b55ecf79bc7751b2af25b2b83e1a947cd34dd59e4b6798f1a"
PROFILES = wait.PROFILES[1:]


def closure_paths(root: Path) -> set[Path]:
    old = json.loads(v7._catalog_file(root, v7.ROSTER_PATH).read_bytes())
    return wait._paths(old) | {ROSTER_PATH}


def verify_human_gui_catalog(root: Path, catalog: Path, max_attempts: int = 2) -> dict:
    root, catalog = root.resolve(), catalog.resolve()
    if catalog != root / ROSTER_PATH or max_attempts != 2:
        raise ValueError("human GUI requires the exact roster and two-attempt Pilot")
    foundation = wait.verify_experimental_wait_catalog(root, root / wait.ROSTER_PATH, wait.PROFILES, 2)
    raw = v7._catalog_file(root, ROSTER_PATH).read_bytes()
    four = json.loads((root / wait.ROSTER_PATH).read_bytes())
    expected = {**four, "roster_id": "human-krenko-three-luna-explicit-wait-recovery-v8",
                "roster_revision": "2026-10-07.1", "source_roster": str(wait.ROSTER_PATH),
                "active_bindings": list(PROFILES)}
    if v7._sha(raw) != ROSTER_SHA256 or json.loads(raw) != expected:
        raise ValueError("human GUI selection changed or differs from canonical source")
    loaded = load_binding_catalog(catalog, instance_root=root,
                                  pilot_factory=lambda pilot, _binding: SimpleNamespace(name=pilot.pilot_id, version=pilot.revision, choose=lambda _obs: (_ for _ in ()).throw(AssertionError("offline preflight cannot choose"))))
    if tuple(loaded.binding_ids) != PROFILES:
        raise ValueError("human GUI must advertise exactly Talrand/Sythis/Lathril")
    digest = hashlib.sha256()
    for relative in sorted(closure_paths(root)):
        content = v7._catalog_file(root, relative).read_bytes()
        digest.update(str(relative).encode() + b"\0" + len(content).to_bytes(8, "big") + content)
    return {**foundation, "humanCommander": "Krenko, Mob Boss", "profiles": list(PROFILES),
            "rosterSha256": v7._sha(raw), "catalogClosureSha256": digest.hexdigest(),
            "parentWaitClosureSha256": foundation["catalogClosureSha256"]}


def stage_human_gui_catalog(root: Path, catalog: Path, run_dir: Path):
    receipt = verify_human_gui_catalog(root, catalog)
    root = root.resolve()
    staged = run_dir / "human-gui-catalog"
    staged.mkdir(mode=0o700)
    for relative in sorted(closure_paths(root)):
        target = staged / relative
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copyfile(v7._catalog_file(root, relative), target)
        target.chmod(0o600)
    if verify_human_gui_catalog(staged, staged / ROSTER_PATH) != receipt:
        raise ValueError("staged human GUI catalog changed")
    return staged, staged / ROSTER_PATH, receipt

SESSION_CATALOG = Path("rosters/human-three-luna.json")
SESSION_MANIFEST = Path("session-manifest.json")
BINDING_FINGERPRINTS = (
    "3b992f9fcce6554c8a2dece8a78d7a8807897f8b03a64f3df3707b1cf84318d0",
    "c0c69103997262af6d8486e2f24a7328159ac3f4b8e9eed1630f4059bdb1c796",
    "b92b40a61e590aa415318f8e80e347210959e34b8f2ca7e70258b56ff0c059aa",
)


def export_session_catalog(root: Path, destination: Path) -> dict:
    """Minimize an already qualified LOCAL catalog; no human deck or history."""
    proof = verify_human_gui_catalog(root, root / ROSTER_PATH)
    source = json.loads((root / ROSTER_PATH).read_bytes())
    bindings = [json.loads((root / f"bindings/{name}.json").read_bytes()) for name in PROFILES]
    deck_refs = {b["deck"]["fingerprint"] for b in bindings}
    knowledge_refs = {b["deck_knowledge"]["fingerprint"] for b in bindings if b.get("deck_knowledge")}
    roster = {
        "schema_version": 1, "roster_id": "human-three-luna-explicit-wait-recovery-v8",
        "roster_revision": "2026-10-07.1", "active_bindings": list(PROFILES),
        "bindings": [f"bindings/{name}.json" for name in PROFILES],
        "pilots": [str(wait.PILOT_PATH)],
        "decks": [e for e in source["decks"] if json.loads((root / e["manifest"]).read_bytes())["fingerprint"] in deck_refs],
        "deck_knowledge": [e for e in source.get("deck_knowledge", []) if json.loads((root / e["manifest"]).read_bytes())["fingerprint"] in knowledge_refs],
    }
    paths = {Path(p) for p in roster["bindings"] + roster["pilots"]}
    for item in roster["decks"] + roster["deck_knowledge"]:
        paths.update(Path(item[k]) for k in ("manifest", "payload"))
    destination.mkdir(mode=0o700)
    for relative in sorted(paths):
        target = destination / relative
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.write_bytes(v7._catalog_file(root, relative).read_bytes())
        target.chmod(0o600)
    target = destination / SESSION_CATALOG
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    target.write_text(json.dumps(roster, sort_keys=True, indent=2) + "\n")
    target.chmod(0o600)
    paths.add(SESSION_CATALOG)
    manifest = {"schemaVersion": 1, "profiles": list(PROFILES), "maxAttempts": 2,
                "model": "gpt-6-luna", "qualification": False,
                "foundationClosureSha256": proof["foundationClosureSha256"],
                "parentWaitClosureSha256": proof["parentWaitClosureSha256"],
                "files": {str(p): v7._sha((destination / p).read_bytes()) for p in sorted(paths)}}
    target = destination / SESSION_MANIFEST
    target.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    target.chmod(0o600)
    verify_session_catalog(destination, v7._sha(target.read_bytes()))
    return manifest


def verify_session_catalog(root: Path, expected_manifest_sha256: str) -> dict:
    """Verify a frozen minimized package without requiring unrelated foundation data."""
    from .identity import Binding, Pilot
    from .game_server_binding_openai_sidecar import BUILTIN_FORGE_EXPLICIT_WAIT_COMPONENT_REF
    raw = v7._catalog_file(root, SESSION_MANIFEST).read_bytes()
    if v7._sha(raw) != expected_manifest_sha256:
        raise ValueError("session manifest changed")
    manifest = json.loads(raw)
    if manifest.get("profiles") != list(PROFILES) or manifest.get("model") != "gpt-6-luna" or manifest.get("maxAttempts") != 2:
        raise ValueError("session profiles/model/attempts changed")
    files = manifest["files"]
    actual = {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}
    if actual != set(files) | {str(SESSION_MANIFEST)}:
        raise ValueError("session package has missing or unrelated files")
    for name, sha in files.items():
        if v7._sha(v7._catalog_file(root, Path(name)).read_bytes()) != sha:
            raise ValueError("session catalog file changed")
    loaded = load_binding_catalog(root / SESSION_CATALOG, instance_root=root,
                                  pilot_factory=lambda pilot, _binding: SimpleNamespace(name=pilot.pilot_id, version=pilot.revision, choose=lambda _obs: (_ for _ in ()).throw(AssertionError("offline preflight cannot choose"))))
    if tuple(loaded.binding_ids) != PROFILES:
        raise ValueError("session must advertise exactly three AI profiles")
    for name, expected in zip(PROFILES, BINDING_FINGERPRINTS):
        binding = Binding.from_dict(json.loads((root / f"bindings/{name}.json").read_bytes()))
        if binding.fingerprint() != expected:
            raise ValueError("session canonical Binding changed")
    pilot = Pilot.from_dict(json.loads((root / wait.PILOT_PATH).read_bytes()))
    if pilot.escalation_provider != BUILTIN_FORGE_EXPLICIT_WAIT_COMPONENT_REF:
        raise ValueError("session requires recovery and explicit wait component8")
    return manifest
