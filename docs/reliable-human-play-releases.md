# Reliable human play, matched releases, and live bindings

This release implements roadmap stages 1–3. Local model providers, batch games,
training, and optional Redis remain outside this release. A real completed human
Luna game and same-seat browser reconnect are acceptance checks, not claims that
can be established by fake-provider or lifecycle fixtures.

## Operating a release

Run `python3 scripts/commander.py --help` from a checked-out Commander Gym repository.
`git pull` updates these tools; it never replaces the running server. Application
updates use one reviewed bundle containing exact native, adapter, frontend,
Python/JVM dependency, service configuration, and catalog inventories. No build or
provider request happens during installation. A branch name is not a release pin.

```sh
python3 scripts/commander.py verify --bundle /path/to/private-release --sha256 MANIFEST_SHA256
sudo python3 scripts/commander.py install --bundle /path/to/private-release --sha256 MANIFEST_SHA256
sudo python3 scripts/commander.py update --bundle /path/to/private-release --sha256 MANIFEST_SHA256
sudo python3 scripts/commander.py status
sudo python3 scripts/commander.py diagnostics
sudo python3 scripts/commander.py rollback
sudo python3 scripts/commander.py recover
```

The SHA is the reviewed `release.json` digest, obtained separately from the bundle.
The bundle contains private catalog data and host configuration: keep it private.
It contains no provider credentials. `pack --runtime ID --output DIRECTORY`
packages an already qualified, approved installed tuple for transport; it does not
manufacture qualification or approval. New-host profiles have previous ID zero and
sequence 1; update approvals identify the current runtime and next sequence.
Qualification for a new activation must be less than 24 hours old.

A fresh installation requires Linux x86-64 with systemd, Python 3, Git, authenticated
Tailscale, a clean initial binding checkout, and the existing provider credential
in the protected destination documented by the profile. The bundled Python/JVM,
proxy executable, and Python packages are pinned by inventory. No pip, Gradle, or
npm runs on the installation path. Service UIDs in the prepared host profile must
be free or match existing named accounts; the installer never reassigns accounts.
Tailscale Serve provides private HTTPS. This installer does not enable Funnel.

Install creates the service accounts, immutable artifact trees, recording/state
directories, callback token, units, and admission timer. The existing provider key
is loaded by systemd only into the sidecar. A prepared installation interrupted
before admission can be rerun with the same bundle. Once installation commits,
use `recover` for admission recovery and `update` for another release.

Update stages verified files without changing selected services, then waits up to
one hour for games, pending activities, admissions, and recording writes to reach
zero. `--wait-seconds 0` makes a busy server return immediately. It never clears a
lobby or terminates a game. The locked transaction repeats idle and identity checks,
requests recorder drain acknowledgment, reconciles capture bytes, and starts the
candidate closed. Admission opens after health and the durable commit.

A candidate that fails before admission is stopped and the previous release is
restored only if capture bytes still match the acknowledged drain inventory. An
unconfirmed commit/resume, changed recordings, or an interrupted transaction stays
held for review. The tool never guesses that such a rollback is safe. Explicit
`rollback` selects only the recorded previous approved tuple, preserves the
recording schema, and consumes a new monotonic attempt sequence. Old artifacts and
recordings are retained. Schema 1 is the only supported storage schema; future
migration formats must be implemented and qualified before installation accepts them.

Diagnostics allowlist release pins, counters, readiness and admission flags. They
do not dump environment variables, keys, logs, deck lists, or game payloads.

## Git-backed bindings

The optional `liveCatalog` profile field declares:

```json
{
  "checkoutRoot": "/srv/commander-bindings/checkout",
  "snapshotRoot": "/var/lib/commander-bindings",
  "roster": "roster.json",
  "pollSeconds": 5
}
```

Use the real roster path from the private catalog. Clone the private repository as
its normal operator into the checkout directory. Keep Git credentials with that
operator, outside both the checkout and snapshot store. The initial checkout must
match the release's qualified catalog. Later updates are simply:

```sh
git -C /srv/commander-bindings/checkout pull --ff-only
```

The publisher observes only clean committed trees, disables Git hooks/fsmonitor,
rejects executable files, symlinks and special files, and bounds tree size and file
count. It validates canonical identities, exact deck references, and installed
pilot fingerprints. New code/provider implementations cannot be installed by
pulling binding data. New catalogs also pass the running native server's Commander
deck validator over loopback before publication. While the server is unavailable,
the previous catalog stays selected.

Publication uses a fsynced immutable snapshot and atomic pointer. A failed or dirty
pull leaves the previous working snapshot in place. The publisher records a bounded
status in `/var/lib/commander-bindings/status.json`; its journal contains status
names, not deck contents. The sidecar and JVM keep their last valid catalogs during
reload failures. Waiting host lobby selectors refresh every five seconds while the
page is visible. A removed explicit selection is shown as unavailable and must be
reselected; it is never silently substituted.

Each created seat holds its exact snapshot/profile. Recording provenance includes
the snapshot digest and Git commit. Retained snapshots let the recorder resolve old
seats after a restart; no automatic snapshot deletion is performed. Editing a live
checkout does not modify a running game. Normal browser refresh/reconnect uses the
existing seat recovery protocol; process-restart survival of an active game is not
promised by this release. Updates wait for idle.

## Verification boundaries

- Python tests cover clean/dirty/tampered Git snapshots, publication races, installed
  pilots, native deck rejection, old-seat lineage, bundle tampering and custody,
  idle waits, admission recovery and rollback safety.
- JVM tests cover refresh, old-controller deck retention, malformed catalogs and
  sidecar outages. Frontend build and tests cover the unchanged selection contract.
- The offline `release-install` CI container runs the actual installer and systemd
  units with synthetic lifecycle/provider/recorder processes. It checks installation,
  cold service restart and automatic admission, busy-update refusal, promotion,
  explicit rollback and failed-candidate recovery. It does not qualify real games,
  native rules, the dependency closure, or actual TLS ingress.
- A deployment still requires matched artifact inspection and the existing isolated
  runtime qualification gates, followed by owner approval of the concrete package.
  After deployment, record the human game/reconnect and idle reboot acceptance.
