"""Credential-separated launch specifications for a sealed manual runtime.

This module neither installs units nor starts games. Production entry points
must first load protected exact receipts and call precredential_validate; only
then may the sidecar read its private systemd credential copies.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import stat
from typing import Callable

from .manual_runtime_profile import (
    ManualRuntimeError, ValidatedRuntime, decode, digest, protected_bytes,
    recording_pins, relative_path, require, validate_profile, verify_artifacts,
)


@dataclass(frozen=True)
class LaunchSpec:
    arguments: tuple[str, ...]
    environment: dict[str, str] = field(repr=False)


def _only_file(profile: dict, role: str) -> Path:
    artifact = profile["artifacts"][role]
    require(len(artifact["files"]) == 1, "single_artifact_required")
    return Path(artifact["root"]) / next(iter(artifact["files"]))


def dependency_lock(profile: dict) -> dict:
    artifact = profile["artifacts"]["dependencies"]
    require("runtime-lock.json" in artifact["files"], "dependency_lock_required")
    lock = decode(protected_bytes(Path(artifact["root"]) / "runtime-lock.json", uid=artifact["uid"]))
    require(set(lock) == {"schemaVersion", "python", "java", "pythonPath", "packages"}
            and type(lock["schemaVersion"]) is int and lock["schemaVersion"] == 1, "dependency_lock_fields")
    for key in ("python", "java"):
        relative_path(lock[key])
        require(lock[key] in artifact["files"], "interpreter_not_sealed")
    require(type(lock["pythonPath"]) is list and lock["pythonPath"], "python_path_required")
    for path in lock["pythonPath"]:
        relative_path(path)
        require(any(name.startswith(path + "/") for name in artifact["files"]), "python_path_not_sealed")
    packages = lock["packages"]
    require(type(packages) is dict and "openai" in packages and packages, "sdk_lock_required")
    for name, row in packages.items():
        require(type(name) is str and name and type(row) is dict and set(row) == {"version", "wheelSha256"}
                and type(row["version"]) is str and row["version"] and type(row["wheelSha256"]) is str
                and len(row["wheelSha256"]) == 64 and all(c in "0123456789abcdef" for c in row["wheelSha256"]), "package_lock_required")
    if profile['purpose'] == 'native-manual-play':
        require(packages['openai']['version'] == '3.27.0', 'qualified_sdk_version')
        require(lock['python'].startswith('python/') and lock['java'].startswith('java/'), 'self_contained_interpreters')
        for name, row in packages.items():
            wheels = [file for file, sha in artifact['files'].items() if file.startswith('wheels/') and file.endswith('.whl') and sha == row['wheelSha256']]
            require(len(wheels) == 1, 'sealed_package_wheel_required')
    return lock


def _runtime(profile: dict) -> tuple[Path, dict]:
    validate_profile(profile)
    return Path(profile["artifacts"]["dependencies"]["root"]), dependency_lock(profile)


def native_spec(runtime: ValidatedRuntime, *, credential_directory: Path) -> LaunchSpec:
    profile = runtime.profile
    root, lock = _runtime(profile)
    require(credential_directory.is_absolute(), "credential_directory_required")
    # No token value in argv/env. Spring reads only the named private configtree.
    env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "SERVER_ADDRESS": "127.0.0.1", "SERVER_PORT": "18080",
           "APP_VERSION": profile["engineSha"], "NATIVE_RELEASE_ID": runtime.runtime_id,
           "NATIVE_GYM_SHA": profile["gymSha"], "NATIVE_LIFECYCLE_ENABLED": "true",
           "NATIVE_LIFECYCLE_SOCKET": profile["paths"]["lifecycleSocket"], "NATIVE_UPDATER_USER": "root",
           "NATIVE_RECORDING_SCHEMA_VERSION": str(profile["recordingSchemaVersion"]),
           "GAME_AI_MODE": "engine", "GAME_AI_ENABLED": "true", "GAME_DEBUG_MODE": "false",
           "GAME_DEV_ENDPOINTS_ENABLED": "false", "GAME_AI_INSIGHT_ENABLED": "false",
           "GAME_EASTER_EGGS_ENABLED": "false", "GAME_TOURNAMENT_SIMULATE_AI_MATCHES": "false",
           "ACCOUNTS_ENABLED": "false", "CACHE_REDIS_ENABLED": "false",
           "COMMANDER_GYM_ENABLED": "true", "COMMANDER_GYM_MANUAL_ONLY": "true",
           "COMMANDER_GYM_SIDECAR_URL": "http://127.0.0.1:8083", "COMMANDER_GYM_SIDECAR_TIMEOUT_MS": "120000"}
    args = (str(root / lock["java"]), "-Xms512m", "-Xmx6g",
            "-Dcommander-gym.recorder.socket=" + str(Path(profile["paths"]["recorderSocket"]).parent / "native.sock"),
            "-Dgame.recording.root=" + profile["paths"]["recordingRoot"],
            "-Dgame.recording.engine-revision=" + profile["engineSha"],
            "-Dlogging.level.com.wingedsheep.gameserver.handler.ConnectionHandler=WARN",
            "-Dlogging.level.com.wingedsheep.gameserver.session.ZombieSessionSweeper=WARN",
            "-Dlogging.level.com.wingedsheep.gameserver.websocket.GameWebSocketHandler=INFO",
            "-Dlogging.level.com.wingedsheep.gameserver.persistence.SessionRecoveryService=WARN",
            "-jar", str(_only_file(profile, "server")),
            "--spring.config.import=configtree:" + str(credential_directory) + "/")
    require(not any("OPENAI" in key or "TOKEN" in key for key in env), "native_secret_environment")
    return LaunchSpec(args, env)


def parse_existing_key(text: str) -> str:
    for line in text.splitlines():
        line = line.strip().removeprefix("export ")
        if line.startswith("OPENAI_API_KEY="):
            key = line.split("=", 1)[1].strip().strip("\"'")
            if key:
                return key
    raise ManualRuntimeError("provider_key_entry_missing")


def _credential(directory: Path, name: str, *, uid: int, limit: int) -> str:
    relative_path(name)
    require("/" not in name, "credential_name")
    info = directory.lstat()
    require(stat.S_ISDIR(info.st_mode) and not directory.is_symlink()
            and info.st_uid in (0, uid) and info.st_mode & 0o022 == 0, "credential_directory_custody")
    path = directory / name
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and not path.is_symlink()
            and info.st_uid in (0, uid) and info.st_mode & 0o077 == 0, "credential_file_custody")
    raw = protected_bytes(path, uid=info.st_uid, max_bytes=limit)
    try:
        return raw.decode()
    except UnicodeError as error:
        raise ManualRuntimeError("credential_encoding") from error


def sidecar_environment(runtime: ValidatedRuntime, *, credential_directory: Path, uid: int,
                        credential_reader: Callable = _credential) -> dict[str, str]:
    # Runtime is immutable; reverify executable/config/catalog bytes BEFORE reading
    # either credential. The root transaction must also validate BEFORE systemd
    # loads credentials, which occurs earlier than this Python function.
    profile = runtime.profile
    verify_artifacts(profile, roles=frozenset({"sidecar", "dependencies", "launcher", "catalog"}))
    dependency_root, lock = _runtime(profile)
    require(uid == profile["identities"]["sidecarUid"], "sidecar_identity")
    key = parse_existing_key(credential_reader(credential_directory, "openai.env", uid=uid, limit=16384))
    token = credential_reader(credential_directory, "commander-gym.sidecar.token", uid=uid, limit=1024).strip()
    require(32 <= len(token) <= 512 and all(c.isascii() and (c.isalnum() or c in "._~-") for c in token), "sidecar_token_shape")
    catalog = profile["artifacts"]["catalog"]
    # BoundedProcessClient launches a child with sys.executable -m. Supply only
    # byte-sealed source/dependency paths; exclude caller/CWD/user-site injection.
    python_path = [profile['artifacts']['sidecar']['root'], *[str(dependency_root / path) for path in lock['pythonPath']]]
    return {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "OPENAI_API_KEY": key,
            "PYTHONPATH": os.pathsep.join(python_path), "PYTHONNOUSERSITE": "1", "PYTHONSAFEPATH": "1", "PYTHONDONTWRITEBYTECODE": "1",
            "COMMANDER_GYM_SIDECAR_TOKEN": token, "COMMANDER_GYM_SIDECAR_HOST": "127.0.0.1",
            "COMMANDER_GYM_SIDECAR_PORT": "8083", "COMMANDER_GYM_OPENAI_MODEL": "gpt-6-luna",
            "COMMANDER_GYM_OPENAI_TIMEOUT": "90", "COMMANDER_GYM_OPENAI_MAX_ATTEMPTS": "2",
            "COMMANDER_GYM_RECORDER_SOCKET": str(Path(profile["paths"]["recorderSocket"]).parent / "sidecar.sock"),
            "COMMANDER_GYM_RECORDER_UID": str(profile["identities"]["nativeUid"]),
            "COMMANDER_GYM_MANUAL_UNCAPPED": "true", "COMMANDER_GYM_INSTANCE_ROOT": catalog["root"],
            "COMMANDER_GYM_BINDING_CATALOG": str(Path(catalog["root"]) / profile["catalog"]["roster"])}


def sidecar_spec(runtime: ValidatedRuntime, environment: dict[str, str]) -> LaunchSpec:
    profile = runtime.profile
    root, lock = _runtime(profile)
    launcher = Path(profile["artifacts"]["launcher"]["root"])
    require("sidecar_entry.py" in profile["artifacts"]["launcher"]["files"], "sidecar_entry_not_sealed")
    # Guarded entry invokes the merged Binding main in remote-recorder mode.
    # No ambient/site import injection or second capture owner.
    return LaunchSpec((str(root / lock["python"]), "-I", "-S", "-B", str(launcher / "sidecar_entry.py")), dict(environment))


def recorder_pins(runtime: ValidatedRuntime) -> dict:
    return recording_pins(runtime.profile)
