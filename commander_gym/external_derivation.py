"""Lineage sidecar linking a trained model to imported parent checkpoints."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib, json
from typing import Any, Mapping
from .storage import StorageError, parse_artifact_id

KIND = "commander-gym.external-model-derivation"
SCHEMA_VERSION = 1

class ModelDerivationError(ValueError):
    pass

def _required(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ModelDerivationError(f"{label} must be a non-empty string")
    return value

def _artifact(value: Any, label: str) -> str:
    value = _required(value, label)
    try:
        parse_artifact_id(value)
    except StorageError as exc:
        raise ModelDerivationError(f"{label} must be a valid artifact ID") from exc
    return value

def _bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()

@dataclass(frozen=True)
class ExternalParentModelRef:
    manifest_artifact_id: str
    model_id: str
    version: str
    model_digest: str
    checkpoint_artifact_id: str
    relation: str

    def to_dict(self) -> dict[str, Any]:
        _artifact(self.manifest_artifact_id, "manifest_artifact_id")
        _artifact(self.model_digest, "model_digest")
        _artifact(self.checkpoint_artifact_id, "checkpoint_artifact_id")
        _required(self.model_id, "model_id")
        _required(self.version, "version")
        _required(self.relation, "relation")
        return dict(self.__dict__)

@dataclass(frozen=True)
class ModelDerivationRecord:
    model_id: str
    version: str
    model_lineage_artifact_id: str
    parents: tuple[ExternalParentModelRef, ...]
    transformations: tuple[Mapping[str, Any], ...]
    schema_version: int = SCHEMA_VERSION
    kind: str = KIND

    def payload(self) -> dict[str, Any]:
        _required(self.model_id, "model_id")
        _required(self.version, "version")
        _artifact(self.model_lineage_artifact_id, "model_lineage_artifact_id")
        if not self.parents or any(not isinstance(x, ExternalParentModelRef) for x in self.parents):
            raise ModelDerivationError("parents must contain ExternalParentModelRef values")
        if any(not isinstance(x, Mapping) for x in self.transformations):
            raise ModelDerivationError("transformations must contain objects")
        if self.schema_version != SCHEMA_VERSION or self.kind != KIND:
            raise ModelDerivationError("unsupported derivation schema or kind")
        return {
            "model_derivation_schema_version": self.schema_version,
            "kind": self.kind,
            "model_id": self.model_id,
            "version": self.version,
            "model_lineage_artifact_id": self.model_lineage_artifact_id,
            "parents": [x.to_dict() for x in sorted(
                self.parents, key=lambda x:(x.model_id,x.version,x.relation)
            )],
            "transformations": [dict(x) for x in self.transformations],
        }

    @property
    def derivation_digest(self) -> str:
        return "sha256:" + hashlib.sha256(_bytes(self.payload())).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        out = self.payload()
        out["derivation_digest"] = self.derivation_digest
        return out
