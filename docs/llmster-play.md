# llmster for human play and managed batches

Gym uses the existing `lmstudio` provider adapter to call llmster's local Chat
Completions endpoint. Local play requires an explicit endpoint IP and port, an
exact loaded model instance ID, and bounded inference settings. It never loads or
unloads a model, reads an OpenAI key, or falls back to a cloud pilot. Discovery
checks the loaded instance and context budget before each inference attempt.
Native legality, seat masking and the sole recorder remain authoritative.

## Prepare local Bindings

Copy `fixtures/lmstudio-config.example.json` into a private configuration file.
Set `endpoint` to your llmster origin and `model` to the exact loaded instance ID
from its `/api/v1/models` response. The endpoint must use an explicit loopback,
RFC1918, or ULA IP and port; hostnames, public addresses and URL credentials are
rejected. The configured context must fit the conservative prompt byte ceiling,
output token allowance, and template reserve. Models must support the existing
native JSON schemas and disabling reasoning. An incompatible or unloaded model
produces a visible failure rather than a different model or schema downgrade.

Prepare a fresh catalog from an existing approved deck catalog:

```sh
python3 -m commander_gym.local_catalog \
  --catalog /path/to/approved/catalog.json \
  --instance-root /path/to/approved \
  --inference-config /private/llmster.json \
  --output /path/to/new-local-catalog
```

This command makes no inference calls. It checks source artifact hashes,
preserves Deck and DeckKnowledge bytes, and creates new local Pilot and Binding
identities. It writes `catalog.json`, canonical `inference.json`, and a file-hash
`manifest.json`. The JSON result supplies Binding and Pilot fingerprints. The
output must be a new directory outside the source root. The source remains
unchanged. Changing endpoint, model, or inference limits produces a new local
component identity and requires new Bindings and a reviewed plan.

Optional synthetic inference check, which makes a local model request but starts
no native game:

```sh
PYTHONPATH=. python3 scripts/smoke_lmstudio.py --config /private/llmster.json
```

## Human play

The sealed manual runtime already supports an optional `localInference` role.
Use the prepared catalog for its `localCatalog` artifact and copy the canonical
`inference.json` into a separate, disjoint `localConfig` artifact directory.
Include both exact file inventories in the sealed runtime profile. Set:

- `localInference.config` to the complete canonical configuration, including defaults.
- `localInference.catalog.roster` to `catalog.json` and `manifest` to `manifest.json`.
- `bindings` and `pilotFingerprint` to the emitted fingerprints, and
  `componentDigest` to `local_component_ref(LMStudioConfig(**config)).digest`.
- A distinct local UID/GID and local sidecar port `8084`.

The existing launcher renders `argentum-local-sidecar.service` and adds the
local profiles to native human-play discovery. After the matched profile,
artifacts, recorder peers and local isolation/cancellation gates are qualified,
the human selects the displayed local model Binding in the normal native lobby.
The manual sidecar requires the native `manualHumanGame` marker. Its service
loads only the local callback token; provider credentials stay in the Luna role.
The profile and normal runtime qualification remain necessary; generating a
catalog alone does not install or activate human play.

## Managed batches

Prepare an immutable local catalog outside `/home` at a service artifact path,
then supply it to the existing batch preparation command:

```sh
python3 deploy/batches/install.py \
  --release /opt/commander-gym-web/<commit> --gym-sha <commit> \
  --local-catalog /opt/commander-gym-local/<snapshot>
```

The installer validates the exact local-only component references without opening
a listener or making inference calls. It hashes the local catalog files into
runtime verification, publishes local profiles alongside paid profiles, and
prepares `commander-gym-batch-local.service`. That unit has no `LoadCredential`
provider directive and allows network access only to localhost and the selected
inference IP. Preparation does not start services or games. Follow the existing
[batch deployment and rollback procedure](../deploy/batches/README.md).

In **New batch**, choose **Local · llmster**, then select four local Bindings.
Local and paid Bindings cannot be mixed within one batch. The provider spending
fence is zero for local mode; electricity and hardware costs are not estimated.
Review the pinned model and frozen plan before Start. Local choices still have
whole-choice deadlines and cancellation, plus batch-wide request/token and
per-game decision/time fences. Reservations survive failures and missing usage;
valid provider-reported token counts settle them. Missing ledgers fail closed.
Usage remains attributable to each game through the existing before/after
snapshots. The local service requires `managedBatchGame`, preserving the separate
human admission path.

Changing runtime pins requires a fresh or cloned draft. Unfinished games and
failed callbacks retain evidence and never become native terminal results merely
because the supervisor stopped them. A passing source test or synthetic inference
check does not establish a completed native game or human acceptance.
