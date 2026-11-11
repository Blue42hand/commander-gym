# Control panel handoff

Branch: `feat/gym-control-panel` in `/home/commander/gym-control-panel`.
Baseline: `84f6c3854ef790932142838a26c27b480573edb5`.
Implementation commit: `458aa2ab234ea1a8018180bd638ce963a842bd44`.
Scope: private observation/review panel only. No producer or game-rule edits.

Milestone 1: integration mapped in `docs/control-panel.md`. This execution is on
Tolaria itself, with current installed engine/Gym snapshots and Serve config
inspected. Source mapping and isolation decision recorded. Current source
inventory is native production only; old batch/qualification roots are excluded.

Milestones 2–3: Overview, paginated/filterable History, Game detail, Triage,
bounded evidence/artifact reads, provenance, independent verification badges,
SQLite notes/change history, optimistic conflicts, source-bound review identity,
and investigation brief implemented. Review never alters analysis claims.

Checks: 18 focused backend tests and 20 existing journal tests pass. Synthetic
Chromium checks pass for desktop Overview/History/Game detail/Triage, deck search,
escaped model/log HTML, evidence anchor selection, review save, two-tab conflict
preserving draft, and 390px phone layout without horizontal page overflow.

Milestone 4: deployment validation is recorded below after installation. Exact
Serve-before config and full source inventory/hashes are retained root-private
at `/var/lib/commander-gym-panel-rollout/`. Annotation store is outside captures.
No host reboot or producer restart is part of deployment.

Remaining limits: no complete authoritative live-game/queue source; no normalized
indexed turn count; out-of-scope legacy batch roots; resource-limited verification
and display may explicitly skip large individual records or exceed scan budgets.
Full tailnet administrative grants are not available through local status and
are not claimed audited. Owner identity is enforced by the application regardless
of broader existing grants. Owner phone/computer interaction is a separate
acceptance step after the URL is delivered.

Next action: follow the recorded deployment receipt and use the private URL from
an untagged owner Tailscale device. Extend sources only from a verified existing
read contract; do not query lifecycle ownership interfaces or infer idle from a
public lobby list.

## Deployment receipt — November 9, 2026

Deployed code commit: `458aa2ab234ea1a8018180bd638ce963a842bd44`.
URL: <https://tolaria.taila3c720.ts.net/gym/>.
Unit: `commander-gym-web.service`, enabled and active; loopback port 8787.
Account: `commander-panel`, isolated filesystem root and read-only evidence mounts.
Code: `/opt/commander-gym-web/current` selects the root-owned commit release.
Notes: `/var/lib/commander-gym-panel/reviews.sqlite3`; rebuildable cache in `index/`.

Milestone 4 completed within the documented source limits. Authenticated HTTPS
from the owner identity works. Missing and unrecognized identities are denied.
The original Serve configuration equals the current configuration after removing
only the new `/gym/` handler. The game client's root route is unchanged.

The final inventory includes all 18 configured captures: 7 fully verified under
the panel limits, 10 explicitly verification-budget limited, and 1 record-size
limited. These latter badges are coverage limits, not assertions of corruption.
No normalized turn count or complete authoritative live-game list is configured;
those values are explicitly unknown. Initial inventory is complete; verification
coverage is intentionally distinct. Older batch/qualification roots are excluded.

18 focused backend tests plus 20 existing journal tests pass. Synthetic browser
checks cover all four views, search, escaped logs, evidence anchors, review saves,
two-tab conflict/draft preservation, and 390px phone layout. Live browser checks
confirm HTTPS access, 18 real records, private evidence, and phone detail layout.
A separate client check confirms that source freshness expires independently of
polling and that the page has no CSP style violations.

An explicitly labeled automated observation was saved on the unsealed capture
as Needs follow-up. Its exact note, state, actor, source binding and version were
recovered after service restart. The producer invocation IDs stayed unchanged.
All 410 original source files retained identical SHA256, byte count, mtime, mode,
UID and GID in the before/after inspection-and-triage comparison. In the panel's
filesystem view, source evidence is not writable; panel state is writable;
producer credential directories and lifecycle sockets are inaccessible.

Five live Overview reads during indexing took 19–91 ms; these are a small
observed sample, not a latency guarantee. Cgroup memory samples were about
25–60 MiB, with the final cached restart reporting a 30 MiB peak. The unit's
512 MiB / 50% CPU limits remain explicit. The final cache avoids rescanning
unchanged journals on requests or restart.

Private receipts, exact Serve-before/after JSON, original file inventories,
annotation acceptance data and SQLite backups remain at
`/var/lib/commander-gym-panel-rollout/` (root-only). A current note backup also
exists at `/var/lib/commander-gym-panel/reviews-backup-20261109.sqlite3`.
Rollback is documented in `docs/control-panel.md`; disable only the panel's
`/gym/` Serve handler and unit, retaining annotations, backups and all evidence.
No host reboot or producer restart was performed.

Next action: Noah opens the URL from an untagged owner Tailscale computer or
phone for personal acceptance. Future source additions must resolve the remaining
live-state, turn-count and legacy-inventory limits through existing read contracts.
