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

## Service doctor

`commander_gym.service_doctor` validates the runtime dependencies of a portable node without making gameplay or model-policy decisions.

It checks the configured Argentum endpoint through the existing orchestration compatibility contract: `/health`, `/status`, and `/schema-hash` must agree, the service must identify as `argentum-gym-server`, and optional expected schema/build revisions must match exactly.

```bash
export COMMANDER_GYM_ARGENTUM_URL=http://127.0.0.1:8082
python -m commander_gym.service_doctor \
  --expected-schema-hash '<expected-schema-hash>'
```

If `COMMANDER_GYM_MODEL_URL` is configured, the same command also performs a transport-only HTTP probe against `models` beneath that base URL by default. For example, an OpenAI-compatible local endpoint configured as `http://127.0.0.1:11434/v1` is probed at `/v1/models`. `COMMANDER_GYM_MODEL_TOKEN` is sent as a bearer credential when configured but is never included in the JSON result. Use `COMMANDER_GYM_MODEL_HEALTH_PATH` or `--model-health-path` when a provider exposes a different safe read-only probe path.

The model probe deliberately does **not** inspect model identities, choose a provider, or decide whether a particular model is suitable for a pilot. Those remain pilot/provider concerns. A model service that is not configured is reported as disabled rather than guessed.

The storage and service doctors are separate so a node can initialize/check disk placement without requiring live network services, and service compatibility can be checked without mutating storage.

## Reproducible node package

`commander_gym.node_package` renders the service scaffolding for one concrete host from a small JSON deployment manifest. The manifest pins the exact Commander Gym and Argentum git revisions, selects the service account, points at the gateway credential, supplies the portable storage layout, and may optionally describe a local inference process.

Start from `deploy/node/package.example.json`, replace the example revisions and paths, then render a package directory:

```bash
python -m commander_gym.node_package \
  --config /etc/commander-gym/package.json \
  --output /etc/commander-gym/rendered
```

The rendered directory contains:

- `node-package.json` — normalized deployment metadata with exact source revisions;
- `storage-layout.json` — the physical storage placement supplied to Commander Gym services;
- `run-argentum-gym.sh` — a pinned Argentum service runner;
- `run-commander-gym-gateway.sh` — a pinned Commander Gym gateway runner;
- `systemd/argentum-gym.service` and `systemd/commander-gym-gateway.service`;
- when `local_inference` is configured, a provider-neutral local inference runner and systemd unit.

Rendering is deterministic for the same config and output location. Checkout roots, data roots, mount points, service accounts, ports, and optional local inference commands are deployment choices rather than Commander Gym artifact identity. A node moved to a new host/path may render new service scaffolding without rewriting Deck/Binding/run/dataset/model identities or content-addressed artifact IDs.

The renderer requires full 40-character git SHAs rather than branch names so a service package cannot silently drift when a checkout moves. Existing source-side service runners still fail closed if the checked-out revision does not match the rendered package.

The checked-in `deploy/systemd/*.service.example` files remain development-host examples. Portable installations should render units from `commander_gym.node_package` rather than treating `/srv/...` paths as architecture.

## Responsibility split with #53

Issue #74 owns storage placement, resolution, durability boundaries, capacity/health checks, service compatibility checks, and portable packaging.

Issue #53 owns the meaning and schema of run evidence, annotations, datasets, lineage, and per-run evidence accounting. Those records may use the storage primitives here, but the storage layer must not invent a competing evidence ontology.

## Next #74 slices

After the layout, health checks, and first reproducible service package land, the remaining work includes:

- route application in current writers/runners as their #5/#53 schemas stabilize;
- optional accelerator-specific availability checks where a local provider adapter exposes them cleanly;
- per-run byte accounting hooks supplied to #53;
- installation/clean-host qualification of the rendered package;
- the moved-data-root end-to-end qualification.
