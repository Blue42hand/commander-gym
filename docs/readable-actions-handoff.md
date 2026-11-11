# Readable game actions and records — November 9, 2026 (Pacific)

Branch: `feat/panel-batches`. Installed panel commit:
`4e4851344fac5ed489572e326e61e72434fa6dab`.

Game detail now has a readable Game actions timeline. It follows recorded
native actions, turn/phase changes, land plays, spells, attacks/blocks, damage,
life changes, decisions, resolutions and relevant zone changes. Pilot callback
errors are explicitly separate from executed moves. Unknown action types use
their recorded names; missing names/turns remain unknown. No AI narration,
provider calls, proposed moves presented as execution, or inferred rules verdicts.

Controls: Latest, From start, Older/Newer pages, turn filter, include passes and
phase changes, Pause/Resume, per-entry evidence anchors, and readable text
download. Polling updates only the feed, preserving review drafts, selected raw
evidence and scroll. Hidden tabs pause reads; failed reads keep the prior feed
and back off. Partial backfills catch up in sequential bounded requests and
remain visibly partial until the observed journal prefix has been processed.

New owner-authenticated GET endpoints: `/api/games/<ref>/actions` (50 entries)
and `/api/games/<ref>/record` (plain text). Downloads include participants,
recorded outcome, independent supervisor termination, source revisions, engine
and Gym revisions, timestamps, journal sequence anchors and row hashes. Exports
are capped at 2,000 projected entries and 1 MiB, with explicit truncation. A
caught-up prefix does not certify recording completeness, replay or a winner.
Existing raw evidence pages remain separately available.

The rebuildable projection database is `/state/index/actions.sqlite3`, separate
from irreplaceable reviews and mounted source evidence. Incremental scans read
at most 120 source rows, 24 MiB and approximately 0.6 seconds per request, with
2 MiB physical/decoded record caps. A single already-started record decode can
extend the wall-clock budget. Existing mixed-format decoding is reused. Journal
sequence/hash continuity is checked before projection; malformed/oversized
records stop with diagnostics, incomplete tails stay pending. Replacement,
truncation or a changed cached edge invalidates the cache generation. These
checks do not replace the existing finalized-manifest verifier. Source files
are never edited, finalized or repaired.

65 focused tests pass from the immutable release: live append without
duplicates, persistence/rebuild, bounded large-journal work, partial tails,
malformed/oversized/hash-mismatched records, symlinks/FIFOs, native facts,
pilot-vs-execution distinction, and authorized read/export routes. Browser
fixtures verify real journal appends appearing in the feed, pause/resume,
preserved review drafts and raw evidence, readable download and phone layout.
The fixture server was stopped. No real or paid game was launched.

Live verification receipt: `/tmp/gym-panel-qa/readable-actions-live-receipt.json`.
Browser scripts/screenshots: `/tmp/gym-panel-qa/readable-*`.
Live HTTPS verification passed: 1,686 journal rows projected into 553 readable entries; history paging, turn filtering, immediate evidence-anchor selection, full observed-prefix export with supervisor limit reason and hashes, desktop/phone layouts, and escaped hostile text. The action cache survived the final panel restart. Initial cold inventory may briefly require the existing Retry action before a capture reference is available.

Deployment restarted only `commander-gym-web.service`. Existing human native,
recorder, sidecar, batch supervisor and batch role invocation IDs match before
and after; Tailscale Serve JSON and diagnostic evidence inventories/hashes are
unchanged. Missing-owner requests to both endpoints return 403. Concurrent
local-inference edits in batch files, installer, and untracked modules were
preserved and excluded from these commits/releases.

Rollback: restore `/opt/commander-gym-web/current` to the target saved in
`/var/lib/commander-gym-readable-actions-rollout/panel-before-release`, then
restart only the web service. Preserve review notes, original captures, budget
ledgers and the rebuildable action cache. There are no service/configuration,
credential, producer, gateway, engine or Tailscale routing changes to undo.
