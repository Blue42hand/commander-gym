# Control panel handoff

Branch: `feat/gym-control-panel` in `/home/commander/gym-control-panel`.
Baseline: `84f6c3854ef790932142838a26c27b480573edb5`.
Implementation commit: `git log --format=%H -- commander_gym/control_panel.py`.
Scope: private observation/review panel only. No producer or game-rule edits.

Milestone 1: integration mapped in `docs/control-panel.md`. This execution is on
Tolaria itself, with current installed engine/Gym snapshots and Serve config
inspected. Source mapping and isolation decision recorded. Current source
inventory is native production only; old batch/qualification roots are excluded.

Milestones 2–3: Overview, paginated/filterable History, Game detail, Triage,
bounded evidence/artifact reads, provenance, independent verification badges,
SQLite notes/change history, optimistic conflicts, source-bound review identity,
and investigation brief implemented. Review never alters analysis claims.

Checks: 17 focused backend tests and 20 existing journal tests pass. Synthetic
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
