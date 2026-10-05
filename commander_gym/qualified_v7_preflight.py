"""Fail closed before a paid v7 comparison uses an imported Binding catalog.

The private roster files are transferred by the catalog owner. This module pins
their exact closure and checks the executable Pilot without a provider request.
"""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
from typing import Any

from .binding_catalog import load_binding_catalog
from .delegated_autopass import DelegatedAutopassPilot
from .game_server_binding_openai_sidecar import (
    BUILTIN_FORGE_DECLARATIVE_CONTINUATION_COMPONENT_REF,
    OpenAIBindingPilotComponentResolver,
)
from .identity import Pilot
from .openai_responses_pilot import OpenAIResponsesPilot
from .pilot_composition import subsystem_specs_for_pilot


ROSTER_PATH = Path("rosters/forge-declarative-continuation-v7-active-four-seat.json")
ROSTER_SHA256 = "0c6bedc3d0b92a889c7f036d65cc0af2a9f0562e300e6158bbca2c87a4c16eee"
CLOSURE_SHA256 = "d8672c27417693ccdc7289746db3a3b0c8746ac47f912ffc9dcb3c822052172f"
PROMPT_SHA256 = "328eea65374ea235edb8a08fb5ea3d59fffc2e7cb59572c9f1c57003afd066cd"
CONFIG_SHA256 = "d90ace295aa77043c113d12b675ebbaf2ebd8971d626df2180a7a121e9b667a2"
PILOT_FINGERPRINT = "528c21e77b543f986da83ef5902fb9185802c7eff928ae9ab40d3cf2de8621eb"
BINDING_FINGERPRINTS = {
    "krenko-forge-declarative-continuation-v7-openai": "49bff1192f1bf1bc0b06c58f699b3f2fe426017fd57344b7f30109d06816d68f",
    "talrand-forge-declarative-continuation-v7-openai": "59cd59d3b9f095400ee753f2fd7b9ed52e3b3330a4d9105e246692cc4c36a135",
    "sythis-forge-declarative-continuation-v7-openai": "f1de4f8c8f06beb56d44f7a5f46d71f31dbd5186db8614d683bfd6e7d05f195b",
    "lathril-forge-declarative-continuation-v7-openai": "3ff2aa520384a98a721b60f762fa7cba7989c9fda8ac1ee699cf2039b0d2cc3e",
}
PILOT_PATH = Path("pilots/forge-conditional-wait-openai/revisions/2026-10-04.2/pilot.json")
SELECTED_PROFILES = (
    "krenko-forge-declarative-continuation-v7-openai",
    "talrand-forge-declarative-continuation-v7-openai",
)


