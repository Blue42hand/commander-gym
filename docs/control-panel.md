# Commander Gym control panel

A small Python standard-library service serves Overview, History, Game detail,
and Triage at `/gym/`. No frontend runtime, database server or provider access is
required. This is an administrative evidence view, distinct from the existing
masked local display. Existing status endpoints and game producers are unchanged.

## Integration observed on Tolaria, November 9, 2026

| Field | Existing source | Interpretation |
| --- | --- | --- |
| Installed Gym / engine | Native `.native-health.json` | `84f6c3854ef790932142838a26c27b480573edb5` / `12ab2f0ed5bd018334521dc52497e6444f26f5a9` observed in the current process health snapshot. The `current` release symlink is not alone an installed-process claim. |
| Recorder health | `/var/lib/commander-gym/runs/native/.native-health.json` and `/run/argentum-play/.recording-disposition-health.json` | Separate freshness contract, 20 seconds; recording health does not imply recording completeness. |
| Process health | `systemctl is-active` through existing local-display helper | `argentum-play.service`, `argentum-recorder.service`, `argentum-luna-sidecar.service`; active means process only. |
| Running / idle / queued | No complete authoritative persisted source configured | Unknown/unavailable, never inferred from process status, quiet logs, registered source count, or public discovery. No queue count is shown. |
| Capture inventory | Configured native production root, direct child capture directories | 18 captures, 410 files and 5.94 GiB in the initial inventory. Includes a roughly 1 GiB unsealed canonical journal. Older batch, restoration and qualification roots are excluded and coverage states this explicitly. |
| Source identity / participants / revisions / seed | First journal manifest row | Source fields retained; missing deck names and other fields remain unavailable. Seat roster order is preferred where recorded. |
| Result / native and supervisor termination | Finalized manifest outcome | Unknown winner stays unknown; a draw needs an explicit `draw: true`. Verification is displayed separately. |
| Integrity | `inspect_journal`, `verify_finalized_manifest` | Verified only after the existing full verifier succeeds; unchanged evidence reuses a cached result and timestamp. |
| Turn count | No normalized indexed source supplied | Explicitly unavailable. No invented conversion to rounds. |
| Duration | First/last recorded timestamps in a terminal capture | Recorded capture span, not inferred engine wall-clock gameplay time. |
| Reports | Existing versioned `analysis/` text report files and manifest analysis state | Read only; no claim, complete or analysis execution operation. |
| Review | Separate SQLite database | Review disposition is not recorder acknowledgement, historical disposition, replay qualification or dataset eligibility. |
| HTTPS ingress | Existing Serve `https://tolaria.taila3c720.ts.net/` -> `127.0.0.1:8180` | Preserve this root route. Add only `/gym/` -> `http://127.0.0.1:8787/gym/`. |
| Owner | Current Tailscale user identity | Explicit login allowlist, initially `Blue42hand@github`. Missing, duplicate and other identities fail closed for HTML, evidence, reports and writes. Tagged clients have no supported user identity policy. |

Integration decision: preserve strict source modes (0700/0600), including the
existing readers' private-directory checks. Use a dedicated unprivileged panel
account in a minimal filesystem root, with only read-bound evidence and health
mounts. `CAP_DAC_READ_SEARCH` permits reading these existing private files without
ACL or ownership changes; it does not permit writes. No original recordings are
made generally readable. The root exposes `/usr`, the panel code/configuration,
selected evidence/health mounts, and its own writable state. Lifecycle sockets
are inaccessible. The panel does not import credentials or communicate with
producer lifecycle interfaces.

## API and data boundaries

GET routes: `/api/session`, `/api/overview`, `/api/history`, `/api/health`,
`/api/games/<opaque-ref>`, `/api/games/<opaque-ref>/evidence`, and
`/api/games/<opaque-ref>/artifact?name=<allowlisted-relative-name>`, all beneath
`/gym`. The only application mutation is `POST /api/games/<ref>/review`.

Writes require an exact configured HTTPS Origin and an actor-bound CSRF token.
A SQLite `BEGIN IMMEDIATE` transaction checks both review version and observed
source revision. A conflict returns 409 and the browser retains its draft.
Created/updated times, actor, authoritative IDs when present, observed source
revision, finalized artifact hash, evidence anchor, state, tags, finding and next
action are retained, with change history. Irreplaceable notes are in
`reviews.sqlite3`; the separate `index/` directory can be deleted and rebuilt.

