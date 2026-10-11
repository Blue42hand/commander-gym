"""Exact sealed manual runtime identity, independent of keyless host manifests.

Validation never opens a provider credential. Production requires protected
root custody and an exact qualification/approval tuple; QA is permanently marked
non-deployable. There is no fallback from malformed manual data to keyless mode.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any, Mapping


class ManualRuntimeError(ValueError):
    """Bounded diagnosis; never include source payloads or credentials."""


SHA = re.compile(r"[0-9a-f]{64}\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
ROLES = frozenset({"server", "adapter", "frontend", "sidecar", "dependencies", "launcher", "config", "catalog", "recorder", "legacyNative", "legacySource"})
GATES = frozenset({"matchedArtifacts", "dependencyClosure", "catalogSeals", "fakeProviderAdmission", "singleRecorder", "recordingRestore", "idlePromotionRollback", "httpsIngress", "cleanup"})
PROFILE_FIELDS = frozenset({"schemaVersion", "profile", "purpose", "engineSha", "gymSha", "recordingSchemaVersion", "settings", "artifacts", "catalog", "ingress", "identities", "paths", "credentialPolicy", "recordingAuthority"})


def require(condition: bool, code: str) -> None:
    if not condition:
        raise ManualRuntimeError(code)


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def decode(raw: bytes) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate_json_key")
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ManualRuntimeError("nonfinite_json")))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ManualRuntimeError("invalid_json") from error
    require(type(value) is dict, "object_required")
    return value


def relative_path(value: Any) -> str:
    require(type(value) is str and value and "\\" not in value and "\x00" not in value, "invalid_relative_path")
    path = PurePosixPath(value)
    require(not path.is_absolute() and all(part not in ("", ".", "..") for part in value.split("/")), "invalid_relative_path")
    return value


def absolute_path(value: Any) -> str:
    require(type(value) is str and value.startswith("/") and "\\" not in value and "\x00" not in value, "invalid_absolute_path")
    require(all(part not in ("", ".", "..") for part in value.split("/")[1:]), "invalid_absolute_path")
    return value


def _sha(value: Any) -> bool:
    return type(value) is str and SHA.fullmatch(value) is not None


def _commit(value: Any) -> bool:
    return type(value) is str and COMMIT.fullmatch(value) is not None


def validate_profile(profile: Mapping[str, Any]) -> dict:
    redis_qa = type(profile) is dict and profile.get("profile") == "manual-luna-redis-qa-v1"
    require(type(profile) is dict and set(profile) == PROFILE_FIELDS | ({"redisQa"} if redis_qa else set()), "profile_fields")
    require(type(profile["schemaVersion"]) is int and profile["schemaVersion"] == 1, "profile_schema")
    require((profile["profile"] == "manual-luna-v1" and profile["purpose"] in ("qualification", "native-manual-play"))
            or (redis_qa and profile["purpose"] == "qualification"), "profile_kind")
    require(_commit(profile["engineSha"]) and _commit(profile["gymSha"]), "source_pins")
    require(type(profile["recordingSchemaVersion"]) is int and profile["recordingSchemaVersion"] == 1, "recording_schema")
    expected = {"defaultController": "engine", "manualOnly": True, "manualUncapped": True,
                "model": "gpt-6-luna", "requestTimeoutSeconds": 90, "maxAttempts": 2,
                "callbackTimeoutSeconds": 110, "nativeTimeoutMs": 120000,
                "paidProvidersEnabled": True, "accountsEnabled": False, "redisEnabled": False}
    if redis_qa:
        expected["redisEnabled"] = True
        from .qa_redis_variant import validate_settings
        validate_settings(profile)
    require(profile["settings"] == expected and all(type(profile["settings"][k]) is type(v) for k, v in expected.items()), "manual_settings")
    artifacts = profile["artifacts"]
    require(type(artifacts) is dict and set(artifacts) == ROLES, "artifact_roles")
    for role, artifact in artifacts.items():
        require(type(artifact) is dict and set(artifact) == {"root", "uid", "files"}, "artifact_fields")
        absolute_path(artifact["root"])
        require(type(artifact["uid"]) is int and artifact["uid"] >= 0, "artifact_uid")
        require(type(artifact["files"]) is dict and artifact["files"] and len(artifact["files"]) <= 100000, "artifact_inventory")
        for name, sha in artifact["files"].items():
            relative_path(name)
            require(_sha(sha), "artifact_digest")
    identities = profile["identities"]
    require(type(identities) is dict and set(identities) == {"nativeUid", "sidecarUid", "proxyUid"}, "identity_fields")
    require(all(type(uid) is int and uid > 0 for uid in identities.values()) and len(set(identities.values())) == 3, "distinct_identities")
    if profile["purpose"] == "native-manual-play":
        require(all(a["uid"] == 0 for a in artifacts.values()), "production_artifact_custody")
        for role, artifact in artifacts.items():
            root = Path(artifact["root"])
            if role in ("legacyNative", "legacySource"):
                require(str(root) == '/usr/local/libexec/argentum-' + ('native' if role == 'legacyNative' else 'source'), "production_legacy_layout")
            else:
                require(root == Path('/srv/argentum-luna/artifacts') / role / digest(artifact['files']), "production_artifact_layout")
            require(not any(name.endswith(('.env', '.token', '.key', '.p12', '.pfx'))
                            or (role != 'dependencies' and name.endswith('.pem')) for name in artifact['files']), "credential_in_artifact_inventory")
    paths = profile["paths"]
    require(type(paths) is dict and set(paths) == {"recordingRoot", "lifecycleSocket", "recorderSocket", "registryPath"}, "runtime_paths")
    for path in paths.values():
        absolute_path(path)
    require(len(set(paths.values())) == len(paths), "runtime_path_alias")
    if profile["purpose"] == "qualification":
        require(all("qa" in PurePosixPath(path).parts for path in paths.values()), "qa_namespace_required")
    authority = profile['recordingAuthority']
    require(type(authority) is dict and set(authority) == {'registrySha256', 'registryUid'}
            and _sha(authority['registrySha256']) and type(authority['registryUid']) is int and authority['registryUid'] >= 0, 'recording_authority')
    if profile['purpose'] == 'native-manual-play':
        require(paths == {'recordingRoot': '/var/lib/commander-gym/runs/native',
                          'lifecycleSocket': '/run/argentum-play/lifecycle.sock',
                          'recorderSocket': '/run/argentum-luna-ipc/native.sock',
                          'registryPath': '/etc/argentum-play/recording-dispositions/acknowledged-incomplete.json'}
                and authority['registryUid'] == 0, 'production_runtime_paths')
    ingress = profile["ingress"]
    require(type(ingress) is dict and set(ingress) == {"origins", "backend", "sidecar", "lanAddress", "lanSubnet", "hostLocalTrusted"}, "ingress_fields")
    require(type(ingress["origins"]) is list and len(ingress["origins"]) in (1, 2) and len(set(ingress["origins"])) == len(ingress["origins"]), "exact_tailnet_or_shared_origins")
    from urllib.parse import urlsplit
    for origin in ingress["origins"]:
        uri = urlsplit(origin)
        require(uri.scheme == "https" and uri.hostname and not uri.username and not uri.password
                and uri.path == "" and not uri.query and not uri.fragment and "*" not in origin, "https_origin_required")
    if len(ingress["origins"]) == 1:
        uri = urlsplit(ingress["origins"][0])
        require(not uri.port and uri.hostname.endswith(".ts.net"), "tailnet_only_origin")
    require(ingress["backend"] == "127.0.0.1:18080" and ingress["sidecar"] == "127.0.0.1:8083" and ingress["hostLocalTrusted"] is True, "private_listeners")
    import ipaddress
    try:
        address = ipaddress.ip_address(ingress["lanAddress"])
        subnet = ipaddress.ip_network(ingress["lanSubnet"])
        require(address.version == 4 and address.is_private and address in subnet and subnet.prefixlen >= 24, "exact_lan_scope")
    except ValueError as error:
        raise ManualRuntimeError("exact_lan_scope") from error
    catalog = profile["catalog"]
    require(type(catalog) is dict and set(catalog) == {"roster", "manifest", "bindings", "pilotFingerprint", "componentDigest"}, "catalog_fields")
    relative_path(catalog["roster"])
    relative_path(catalog["manifest"])
    require(catalog["roster"] in artifacts["catalog"]["files"] and catalog["manifest"] in artifacts["catalog"]["files"], "catalog_seal_required")
    require(type(catalog["bindings"]) is dict and len(catalog["bindings"]) == 3 and all(type(name) is str and name and _sha(fp) for name, fp in catalog["bindings"].items()), "binding_pins")
    require(_sha(catalog["pilotFingerprint"]) and type(catalog["componentDigest"]) is str and catalog["componentDigest"].startswith("sha256:") and _sha(catalog["componentDigest"][7:]), "pilot_pins")
    policy = profile["credentialPolicy"]
    require(policy == {"providerReaders": ["sidecar"], "tokenReaders": ["native", "sidecar"],
                       "providerSource": "existing-private-env", "delivery": "systemd-credentials",
                       "valueInManifest": False}, "credential_policy")
    if profile['purpose'] == 'native-manual-play':
        from .manual_runtime_config import validate_config_contract
        validate_config_contract(profile)
    # A secret-shaped extra field anywhere in metadata is never accepted.
    return dict(profile)


def protected_bytes(path: Path, *, uid: int, max_bytes: int = 64 * 1024 * 1024) -> bytes:
    # Ancestors may be root-owned or owned by this role, never group/world writable.
    for parent in (path.parent, *path.parent.parents):
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid in (0, uid) and info.st_mode & 0o022 == 0, "untrusted_parent")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == uid and info.st_mode & 0o022 == 0 and info.st_size <= max_bytes, "untrusted_file")
        chunks = []
        size = 0
        while data := os.read(fd, min(1024 * 1024, max_bytes - size + 1)):
            size += len(data)
            require(size <= max_bytes, "file_size")
            chunks.append(data)
        after = os.fstat(fd)
        require((info.st_size, info.st_mtime_ns, info.st_ctime_ns) == (after.st_size, after.st_mtime_ns, after.st_ctime_ns), "file_changed")
        return b"".join(chunks)
    finally:
        os.close(fd)


def verify_artifacts(profile: dict, *, roles: frozenset | None = None) -> None:
    validate_profile(profile)
    if roles is None:
        authority = profile['recordingAuthority']
        raw = protected_bytes(Path(profile['paths']['registryPath']), uid=authority['registryUid'])
        require(hashlib.sha256(raw).hexdigest() == authority['registrySha256'], 'recording_authority_changed')
    selected = ROLES if roles is None else roles
    require(type(selected) is frozenset and selected <= ROLES, "artifact_selection")
    for role in selected:
        artifact = profile["artifacts"][role]
        root = Path(artifact["root"])
        require(not root.is_symlink() and root.is_dir(), "artifact_root")
        for directory in (root, *root.parents):
            info = directory.lstat()
            require(stat.S_ISDIR(info.st_mode) and info.st_uid in (0, artifact["uid"])
                    and info.st_mode & 0o022 == 0, "untrusted_parent")
        files = {}
        for path in root.rglob("*"):
            require(not path.is_symlink(), "artifact_symlink")
            if path.is_file():
                files[str(path.relative_to(root))] = path
            else:
                info = path.lstat()
                require(stat.S_ISDIR(info.st_mode), "artifact_special_file")
                require(info.st_uid == artifact["uid"] and info.st_mode & 0o022 == 0, "untrusted_artifact_directory")
        require(set(files) == set(artifact["files"]), "unindexed_artifact")
        for name, path in files.items():
            # JVM jars and sealed interpreters may exceed64MiB; still bounded.
            raw = protected_bytes(path, uid=artifact["uid"], max_bytes=512 * 1024 * 1024)
            require(hashlib.sha256(raw).hexdigest() == artifact["files"][name], "artifact_changed")


def validate_qualification(profile: dict, receipt: dict, *, now: float) -> None:
    require(set(receipt) == {"schemaVersion", "profileSha256", "gates", "sourceCi", "providerMode", "externalProviderCalls", "observedUnix"}, "qualification_fields")
    require(type(receipt["schemaVersion"]) is int and receipt["schemaVersion"] == 1 and receipt["profileSha256"] == digest(profile), "qualification_identity")
    require(type(receipt["gates"]) is dict and set(receipt["gates"]) == GATES and all(value is True for value in receipt["gates"].values()), "qualification_gates")
    require(receipt["providerMode"] == "fake" and type(receipt["externalProviderCalls"]) is int and receipt["externalProviderCalls"] == 0, "keyless_qualification_required")
    observed = receipt["observedUnix"]
    require(type(observed) in (int, float) and math.isfinite(observed) and 0 <= now - observed <= 86400, "qualification_stale")
    ci = receipt["sourceCi"]
    require(type(ci) is dict and set(ci) == {"engine", "gym"}, "source_ci_required")
    for role in ci:
        check = ci[role]
        require(type(check) is dict and set(check) == {"sha", "checks"} and check["sha"] == profile[role + "Sha"], "source_ci_identity")
        require(type(check["checks"]) is list and check["checks"] and all(type(item) is dict and set(item) == {"name", "conclusion", "runId"} and type(item["name"]) is str and item["name"] and item["conclusion"] == "success" and type(item["runId"]) is int and item["runId"] > 0 for item in check["checks"]), "source_ci_failed")
        required = {'engine': {'coverage'}, 'gym': {'python', 'jvm-adapter', 'manual-runtime-sdk'}}[role]
        names = [item['name'] for item in check['checks']]
        require(len(names) == len(set(names)) and required <= set(names), 'required_source_ci_missing')



def validate_approval(profile: dict, qualification: dict, approval: dict, *, previous_id: str, sequence: int) -> None:
    require(set(approval) == {"schemaVersion", "approved", "scope", "profileSha256", "qualificationSha256", "previousRuntimeId", "sequence"}, "approval_fields")
    require(type(approval["schemaVersion"]) is int and approval["schemaVersion"] == 1 and approval["approved"] is True and approval["scope"] == "activate-manual-runtime", "activation_approval_required")
    require(approval["profileSha256"] == digest(profile) and approval["qualificationSha256"] == digest(qualification) and approval["previousRuntimeId"] == previous_id and type(approval["sequence"]) is int and approval["sequence"] == sequence, "approval_tuple_mismatch")


@dataclass(frozen=True)
class ValidatedRuntime:
    profile_bytes: bytes
    runtime_id: str
    sequence: int

    @property
    def profile(self) -> dict:
        return decode(self.profile_bytes)


def precredential_validate(profile: dict, qualification: dict, approval: dict, *, previous_id: str,
                           sequence: int, now: float) -> ValidatedRuntime:
    validate_profile(profile)
    require(profile["purpose"] == "native-manual-play", "qa_never_activates")
    validate_qualification(profile, qualification, now=now)
    validate_approval(profile, qualification, approval, previous_id=previous_id, sequence=sequence)
    verify_artifacts(profile)
    return ValidatedRuntime(canonical(profile), digest(profile), sequence)


def recording_pins(profile: dict) -> dict:
    validate_profile(profile)
    return {"engine": profile["engineSha"], "gym": profile["gymSha"], "models": {"provider": "openai-responses", "model": "gpt-6-luna"},
            "decks": None, "rng": None,
            "bindings": {"fingerprints": profile["catalog"]["bindings"], "pilotFingerprint": profile["catalog"]["pilotFingerprint"], "componentDigest": profile["catalog"]["componentDigest"]},
            "config": {"purpose": profile["purpose"], "paidProvidersEnabled": True, "gameAiMode": "engine", "manualOnly": True,
                       "manualUncapped": True, "accountsEnabled": False, "redisEnabled": profile["settings"]["redisEnabled"], "runtimeProfileSha256": digest(profile)}}
