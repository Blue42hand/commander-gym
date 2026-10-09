"""Concrete byte/catalog/dependency inspection, never a paid qualification run."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import zipfile

from .manual_runtime_launch import dependency_lock
from .manual_runtime_profile import decode, digest, protected_bytes, require, verify_artifacts


def _manifest(archive: zipfile.ZipFile) -> dict:
    text = archive.read("META-INF/MANIFEST.MF").decode().replace("\r\n ", "")
    return dict(row.split(": ", 1) for row in text.splitlines() if ": " in row)


def inspect_matched_artifacts(profile: dict) -> dict:
    def data(role):
        artifact = profile["artifacts"][role]
        require(len(artifact["files"]) == 1, "single_jar_required")
        name = next(iter(artifact["files"]))
        return protected_bytes(Path(artifact["root"]) / name, uid=artifact["uid"], max_bytes=512 * 1024 * 1024)
    adapter_bytes, server_bytes = data("adapter"), data("server")
    with zipfile.ZipFile(io.BytesIO(adapter_bytes)) as archive:
        metadata = _manifest(archive)
        require(metadata.get("Argentum-Revision") == profile["engineSha"]
                and metadata.get("Commander-Gym-Revision") == profile["gymSha"], "adapter_source_mismatch")
        imports = archive.read("META-INF/spring/org.springframework.boot.autoconfigure.AutoConfiguration.imports").decode().splitlines()
        require("org.commandergym.argentum.CommanderGymAutoConfiguration" in imports
                and "org/commandergym/argentum/CommanderGymControllerProvider.class" in archive.namelist(), "compiled_provider_missing")
    with zipfile.ZipFile(io.BytesIO(server_bytes)) as archive:
        require(_manifest(archive).get("Argentum-Revision") == profile["engineSha"], "server_source_mismatch")
        embedded = [name for name in archive.namelist() if name.startswith("BOOT-INF/lib/") and name.endswith(".jar") and "commander-gym-argentum-adapter" in name]
        require(len(embedded) == 1 and archive.read(embedded[0]) == adapter_bytes, "embedded_adapter_mismatch")
    return {"serverSha256": hashlib.sha256(server_bytes).hexdigest(), "adapterSha256": hashlib.sha256(adapter_bytes).hexdigest(), "embeddedAdapter": embedded[0]}


def inspect_catalog(profile: dict) -> dict:
    artifact = profile["artifacts"]["catalog"]
    root = Path(artifact["root"])
    manifest = decode(protected_bytes(root / profile["catalog"]["manifest"], uid=artifact["uid"]))
    require(type(manifest.get("files")) is dict and all(name in artifact["files"] and artifact["files"][name] == sha for name, sha in manifest["files"].items()), "catalog_manifest_seal")
    require(manifest.get("model") == "gpt-6-luna" and type(manifest.get("maxAttempts")) is int and manifest["maxAttempts"] == 2, "catalog_provider_settings")
    require(set(manifest.get("profiles", [])) == set(profile["catalog"]["bindings"]), "catalog_profile_set")
    # Canonical resolver/factory validation is performed with an injected rejecting
    # client. No SDK client constructor, recording thread or game is created.
    from .game_server_binding_openai_sidecar import (
        binding_openai_game_server_config_from_environment,
        OpenAIBindingPilotComponentResolver,
    )
    from .binding_catalog import load_binding_catalog
    from .pilot_composition import compose_pilot_runtime
    class RejectingClient:
        @property
        def responses(self):
            return self
        def create(self, **kwargs):
            raise AssertionError("offline qualification forbids provider calls")
    config = binding_openai_game_server_config_from_environment({
        "OPENAI_API_KEY": "qa-placeholder-never-a-credential", "COMMANDER_GYM_SIDECAR_TOKEN": "qa-placeholder-never-a-token",
        "COMMANDER_GYM_SIDECAR_PORT": "8083", "COMMANDER_GYM_MANUAL_UNCAPPED": "true",
        "COMMANDER_GYM_INSTANCE_ROOT": str(root), "COMMANDER_GYM_BINDING_CATALOG": str(root / profile["catalog"]["roster"]),
        "COMMANDER_GYM_OPENAI_MODEL": "gpt-6-luna", "COMMANDER_GYM_OPENAI_TIMEOUT": "90", "COMMANDER_GYM_OPENAI_MAX_ATTEMPTS": "2"})
    resolver = OpenAIBindingPilotComponentResolver(config=config.sidecar, client=RejectingClient(), budget=None, cache_friendly_history=config.cache_friendly_history, manual_uncapped=True)
    loaded = load_binding_catalog(root / profile["catalog"]["roster"], instance_root=root,
                                  pilot_factory=lambda pilot, binding: compose_pilot_runtime(pilot, resolver))
    require(set(loaded.binding_ids) == set(profile["catalog"]["bindings"]), "canonical_binding_set")
    for binding_id in loaded.binding_ids:
        resolved = loaded.resolver.resolve(binding_id)
        require(resolved.binding.fingerprint == profile["catalog"]["bindings"][binding_id]
                and resolved.pilot.fingerprint == profile["catalog"]["pilotFingerprint"], "canonical_fingerprint_mismatch")
    pilot_files = [name for name in artifact["files"] if name.startswith("pilots/") and name.endswith("/pilot.json")]
    require(len(pilot_files) == 1, "single_pilot_required")
    pilot = decode(protected_bytes(root / pilot_files[0], uid=artifact["uid"]))
    require(pilot.get("fingerprint") == profile["catalog"]["pilotFingerprint"]
            and pilot.get("escalation_provider", {}).get("digest") == profile["catalog"]["componentDigest"], "component_digest_mismatch")
    # Return only exact seals, no private decks/prompts/provider payloads.
    return {"manifestSha256": artifact["files"][profile["catalog"]["manifest"]], "profiles": sorted(loaded.binding_ids), "externalProviderCalls": 0}


def inspect_runtime(profile: dict) -> dict:
    verify_artifacts(profile)
    matched = inspect_matched_artifacts(profile)
    catalog = inspect_catalog(profile)
    lock = dependency_lock(profile)
    return {"schemaVersion": 1, "profileSha256": digest(profile), "matchedArtifacts": matched,
            "catalog": catalog, "dependencyLockSha256": digest(lock), "externalProviderCalls": 0,
            "activationEligible": False,
            "missingBehavioralGates": ["dependencyRuntime", "fakeProviderAdmission", "singleRecorder", "recordingRestore", "idlePromotionRollback", "httpsIngress", "cleanup"]}
