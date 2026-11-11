# Live Overview handoff — November 9, 2026 (Pacific)

Branch/worktree: `feat/panel-batches`, `/home/commander/gym-panel-batches`.
Installed panel/observer commit: `984c3b0d4f348c3e463a0bd7edf9eac31f09af11`.
This handoff is a later documentation-only commit. URL: https://tolaria.taila3c720.ts.net/gym/.

Completed: live Overview implementation and private deployment. Human and batch game services, the batch supervisor, existing worker pins, original recordings, review notes and Tailscale routing were preserved. No game was launched or stopped for this task, and no provider credentials were loaded by the observer.

## Sources and behavior

`commander-gym-observer.service` reads the existing native status sockets for the configured human-play and managed-batch runtimes. Unix peer UIDs are checked. The only native request is `{"protocol":1,"op":"status"}`. Counts include private native sessions; in-flight admissions are displayed separately from active games. Status freshness is checked independently of browser polling.

The collector reads batch metadata through the existing UID-authenticated owner `list` API. It follows up to eight pages (240 batches) within a collection work budget, deduplicates identities, detects a changing inventory and reports partial coverage/unknown queue totals when appropriate. It does not open the control database: SQLite WAL readers may need auxiliary bookkeeping writes, which are intentionally disallowed by the observer's filesystem sandbox. Its access to the entire batch-state directory, provider credential directory and credential mounts is blocked. Only its own snapshot state is writable.

The allowlisted snapshot is `/var/lib/commander-gym-observer/overview.json`, mounted read-only into the panel at `/live/overview.json`. Collection and browser refresh run approximately every five seconds. A 20-second freshness contract applies on the server and in the browser, including network failure or suspended tabs. A stopped batch engine counts as idle only when the fresh supervisor/queue evidence and all owned worker-unit states agree. An active process alone cannot establish idle or progress.

The Overview now shows:

- Native active counts, runner state, independent recorder health, observation ages and collection scope.
- Per-runtime status for human play and managed batches; a stopped batch recorder is explicitly Standby.
- Current managed batch identity, seat-order deck lineup, native turn/observation age, decisions, progress and stable batch/evidence links.
- Explicit queue counts and recent supervisor failures/interruption links. Native result, supervisor disposition, recording integrity and review state remain separate.
- Historical capture totals combined with authoritative managed-attempt totals without counting a batch manifest twice. Partial historical time coverage remains labeled.
- Separate process health and archive coverage. Refresh preserves scroll position and expanded service details. Phone layouts and escaped text are supported.

Human game identities/turns are not exposed by the configured status socket. The existing administrative live-overview HTTP endpoint requires separate native administrative authentication (currently returns 401), so it was not bypassed or reconfigured. Legacy training/gateway sessions remain outside the explicitly displayed production-runtime scope. No engine/adapter/producer changes were needed. Existing batch workers remain pinned to Gym `44d378af25df52f8751b2cb73f1103d253e743f3` and native `12ab2f0ed5bd018334521dc52497e6444f26f5a9`.

## Verification

47 focused Python tests passed across observer, panel and batch behavior. Coverage includes fixed read-only native messages, peer authentication, private-session counts, independent recorder warnings, unavailable/stale/partial sources, queue/worker evidence, active attempts beyond the first history page, changing API inventories and preserved historical semantics.

Browser fixture checks passed live nonzero counts, turn/deck rendering, escaped hostile text, retained disclosure state, phone layout and stale state after network loss. Live owner HTTPS checks passed source agreement, fresh idle counts, recorder Healthy/Standby distinction, advancing snapshots across refresh, process disclosure preservation and phone layout. Anonymous Overview access returns 403. No live running-game or paid batch was created for testing; nonzero game/turn transitions were exercised with fixtures.

Actual observed idle state: both configured runtimes 0 active, runner Idle, human recorder Healthy, batch recorder Standby, queue 0, complete runtime observations. The four existing human-service/batch-supervisor invocation IDs were identical before and after deployment. Tailscale Serve JSON was unchanged. Observer memory at one snapshot was 20,484,096 bytes; this is not a general throughput benchmark.

Private deployment backups and service/Serve checks: `/var/lib/commander-gym-overview-rollout`.
Browser receipts/screenshots: `/tmp/gym-panel-qa/overview-live-*` and `overview-live-receipt.json`.
The isolated fixture web server was stopped.

## Rollback and next action

Disable/stop only `commander-gym-observer.service`. Restore the saved panel unit, panel configuration and previous release target from `/var/lib/commander-gym-overview-rollout`, daemon-reload and restart only `commander-gym-web.service`. Keep all game services, batch supervisor, recordings, annotations and Tailscale routes intact. The older panel remains usable without the new snapshot.

Preparation is implemented by `deploy/panel/install_observer.py --release /opt/commander-gym-web/<commit>`. It prepares only the collector and panel mount/config; activation starts/restarts the observer and panel only.

Next action: reload Overview. When Noah starts a reviewed batch, its native count and managed game progress will appear automatically. Extending human-game detail requires a separately authorized native administrative summary source; it is not needed for the live counts now deployed.
