# Portable storage layout

Commander Gym durable identity must not depend on the hostname, filesystem root, mount path, Linode, Cloudflare, Tailscale, or any other deployment choice.

`commander_gym.storage` provides the first filesystem-backed storage primitive for issue #74. It deliberately owns **placement**, not game, pilot, evidence, annotation, or dataset semantics.

## Configuration

A node has one storage root and may override individual routes:

```json
{
  "root": "/srv/commander-gym/data",
  "routes": {
    "models": "/mnt/large-model-store",
    "cache": "/var/cache/commander-gym"
  }
}
```

Relative route overrides are resolved beneath `root`. Absolute overrides may point at separately mounted local storage.

Default routes:

- durable: `artifacts/`, `runs/`, `annotations/`, `datasets/`, `models/`, `catalog/`;
- regenerable/ephemeral: `logs/`, `cache/`.

Backups should include the durable routes. Cache and ordinary transient logs are explicitly outside the durable backup contract unless an operator deliberately promotes a particular artifact into durable storage.

## Location-independent artifact IDs

The local artifact store uses content IDs of the form:

```text
sha256:<64 lowercase hex characters>
```

Records should retain that ID, not an absolute path. The filesystem backend resolves the ID at access time beneath the configured `artifacts` route. Moving/copying the storage root to a different host or path therefore leaves the artifact ID unchanged.

The current filesystem mapping is an implementation detail:

```text
artifacts/sha256/ab/cdef...
```

Higher-level Commander Gym code must not persist this path as artifact identity.

## Backend boundary

The initial backend is a local filesystem because it works for a single server, a large directly attached disk, or any NAS/object-storage gateway mounted by the host. Commander Gym does not encode NFS, SMB, ZFS, or vendor-specific storage behavior into application records.

A future object-store backend should preserve the same location-independent IDs and route semantics.

## Storage doctor

`commander_gym.storage_doctor` checks the physical storage contract without interpreting any game or learning records. It verifies that every configured route exists, performs an actual temporary write/delete probe, records filesystem capacity, and can enforce a minimum free-space threshold.

Initialize and check a new default layout:

```bash
python -m commander_gym.storage_doctor \
  --root /srv/commander-gym/data \
  --create
```

Check a node with route overrides and require at least 100 GiB free on every configured route:

```bash
python -m commander_gym.storage_doctor \
  --root /srv/commander-gym/data \
  --route models=/mnt/models \
  --route cache=/var/cache/commander-gym \
  --min-free-bytes 107374182400
```

The command emits JSON and exits nonzero if a route is missing, cannot be written, cannot be inspected for capacity, or falls below the configured threshold. `--create` only creates configured directories; the write probe leaves no durable artifact behind.

Service/provider checks belong in later #74 doctor slices. Keeping those separate avoids turning storage health into an implicit model-provider or evidence-schema contract.

## Responsibility split with #53

Issue #74 owns storage placement, resolution, durability boundaries, capacity/health checks, and portable packaging.

Issue #53 owns the meaning and schema of run evidence, annotations, datasets, lineage, and per-run evidence accounting. Those records may use the storage primitives here, but the storage layer must not invent a competing evidence ontology.

## Next #74 slices

After the layout and storage-health primitives land, the remaining work includes:

- route application in current writers/runners as their #5/#53 schemas stabilize;
- service/provider `doctor` checks for Argentum/schema compatibility and configured model endpoints;
- per-run byte accounting hooks supplied to #53;
- reproducible node packaging/service scaffolding for Argentum + Commander Gym with optional local inference;
- a clean-host and moved-data-root end-to-end qualification.
