# Per-game usage display — November 9, 2026 (Pacific)

Branch `feat/panel-batches`; installed panel/supervisor source commit
`5d7b10409adfbbbb4d7fc18b82c3953d3225651e`.

Game detail has a Tokens & cost section showing reported total/input/output
tokens, estimated/reserved cost, provider request admissions, used/reserved
tokens, unsettled reservations and snapshot time. Batch attempts also show
per-game usage, with cumulative batch observations in a separate disclosure.
Cost includes unresolved reservations and is explicitly not billed cost.

Before starting any role for a new attempt, the supervisor persists its starting
ledger balances. Live and final observations retain cumulative balances, now
including input/output totals and observation time. The read API computes
per-game differences from those saved starting balances. Restarting preserves
the baseline. A legacy first attempt can be attributed to the zero-initialized
batch ledger. Later legacy games without baselines remain unavailable; missing
or regressing counters remain unknown. No historical ledgers or captures are
rewritten. The existing owner-authenticated game_context API supplies usage,
with the existing game-ID and owning-attempt path validation unchanged.

54 focused batch, panel and observer tests pass; JavaScript syntax and diff
checks pass. Tests cover separate usage for two games, reopen persistence,
legacy first-game attribution, unavailable later-game attribution and missing
or regressing counters. Live HTTPS checks confirm the diagnostic game's
904,483 tokens and $0.2288 estimated/reserved, reservation caveats, desktop and
phone layout, reload persistence, and batch per-game summaries. Anonymous game
API access returns 403. No additional paid game was started; workers stay
stopped. Human service invocation IDs, Tailscale Serve JSON and the original
diagnostic capture inventory/hashes match their pre-deployment values.

Example: https://tolaria.taila3c720.ts.net/gym/games/capture-866246574fab0ef0bd2b081e
Browser receipts and screenshots: `/tmp/gym-panel-qa/per-game-usage-*`.

The panel's current symlink and supervisor WorkingDirectory point at the
immutable release above. The provider's previously installed limit/accounting
overlay is unchanged. Configuration additionally pins and verifies the new
batch_store and batch_supervisor module hashes; future reviewed plans retain
those pins. No native/adapter, pilot, credential or recorder changes.

Rollback receipts: `/var/lib/commander-gym-per-game-usage-rollout` (0700).
While isolated workers are stopped and no batch is queued/running, stop only
the batch supervisor; restore config-before.json and
supervisor-drop-before.conf, restore the panel current symlink from
panel-before-release, daemon-reload, start the supervisor and restart only the
panel. Preserve notes, captures, budget ledgers and saved attempt balances.
No Tailscale changes or host reboot are needed.
