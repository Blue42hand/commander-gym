# History wall time — November 9, 2026 (Pacific)

Branch `feat/panel-batches`; installed panel commit
`784b9a19d35fdc878ab408e874e80aeebecf696d`.

History now shows game wall time from the batch ledger's saved started_unix
and ended_unix, independently of journal sealing or verification. The game
detail page shows the same wall time and its source, alongside the separate
recorded capture span. Running games show elapsed time only when the existing
live snapshot also identifies that attempt/game with a fresh native observation.
Missing, nonfinite, reversed or absent timestamps remain unavailable; preflight
time is not substituted for actual game start.

Manifest metadata retains validated batch/attempt references, including when
loading older metadata caches. Timing reads use the existing authorized broker
get operation and require matching game ID and owning capture directory.
Only displayed History rows are enriched. Per-batch timing data is cached for
five seconds; broker calls use a 0.5-second timeout and History enrichment has
a one-second request work budget. No supervisor or producer changes.

43 focused panel/action-reader/observer tests pass, plus JavaScript syntax
checks. Coverage includes an unsealed interrupted game, timing cache reuse,
cross-attempt rejection, invalid timestamps, and fresh-vs-stale live elapsed
time. Live HTTPS browser checks confirm the diagnostic game's 461.2-second
wall time renders as 7m 41s in desktop/phone History and game detail.
Receipt: `/tmp/gym-panel-qa/history-wall-time-receipt.json`.

Only the web service restarted. Human native/recorder/sidecar, batch supervisor
and observer invocation IDs are unchanged. No game was launched, no credentials
were loaded and no original capture or annotation was modified. Concurrent
local-inference work remains outside these commits and deployment.

Rollback: restore the panel current symlink from
`/var/lib/commander-gym-wall-time-rollout/panel-before-release`, then restart
only commander-gym-web.service. Preserve notes, evidence and batch ledgers.
