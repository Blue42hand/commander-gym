# Default batch limit diagnosis — November 9, 2026 (Pacific)

Branch `feat/panel-batches`. Installed reporting patch:
`d5d25742afcb6ba32113376c30cca98367181102`.

The owner's latest retry `batch-ebbc2f8f6ad9402b8eea0fe1efc8bf0e`, attempt
`attempt-3fed00e5102749b8b5ed632416527bd9`, game
`7ac04079-032b-4318-be99-e8e58ca74523`, reached native turn 6 and 78 callbacks.
Its token ledger settled correctly: 929,383 reported tokens, zero unsettled
token requests. The next conservative request reservation was 96,930 tokens:
1,026,313 exceeds the configured 1,000,000 limit. Recorded transport error is
`ValueError`, occurring locally in 7.28 ms. The earlier mapping-usage repair
worked; this observation is an ordinary admission limit, not another key error.

## Reporting repair

`BatchLimitError` records only a fixed reason and integer limit details.
`ChoiceGuard` follows the pilot's bounded exception cause chain, distinguishes
local token/request/cost/time/decision limits from other callback failures and
publishes structured safe progress. Arbitrary exception text remains private.
The supervisor records limit stops as Interrupted with a precise reason, and
retains native result, capture quality and batch disposition independently.
Existing historical attempts and ledgers are not rewritten or reclassified.

The provider's existing read-only `batch_budget.py` overlay now names this
immutable release. The supervisor's only unit override is WorkingDirectory
pointing at the same release. Its baseline `batch_store.py` and `batch_roles.py`
are unchanged. Configuration freezes and admission-verifies
`pins.batch_token_fence_sha256` and `pins.batch_supervisor_sha256` alongside the
unchanged native/Gym/adapter/catalog pins. No game rules, pilot behavior,
provider credentials, human units, panel, observer or Tailscale routes changed.
The idle batch supervisor was restarted before admitting the diagnostic.

47 focused tests passed. Coverage includes repeated usage settlement, invalid
usage retaining reservations, a wrapped local token exception becoming a safe
limit reason, arbitrary exception text exclusion and supervisor interruption
without inventing a native winner. An actual provider process root was read
to confirm the loaded module's hash matches its frozen pin.

## Authorized single-game diagnostic

The owner explicitly authorized one game using the existing key and default
settings. Batch `batch-d65030de16f948b4bb7f90a3c8ca4fec`, attempt
`attempt-767c561429eb4f74bb21ac3af02aeb0d`, native game
`1307c85b-bb12-4489-9f38-3d11c2798d0b`.

Same lineup: Meren, Sythis, Lathril, Alela, in that seat order. Limits unchanged:
one paid game, concurrency 1, 1,800 seconds, 10,000 callbacks, 100 provider
request admissions, 1,000,000 total tokens, $1 conservative estimated-spend
fence, pause on failure. The exact plan, reviewed fingerprint and idempotency
keys are private in the rollout receipt. No automatic second game is authorized.

Authenticated live browser checks confirmed Overview Running=1, Healthy
recorder, the diagnostic game's identity, and a working batch detail page.
Screenshot `/tmp/gym-panel-qa/default-diagnostic-active.png`.

Result: the single paid attempt stopped after 461.2 seconds at native player
turn 10 and 95 callbacks. It settled 904,483 reported tokens with zero unsettled
token reservations. Its next conservative reservation of 107,006 would have
raised that total to 1,011,489, above 1,000,000. The patched progress reported
`Limit reached / batch_token_fence` and the supervisor recorded Interrupted:
`Batch total token limit reached before provider dispatch (904,483 used or reserved + 107,006 next reservation > 1,000,000 limit)`.

The final estimated/reserved USD ledger was 0.228842592 with 52 monetary
request admissions and one unsettled monetary reservation. That last reservation
was made by the outer monetary guard before the inner token guard rejected
dispatch; retain it conservatively. These figures are not billed cost. All
token reservations settled; no limits were increased and no replacement
game was started. Native winner/result remain unknown and the interrupted
capture is unsealed; no finalization or repair was requested.

Authenticated HTTPS browser verification confirmed the exact limit arithmetic
and Interrupted attempt on the batch detail page; Overview returned to Idle
and zero running. Screenshot `/tmp/gym-panel-qa/default-diagnostic-stopped.png`.
All four isolated role units were stopped (native reports failed after the
supervisor's shutdown, the other roles inactive). Human native, recorder and
sidecar invocation IDs match their pre-deployment values. The preceding failed
retry's eight-file inventory and hashes remain unchanged. The private receipt
now includes the full final batch/attempt, frozen plan and elapsed time.

No completed autonomous game is claimed. This test establishes valid paid
responses, correct token settlement, progress beyond the earlier turn-6 stop,
safe admission rejection at the default cap, clear browser reporting and
isolated worker cleanup. Repeated defaults can terminate before a native
outcome; a future full-game run requires a separately authorized reviewed plan
with suitable limits. No additional paid attempts remain authorized here.

## Rollback

Previous config, provider override and human service invocation IDs are in
`/var/lib/commander-gym-limit-reporting-rollout` (0700). After all isolated
workers stop and no batch is queued/running, stop only
`commander-gym-batches.service`, restore `config-before.json` and
`provider-drop-before.conf` to their original locations, remove only
`commander-gym-batches.service.d/30-limit-reporting.conf`, daemon-reload and
start the supervisor. Preserve original evidence, annotation stores and
all budget ledgers. Restoring the previous reporting code restores generic
failure wording; it does not change or remove a historical limit stop.
