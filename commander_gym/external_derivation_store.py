"""Validate and store external-model derivation sidecars."""
from __future__ import annotations
import hashlib, json, os
from .external_derivation import ModelDerivationError, ModelDerivationRecord
from .external_provenance import ExternalProvenanceStore
from .model_lineage import ModelLineageStore
from .storage import LocalArtifactStore, StorageLayout

class ModelDerivationStore:
    def __init__(self, layout: StorageLayout):
        self.layout = layout
        self.artifacts = LocalArtifactStore(layout)
        self.lineage = ModelLineageStore(layout)
        self.external = ExternalProvenanceStore(layout)

    def write(self, record: ModelDerivationRecord):
        record.payload()
        lineage = self.lineage.read_artifact(record.model_lineage_artifact_id)
        if (lineage.model_id, lineage.version) != (record.model_id, record.version):
            raise ModelDerivationError("derived model identity does not match lineage")
        for ref in record.parents:
            source = self.external.read_model_artifact(ref.manifest_artifact_id)
            actual = (source.model_id, source.version, source.model_digest,
                      source.checkpoint_artifact_id)
            expected = (ref.model_id, ref.version, ref.model_digest,
                        ref.checkpoint_artifact_id)
            if actual != expected:
                raise ModelDerivationError("imported model reference does not match manifest")
        payload = json.dumps(record.to_dict(), sort_keys=True,
                             separators=(",", ":"), ensure_ascii=True).encode()
        artifact = self.artifacts.put_bytes(payload)
        ident = hashlib.sha256(record.model_id.encode()).hexdigest()
        rev = hashlib.sha256(record.version.encode()).hexdigest()
        path = self.layout.path("models") / "derivations" / ident[:2] / ident / f"{rev}.json"
        data = json.dumps({"artifact_id":artifact.artifact_id,
                           "derivation_digest":record.derivation_digest},
                          sort_keys=True,separators=(",", ":")).encode()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_bytes() != data:
            raise ModelDerivationError("model id/version already has different derivation")
        if not path.exists():
            tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            try:
                tmp.write_bytes(data)
                os.replace(tmp, path)
            finally:
                if tmp.exists():
                    tmp.unlink()
        return artifact
