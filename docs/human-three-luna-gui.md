# Supervised human and three AI GUI preparation

`scripts/run_human_three_luna_gui.py` supervises temporary loopback Binding sidecar,
normal Argentum GUI server and matching Vite preview. It never creates a lobby/game,
drives the human, invokes the headless runner, or starts a replacement game. This
is an opt-in experimental session, not foundation or full-game qualification.

The local preparation preflight first checks the accepted foundation and exact
component8 derivative. `export_session_catalog` then exports only the three active
Talrand/Sythis/Lathril Bindings and referenced Pilot, Deck and DeckKnowledge files.
It retains canonical identities, writes private files and excludes Krenko, old
Bindings, credentials, ledger, prompts and traces. Human deck choice is separate.
`verify_session_catalog` checks the separately pinned manifest, exact file set,
content hashes and canonical references without requiring unrelated foundation data.

## Operator plan and holds

Prepare an owner-only JSON plan with these required fields:

- `gymHead`, `engineHead`, `engineDir`: exact reviewed source and matching frontend.
- `catalogRoot`, `catalogManifestSha256`: minimized private package and external hash.
- `classpathFile`, `runtimeClasspathSha256`: ordered absolute JSON classpath from
  the composite adapter test runtime. Hash is the SHA256 of compact sorted-key JSON
  `[absolutePath, contentHash]` entries, using the existing runtime probe algorithm.
  Adapter main/test, engine server/rules compiled Kotlin classes must exist.
- `frontendSha256`: recursive content hash from the existing runtime probe's
  `_hash_entry(web-client/dist)`; matching installed Vite is required. Build before play.
- `budgetLedger`, `cumulativeCapUsd`, `cumulativeMaxRequests`: existing shared ledger
  and unchanged approved absolute ceilings. No ledger initialization or increase.
- `startRequests`, `startUnsettled`, `startEstimatedUsd`: fresh exact baseline.
- `incrementalCapUsd`, `attemptLimit`, `wallSeconds`: separately approved explicit
  session bounds; both provider attempts/retries count. No turn-8 cutoff, default
  spend allowance or human-idle stall timeout is inherited.
- `runtimeLock`: shared sole-writer lock; `serverPort`, `sidecarPort`, `frontendPort`:
  distinct loopback ports. Preview proxies `/api` and `/game` WebSocket to GUI backend.

Default invocation `python3 scripts/run_human_three_luna_gui.py --plan PLAN` only
verifies prepared sources/builds/catalog/budget. It loads no key or provider and
starts no services. Paid startup additionally requires `--launch --run-dir FRESH
--api-key-file EXISTING [--python EXISTING_VENV]` and all explicit plan gates true:
`paidSessionApproved`, `fourthChoiceRiskDispositionApproved`, `humanDeckChosen`,
`nativeGuiMockPassed`, `protectedRuntimeVerified`. These are recorded operator
attestations, not approvals created by the program. Run only frozen reviewed code
from the approved protected runtime/account. Do not stage credentials or ledger
in a user-writable public build tree.

Before approving those gates, perform a no-cost native/browser fixture with dummy
credentials and a rejecting fake Responses client: discover exact profiles over
sidecar HTTP and lobby WebSocket, select one human plus the three profiles, verify
bound decks and native masked seat observations, route opening/action/typed decision
callbacks independently, and verify human actions bypass the AI sidecar. Verify
frontend `/game` proxy and native parameter checks. A rejecting provider is mandatory:
fixture completion is not evidence of Luna play, strategy or paid qualification.
Python mock tests cover the registry/lifecycle seam; they do not claim this browser
fixture has run. Keep fourth-choice strategic uncertainty explicit.

The launcher passes a shared deadline and absolute session ceilings into the atomic
budget and guard. Native callbacks remain native; source state is never modified.
It rejects an extra AI seat, opening after play or a rewound turn, stops on callback/
native errors, caps, wall limit or operator signal, and holds the lock through verified
process-group cleanup. Human thinking alone does not trigger a stall. Logs and
provenance stay private. It records no claim of natural-terminal qualification;
separate native terminal/replay evidence is required. The operator must confirm
exactly one human and these three profiles before starting **one** game in the GUI.
The guard is a fail-closed backstop, not a general lobby authorization service.