class QualifiedV7PreflightError(ValueError):
    """Catalog or executable Pilot differs from the qualified v7 comparison."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _catalog_file(root: Path, relative: Path) -> Path:
    root = root.resolve()
    if relative.is_absolute() or ".." in relative.parts:
        raise QualifiedV7PreflightError("catalog closure path is not relative and confined")
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise QualifiedV7PreflightError("catalog closure file is missing or outside instance root")
    return path


def _closure_paths(roster: dict[str, Any]) -> set[Path]:
    paths = {ROSTER_PATH}
    for key in ("bindings", "pilots"):
        paths.update(Path(value) for value in roster[key])
    for key in ("decks", "deck_knowledge"):
        for entry in roster[key]:
            paths.update(Path(entry[field]) for field in ("manifest", "payload"))
    if len(paths) != 62:
        raise QualifiedV7PreflightError("v7 catalog closure file count changed")
    return paths


def _closure(root: Path, roster: dict[str, Any]) -> str:
    digest = hashlib.sha256()
    for relative in sorted(_closure_paths(roster)):
        content = _catalog_file(root, relative).read_bytes()
        digest.update(str(relative).encode() + b"\0" + len(content).to_bytes(8, "big") + content)
    return digest.hexdigest()


class _FakeResponses:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create(self, **request: Any) -> dict[str, Any]:
        self.calls.append(request)
        return {"status": "completed", "output_text": json.dumps({
            "channel": "action", "choice": {"semanticId": "preflight-pass", "params": {}},
        })}


def _verified_runtime_config_digest(spec: Any, runtime: Any, strategic: Any) -> str:
    runtime_config = {
        "model": strategic.model, "maxAttempts": strategic.max_attempts,
        "delegatedPilotVersion": runtime.version,
        "providerRef": asdict(spec.ref),
        "runtimeFlags": {field: getattr(runtime, field) for field in (
            "allow_named_deferrals", "guarded_then_cast_templates", "allow_declarative_continuation",
        )},
        "strategicFlags": {field: getattr(strategic, field) for field in (
            "allow_priority_delegation", "allow_named_deferrals", "require_nonempty_named_deferrals",
            "compact_model_observation", "guarded_then_cast_templates", "allow_declarative_continuation",
        )},
    }
    digest = _sha(json.dumps(runtime_config, sort_keys=True, separators=(",", ":")).encode())
    if digest != CONFIG_SHA256:
        raise QualifiedV7PreflightError("v7 runtime config digest changed")
    return digest


def verify_qualified_v7_catalog(
    instance_root: Path, catalog_path: Path, profile_a: str, profile_b: str,
    max_attempts: int,
) -> dict[str, str | int]:
    """Verify exact private closure, canonical refs, runtime flags and prompt, offline."""
    root = instance_root.expanduser().resolve()
    catalog = catalog_path.expanduser().resolve()
    if catalog != root / ROSTER_PATH:
        raise QualifiedV7PreflightError("paid v7 comparison requires the exact roster path")
    raw = _catalog_file(root, ROSTER_PATH).read_bytes()
    if _sha(raw) != ROSTER_SHA256:
        raise QualifiedV7PreflightError("v7 roster digest changed")
    roster = json.loads(raw)
    if (roster.get("roster_id") != ROSTER_PATH.stem
        or roster.get("roster_revision") != "2026-10-04.2"
        or set(roster.get("active_bindings", [])) != set(BINDING_FINGERPRINTS)
        or (profile_a, profile_b) != SELECTED_PROFILES):
        raise QualifiedV7PreflightError("v7 roster or selected Binding IDs changed")
    closure_digest = _closure(root, roster)
    if closure_digest != CLOSURE_SHA256:
        raise QualifiedV7PreflightError("v7 catalog closure digest changed")
    for binding_id, fingerprint in BINDING_FINGERPRINTS.items():
        binding = json.loads(_catalog_file(root, Path("bindings") / f"{binding_id}.json").read_bytes())
        if (binding.get("fingerprint") != fingerprint
            or binding.get("pilot", {}).get("fingerprint") != PILOT_FINGERPRINT
            or binding.get("pilot", {}).get("revision") != "2026-10-04.2"):
            raise QualifiedV7PreflightError("v7 Binding/Pilot identity changed")
    pilot = Pilot.from_dict(json.loads(_catalog_file(root, PILOT_PATH).read_bytes()))
    specs = [spec for spec in subsystem_specs_for_pilot(pilot) if spec.role == "frontier_escalation"]
    if (pilot.fingerprint() != PILOT_FINGERPRINT or len(specs) != 1
        or specs[0].ref != BUILTIN_FORGE_DECLARATIVE_CONTINUATION_COMPONENT_REF):
        raise QualifiedV7PreflightError("v7 Pilot provider component changed")
    responses = _FakeResponses()
    resolver = OpenAIBindingPilotComponentResolver(
        config=SimpleNamespace(model="gpt-6-luna", max_attempts=max_attempts),
        client=SimpleNamespace(responses=responses),
    )
    component = resolver.resolve(specs[0])
    runtime = component.player
    strategic = runtime.strategic_pilot if isinstance(runtime, DelegatedAutopassPilot) else None
    if (not isinstance(strategic, OpenAIResponsesPilot)
        or not all(getattr(runtime, field) is True for field in (
            "allow_named_deferrals", "guarded_then_cast_templates", "allow_declarative_continuation",
        ))
        or not all(getattr(strategic, field) is True for field in (
            "allow_priority_delegation", "allow_named_deferrals", "require_nonempty_named_deferrals",
            "compact_model_observation", "guarded_then_cast_templates", "allow_declarative_continuation",
        ))
        or strategic.model != "gpt-6-luna"
        or strategic.max_attempts != max_attempts):
        raise QualifiedV7PreflightError("v7 runtime features or model are disabled")
    config_digest = _verified_runtime_config_digest(specs[0], runtime, strategic)
    strategic.choose({
        "type": "GameServerSeat", "perspectivePlayerId": "preflight-seat",
        "agentToAct": "preflight-seat", "pendingDecision": None, "state": {},
        "legalActions": [{"actionId": 0, "semanticId": "preflight-pass",
                          "kind": "PassPriority", "parameterSpec": {"allowedFields": {}}}],
    })
    prompt_digest = _sha(responses.calls[0]["instructions"].encode())
    if prompt_digest != PROMPT_SHA256:
        raise QualifiedV7PreflightError("v7 runtime prompt digest changed")
    # Canonical loader verifies every Deck, Pilot and DeckKnowledge reference/payload.
    loaded = load_binding_catalog(catalog, instance_root=root,
                                  pilot_factory=lambda _pilot, _binding: strategic)
    if set(loaded.binding_ids) != set(BINDING_FINGERPRINTS):
        raise QualifiedV7PreflightError("v7 resolved active Bindings changed")
    return {"catalogClosureSha256": closure_digest, "pilotFingerprint": PILOT_FINGERPRINT,
            "promptSha256": prompt_digest, "configSha256": config_digest,
            "model": strategic.model, "maxAttempts": strategic.max_attempts}


def stage_qualified_v7_catalog(
    instance_root: Path, catalog_path: Path, run_dir: Path,
    profile_a: str, profile_b: str, max_attempts: int,
) -> tuple[Path, Path, dict[str, str | int]]:
    """Copy the exact reviewed closure into the private run before services start."""
    receipt = verify_qualified_v7_catalog(
        instance_root, catalog_path, profile_a, profile_b, max_attempts,
    )
    root = instance_root.expanduser().resolve()
    roster = json.loads((root / ROSTER_PATH).read_bytes())
    staged_root = run_dir / "qualified-v7-catalog"
    staged_root.mkdir(mode=0o700)
    for relative in sorted(_closure_paths(roster)):
        source = _catalog_file(root, relative)
        destination = staged_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copyfile(source, destination)
        destination.chmod(0o600)
    staged_catalog = staged_root / ROSTER_PATH
    staged_receipt = verify_qualified_v7_catalog(
        staged_root, staged_catalog, profile_a, profile_b, max_attempts,
    )
    if staged_receipt != receipt:
        raise QualifiedV7PreflightError("staged v7 catalog closure changed")
    return staged_root, staged_catalog, receipt
