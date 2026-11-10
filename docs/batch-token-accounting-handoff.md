# Retry token accounting repair — November 10, 2026

Branch: `feat/panel-batches`. Patch commit: `3a058605eb04de7c1bd791f0c2515e38a2b80920`.

The owner's retry `batch-c0593d4125d544c6bc981a712e5b9bec`, attempt
`attempt-0e07222e4627492a80825482ed88d6df`, game
`5a0a82fb-565f-4f21-a1a0-c8bd6fa84af4`, passed authentication and reached
native turn 3 / 29 callbacks. Sixteen unique successful Responses IDs report
224,836 total tokens. The token ledger instead retained 914,635 reserved tokens,
zero reported tokens and sixteen unsettled requests. The next request reserved
89,065 tokens, exceeding the 1,000,000 fence. Recorded transport failure is
`ValueError`; the ledger arithmetic and source implementation identify the local
token admission guard. This is separate from the original expired-key failure.

`BoundedProcessResponses` returns `_ResponseDict` with nested usage as a mapping.
`TokenFence` previously used only attribute lookup for `usage.total_tokens`.
It now accepts mapping and SDK attribute representations. Missing, invalid,
negative and boolean token values retain reservations; exceptions remain
ambiguous. No limits were raised, no ledgers rewritten, no outcomes changed.

## Installed repair

The native/adapter/Gym baseline remains `12ab2f0` / `44d378a`. The provider alone
receives an immutable read-only file overlay from
`/opt/commander-gym-web/3a058605eb04de7c1bd791f0c2515e38a2b80920/commander_gym/batch_budget.py`
through `/etc/systemd/system/commander-gym-batch-provider.service.d/30-token-accounting.conf`.
SHA256: `22c653d2751fb44633adcdafe4415091d653011bab77c890a64a0a14490871c8`.

The supervisor configuration records this hash in `pins.batch_token_fence_sha256`
and verifies the immutable file before admission. New retry/clone drafts freeze
that pin; an existing draft with older pins requires a new reviewed draft.
Only the idle batch supervisor was restarted. Human services, panel, observer,
Tailscale routes, original captures, annotations and provider credential were
not changed. The provider unit was not started and no API call was made.

## Checks and next action

45 focused tests passed across batch controls, bounded provider subprocess,
deadline worker, monetary budget and provider failure evidence. Regression
tests cover repeated mapping-based usage settlement and invalid usage keeping
the fence closed. A transient offline check using the actual provider identity,
root directory, baseline source mount and new overlay settled twenty fixture
responses to 340 tokens with zero unsettled reservations. It had no credential
mount and only Unix sockets available. Broker reads confirm the new pin and
the original failed retry with exactly one attempt. Original retry evidence
hashes and inventory match before/after. Saved human service invocation IDs
remain unchanged. No full paid game is claimed.

Noah can use Retry on the failed batch, review the new draft and explicitly
Start. Existing monetary/request/token fences still apply; actual later limits
are possible and must not be confused with a terminal game result.

## Rollback

Private receipts and previous configuration are in
`/var/lib/commander-gym-token-accounting-rollout` (mode 0700). The one-time
installer is `/tmp/install-gym-token-patch.py`. While all isolated workers are
stopped and no batch is queued/running, stop only `commander-gym-batches.service`,
restore `config-before.json` to `/etc/commander-gym-batches/config.json`, remove
only `30-token-accounting.conf`, daemon-reload and start only the supervisor.
Retain all captures, notes and budget ledgers. Restoring the old code restores
the accounting defect. A future complete batch release from this branch should
include the fix directly and remove the overlay deliberately; do not copy its
hash pin into an unrelated runtime.
