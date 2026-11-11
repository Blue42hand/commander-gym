# Managed batch controls handoff — November 9, 2026

Branch: `feat/panel-batches`, worktree `/home/commander/gym-panel-batches`.
Implementation and installed panel/worker commit: `44d378af25df52f8751b2cb73f1103d253e743f3`. This handoff is a later documentation-only commit. The original panel worktree and existing human services were preserved.

Status: implementation and private deployment completed. Owner URL: **https://tolaria.taila3c720.ts.net/gym/batches**. No paid batch was started. The existing key is configured through systemd `LoadCredential` only on the paid provider unit; that unit's `ExecMainStartTimestamp` remains empty. No key was copied into browser, panel configuration, source, or command arguments.

## Delivered

- Draft/review/explicit Start of fixed four-seat pods from the installed six-Binding catalog, with frozen source pins, serial concurrency, native-assigned seeds, wall/decision limits, whole-batch estimated USD/request/token fences, and pause/continue failure policy.
- Durable separate SQLite plans, commands, attempts, actor history, optimistic versions and idempotency keys. Pause-after-current, resume unstarted work, finish-and-stop, isolated interruption, clone and linked retry drafts. Changed installed pins require a new reviewed draft; no blind create retries.
- Isolated native, recorder and provider roles with distinct engine/provider identities, private IPC and per-attempt capture roots. The panel sees only the narrow broker socket and read-only captures. Its review database remains separate. Broker restart stops/reconciles the owned runtime and requires explicit Resume.
- Private native lobby status supplies game identities and turn observations for managed games. Whole-gym live coverage remains unknown; scoped batch counts cannot establish that manual/private games elsewhere are idle.
- Grouped capture inventory, stable evidence links, separately stored supervisor termination in game inspection, synthetic execution labels, existing annotation workflow, copyable batch/investigation briefs, desktop and phone views.

The existing pinned adapter/recorder callback contract was human-only. A separate explicit `managedBatchGame` opt-in now admits recorded all-AI callbacks on the batch instance. Default human admission remains intact. The batch server is a copy with only its embedded Gym adapter replaced; packaging proves all other archive members byte-identical. No engine rules, original records, historical acknowledgements, or producer instrumentation were changed.

## Installed mapping

- Native engine: `12ab2f0ed5bd018334521dc52497e6444f26f5a9`.
- Batch Gym/panel: `44d378af25df52f8751b2cb73f1103d253e743f3`.
- Batch server SHA256: `7da1a8edef0f7632536609cca9264d2ac7740bde295f2cfbba5744a43526d7d3`.
- Embedded adapter SHA256: `b286fa1df47b40eed935acbdbd05d4d38a2c87121c304d0625d659a8d6296d37`.
- Base server SHA256: `4bc99c5472cf351f3c35c299ffd0434e77c54189de8654173aecf33196b00de8`.
- Catalog, dependencies and original server were verified against `/etc/argentum-luna/service-profile.json`; the older `/srv/argentum-play/current/release.json` is not the active-version authority.
- Loopback batch native/provider ports: 8879/8880, verified unused before installation. Panel remains 8787. Existing Tailscale Serve configuration is byte-equivalent as JSON; no route changes.
- Supervisor: `commander-gym-batches.service`, enabled. Fixed role units: `commander-gym-batch-{native,recorder,provider,fixture}.service`, start only on admitted work. Source/config: `/etc/commander-gym-batches/config.json`.
- Durable state: `/var/lib/commander-gym-batches/{batches.sqlite3,budgets,captures,progress}`. Socket: `/run/commander-gym-batches/panel/control.sock`. Tmpfiles prepares its panel mount at boot. Panel configuration uses `/control/control.sock` and `/evidence/batches`.

## Checks and precise limits

- 89 focused Python checks passed, one environment-dependent UID test skipped. Coverage includes command conflicts/idempotency, persistence, ambiguous reservations, missing ledgers, concurrent zero-usage settlement, callback guards, batch/human admission separation, masking, unauthorized writes, traversal, grouped captures, simulated-result rejection and independent recording quality. Existing panel and provider budget checks passed.
- 12 focused JVM checks passed: recorder registration/retry/close, explicit managed marker without fabricated human admission, human-only refusal, profile and auto-configuration contracts. Built using the pinned native checkout and Java 25 through its `just gradle` wrapper with daemon reaping disabled.
- Real isolated native launches and private lobby observations succeeded. Synthetic keep/pass/discard choices progressed through multiple native turns and retained masked callback evidence. No SDK network calls or provider credential loading occurred.
- Owner interruption stopped only the isolated instance and preserved an interrupted capture. Killing the supervisor during a running synthetic game recovered to Paused/Interrupted with the same attempt count and unchanged observed budget (35 synthetic requests, zero reported tokens/cost). No replacement game was created.
- A final four-decision fence test stopped at exactly four decisions; four synthetic requests settled, with no uncertain usage. Its game is `ad1e2a54-ab37-4291-8fc4-e0bc98480729`, batch `batch-5670773ce4b74ea2bfa088569f2d36f1`. Its native outcome is unknown. Failed qualification attempts and unsealed captures remain visible.
- Live authenticated HTTPS browser checks passed draft/review/Start, queued pause without admission, two-tab 409, same-key response replay, copy brief, desktop/phone layouts, scoped Overview idle and managed-game/supervisor-context links. Missing and other identities returned 403. Final browser checks were read-only; no paid work was admitted.
- Original native inventory: 411 files before/after, with matching game-artifact inventories, modes, sizes, mtimes and SHA256 hashes. Only `.native-health.json` changed through the existing producer's normal heartbeat. Existing human units retain their November 6 activation timestamps.
- Observed administrative-process memory at one rollout snapshot: panel 24,645,632 bytes; supervisor 28,135,424 bytes. This is not a full archive latency or worker capacity benchmark. Workers have explicit systemd memory/CPU/task limits and remain stopped when idle.

A full autonomous native terminal game and a paid run have **not** been demonstrated. Confirmed native-completion handling was checked with native-status fixtures; simulated results and discovery completion cannot invent an outcome. Real smoke tests intentionally ended through interruption/recovery/limits. Strategic quality, actual paid usage, billing accuracy, and longer batch throughput remain owner validation. Cost fences are conservative estimates/reservations, not billing guarantees. Native outcome, supervisor disposition, recording completeness, integrity verification, replay qualification and review state remain independent.

## Recovery, rollback and next action

Deployment receipts, original panel unit/config/release target, source inventories/hashes and consistent SQLite backups are private under `/var/lib/commander-gym-batch-rollout`. Browser receipts/screenshots are under `/tmp/gym-panel-qa` (private; not published).

Rollback: stop and disable **only** `commander-gym-batches.service`; confirm its four role units are stopped. Restore `panel-before.service`, `panel-before.json`, and `panel-before-release` from the rollout directory, daemon-reload and restart only `commander-gym-web.service`. Leave Tailscale Serve unchanged. Preserve batch state, budgets, captures and the existing review database. Do not reboot or alter human services.

Next action: Noah can create a small bounded paid draft, inspect its frozen lineup/pins/limits, then explicitly Start if desired. Watch the independent native result and recorder qualification, and review any interrupted or failed attempt before creating a linked retry. No further implementation approval or key selection is needed. Preparation/update details are in `deploy/batches/README.md`.