Inventory walks only the configured root's direct capture directories, capped at
500 captures and 1000 files per capture. It does not depend solely on finalized
manifest discovery, which would omit damaged, unsealed and legacy captures.
Unsupported metadata produces a panel-local capture reference, never a fabricated
run ID. Partial and unavailable root inventories are explicit. Record metadata is
indexed incrementally in a background thread, not on each browser request.

Verification runs in an isolated subprocess with a 320 MiB address-space limit,
a 30-second CPU limit, a 30-second cooperative work budget, and a 40-second parent
timeout. Its panel-only codec wrappers limit physical and decoded records to
2 MiB, preserving the producers' larger format limits. Over-limit records are
reported as too large, never silently treated as verified. The panel unit is
limited to 512 MiB, 50% of one CPU, and 32 tasks. Unchanged scans are cached. Finalized verification reuses its single existing journal scan; sparse seek offsets are collected during that scan. Smaller captures are verified first after all metadata is published. Verification-budget and baseline replay limitations remain coverage indicators, rather than new urgent incidents.

Evidence uses existing mixed plaintext/compressed record decoding. Each page
consumes at most 200 source records, displays at most 20 excerpts / 128 KiB, and
has a one-second cooperative budget. Excerpts are clipped explicitly. Signed
cursors bind file/offset positions to the capture and source revision. Sparse
seek metadata supports `#event-<sequence>` bookmarks within indexed coverage.
There are at most 8 concurrent HTTP requests. Text artifact views are allowlisted,
server-resolved, sanitized and limited to 512 KiB; binary and large artifacts
are retained for local inspection. Model text is escaped, never active HTML.
Transport fields and credential-shaped text are excluded. Evidence remains
private; the existing masked pilot/status boundary is unchanged.

Browser polling is 5 seconds on Overview and 30 seconds on History/Triage, paused
in hidden tabs with error backoff. Game detail stays on the selected evidence and
retains unsaved notes. Filters/page are in the URL. Review changes never invoke
claim, acknowledgement, repair, finalization, promotion or game control paths.

## Install / rollback

The host-specific installation uses `deploy/panel/commander-gym-web.service.example`
and `panel.example.json`, with explicit paths and owner identity. The root-owned
code release under `/opt/commander-gym-web/` must be built from a recorded commit;
`current` selects that release. Prepare the minimal RootDirectory's `/lib` and
`/lib64` symlinks to `/usr/lib` and `/usr/lib64`, and configure the dedicated
`commander-panel` account and its StateDirectory. Use `systemd-analyze verify`
before starting the new unit. Do not restart any producer unit.

Before modifying Serve, save its exact prior JSON outside the capture roots.
The verified new mapping is:

```sh
sudo tailscale serve --bg --https=443 --set-path=/gym/ http://127.0.0.1:8787/gym/
```

The backend path preserves the application's `/gym` prefix after Serve strips
the mount point. Inspect the returned Serve configuration and confirm that the
unrelated root route still points at port 8180. No Funnel route is added. The
application allowlist remains authoritative even when existing tailnet grants
are broader. The complete tailnet administrative policy is not modified or
claimed audited by this implementation. Tailscale documents trusted identity
headers and tagged-client limitations at
<https://tailscale.com/docs/features/tailscale-serve#identity-headers>.

Rollback only the new route and unit:

```sh
sudo tailscale serve --https=443 --set-path=/gym/ off
sudo systemctl disable --now commander-gym-web.service
```

Check `tailscale serve status --json` against the saved prior configuration. Do
not use `tailscale serve reset`, remove source evidence, or remove annotation
state. For code rollback, stop only the panel, select its prior `current`
release, and restart it. Back up the SQLite database with SQLite's backup API
rather than copying a live database without its WAL. Keep the note backup and
capture inventory. A panel restart is sufficient; no host reboot is required.

## Verification

Run `python3 -B -m unittest discover -s tests -p test_control_panel.py -v` and the
existing `test_game_journal.py` suite. Tests cover completed/failed/interrupted,
unsealed/malformed, compressed/plain, oversized and stale input; persistence and
change history; two-tab conflicts; source revision changes; private identity and
CSRF denial; path traversal and symlink rejection; source byte/metadata identity;
and absence of recorder construction during inspection. Browser checks use only
synthetic fixtures for unsafe HTML and annotation-conflict exercises. Real
source inventories/hashes before and after observation are retained privately
outside capture roots by `deploy/panel/inventory.py`.

Remaining integration limits are deliberately visible: no complete live-game
source, no queue source, unknown indexed turn count, excluded legacy batch roots,
and bounded verification/display of very large individual records. Exact replay
and dataset qualification are never inferred from terminal or finalized state.
