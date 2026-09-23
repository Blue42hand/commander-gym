"""Location-independent Commander Gym storage primitives.

This module deliberately owns storage placement rather than game, pilot, or
training semantics. Higher-level records should store stable artifact IDs and
ask a storage backend to resolve them to physical locations.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
from typing import Any, Mapping

STORAGE_LAYOUT_VERSION = 1

DURABLE_STORAGE_ROUTES = (
    "artifacts",
    "runs",
    "annotations",
    "datasets",
    "models",
    "catalog",
)
EPHEMERAL_STORAGE_ROUTES = ("logs", "cache")
STORAGE_ROUTES = DURABLE_STORAGE_ROUTES + EPHEMERAL_STORAGE_ROUTES

_ARTIFACT_ID_RE = re.compile(r"^sha256:([0-9a-f]{64})$")


class StorageError(ValueError):
    """Raised when storage configuration or artifact identity is invalid."""


def _coerce_path(value: object, *, field: str) -> Path:
    if not isinstance(value, (str, os.PathLike)):
        raise StorageError(f"{field} must be a filesystem path")
    path = Path(value).expanduser()
    if not str(path):
        raise StorageError(f"{field} must not be empty")
    return path


@dataclass(frozen=True)
class StorageLayout:
    """Physical route configuration for one Commander Gym node.

    Route paths may move between hosts without changing durable artifact IDs.
    Relative route overrides are resolved beneath ``root``; absolute overrides
    may point at independently mounted local storage.
    """

    root: Path
    routes: Mapping[str, Path]
    version: int = STORAGE_LAYOUT_VERSION

    @classmethod
    def create(
        cls,
        root: str | os.PathLike[str],
        *,
        overrides: Mapping[str, str | os.PathLike[str]] | None = None,
    ) -> "StorageLayout":
        base = _coerce_path(root, field="storage root")
        supplied = dict(overrides or {})
        unknown = sorted(set(supplied) - set(STORAGE_ROUTES))
        if unknown:
            raise StorageError(f"unknown storage route(s): {', '.join(unknown)}")

        routes: dict[str, Path] = {}
        for name in STORAGE_ROUTES:
            raw = supplied.get(name, name)
            path = _coerce_path(raw, field=f"storage route {name}")
            routes[name] = path if path.is_absolute() else base / path
        return cls(root=base, routes=routes)

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any]) -> "StorageLayout":
        """Load the storage section used by deployment/application config.

        Expected shape::

            {
              "root": "/srv/commander-gym/data",
              "routes": {"models": "/mnt/models"}
            }

        The schema is intentionally small so deployment systems can supply it
        through JSON/YAML/TOML/environment adapters without changing storage
        semantics.
        """

        if not isinstance(config, Mapping):
            raise StorageError("storage config must be a mapping")
        if "root" not in config:
            raise StorageError("storage config requires root")
        routes = config.get("routes", {})
        if routes is None:
            routes = {}
        if not isinstance(routes, Mapping):
            raise StorageError("storage routes must be a mapping")
        return cls.create(config["root"], overrides=routes)

    def path(self, route: str) -> Path:
        try:
            return self.routes[route]
        except KeyError as exc:
            raise StorageError(f"unknown storage route: {route}") from exc

    def ensure_directories(self) -> None:
        for path in dict.fromkeys(self.routes.values()):
            path.mkdir(parents=True, exist_ok=True)

    def durable_paths(self) -> tuple[Path, ...]:
        """Return the explicitly durable backup scope."""

        return tuple(self.path(name) for name in DURABLE_STORAGE_ROUTES)

    def ephemeral_paths(self) -> tuple[Path, ...]:
        return tuple(self.path(name) for name in EPHEMERAL_STORAGE_ROUTES)

    def to_dict(self) -> dict[str, Any]:
        """Serialize deployment placement only; never use this as artifact identity."""

        return {
            "version": self.version,
            "root": str(self.root),
            "routes": {name: str(self.path(name)) for name in STORAGE_ROUTES},
        }


@dataclass(frozen=True)
class StoredBlob:
    """Location-independent reference to bytes stored by content digest."""

    artifact_id: str
    size_bytes: int

    def validate(self) -> str:
        digest = parse_artifact_id(self.artifact_id)
        if type(self.size_bytes) is not int or self.size_bytes < 0:
            raise StorageError("size_bytes must be a non-negative integer")
        return digest


def artifact_id_for_bytes(payload: bytes) -> str:
    if not isinstance(payload, bytes):
        raise StorageError("artifact payload must be bytes")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def parse_artifact_id(artifact_id: str) -> str:
    if not isinstance(artifact_id, str):
        raise StorageError("artifact_id must be a string")
    match = _ARTIFACT_ID_RE.fullmatch(artifact_id)
    if match is None:
        raise StorageError("artifact_id must be sha256:<64 lowercase hex chars>")
    return match.group(1)


class LocalArtifactStore:
    """Filesystem-first content-addressed blob store.

    The physical path is derived only at resolution time. Durable records
    should retain ``sha256:...`` IDs rather than this path.
    """

    def __init__(self, layout: StorageLayout):
        if not isinstance(layout, StorageLayout):
            raise StorageError("layout must be a StorageLayout")
        self.layout = layout

    def path_for(self, artifact_id: str) -> Path:
        digest = parse_artifact_id(artifact_id)
        return self.layout.path("artifacts") / "sha256" / digest[:2] / digest[2:]

    def put_bytes(self, payload: bytes) -> StoredBlob:
        artifact_id = artifact_id_for_bytes(payload)
        target = self.path_for(artifact_id)
        target.parent.mkdir(parents=True, exist_ok=True)

        if target.exists():
            existing = target.read_bytes()
            if existing != payload:
                raise StorageError("artifact digest collision or corrupted stored blob")
            return StoredBlob(artifact_id=artifact_id, size_bytes=len(payload))

        temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        except FileExistsError:
            if not target.exists() or target.read_bytes() != payload:
                raise StorageError("concurrent artifact write did not resolve safely")
        finally:
            if temporary.exists():
                temporary.unlink()

        return StoredBlob(artifact_id=artifact_id, size_bytes=len(payload))

    def read_bytes(self, artifact_id: str) -> bytes:
        target = self.path_for(artifact_id)
        try:
            payload = target.read_bytes()
        except FileNotFoundError as exc:
            raise StorageError(f"artifact not found: {artifact_id}") from exc
        if artifact_id_for_bytes(payload) != artifact_id:
            raise StorageError(f"artifact digest mismatch: {artifact_id}")
        return payload
