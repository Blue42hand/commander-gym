# Experimental wait/recovery prefix

The existing one-game launcher has a separate `--experimental-wait-prefix` mode.
It is off by default and cannot be combined with `--qualified-v7-comparison`,
`--dry-run`, or the sequential batch process-group option. The v7 preflight and
full-game qualification criteria remain unchanged. This mode always reports
`technicalQualified: false` and exits nonzero; it must not feed a qualified-game
batch cursor. A deliberate prefix stop is reported separately from native,
provider and callback failures.

The experiment uses the canonical v8 Pilot/Bindings already proposed privately:
fixed Krenko, Talrand, Sythis, Lathril order; GPT-6 Luna; at most two attempts per
callback; explicit named-wait guidance plus bounded provider recovery; unchanged
conditional-wait executor and native all-unaffordable router. Cache-friendly
history is disabled. No rule, legal-action validator, schema or seat projection
is changed. The experimental catalog preflight verifies the original qualified
v7 closure first, verifies the exact derivative Pilot/Bindings and their original
Deck/DeckKnowledge references, pins the experimental roster and closure, resolves
the canonical catalog offline, then rechecks the private staged copy. It makes no
provider request and does not qualify or activate the candidate.

## Bounds and lifecycle

An operator must supply the exact four experimental profiles, existing absolute
ledger path and cumulative limits, expected starting request/unsettled/USD values,
reviewed Gym and engine heads, the shared absolute runtime lock, and both absolute
session ceilings. The launcher refuses tracked source drift, ledger drift and
existing output evidence. It never changes cumulative caps, resets a ledger,
resumes an interrupted prefix or starts a second game.

The session ceilings must allow no more than **60 additional provider attempts**
and **$0.75 additional conservative estimated spend**, within the unchanged
existing cumulative ceilings. Attempts include validation and transport retries.
The ledger checks each reservation atomically across seats and reopened budget
instances; ambiguous attempts retain their reservations. The sidecar receives the
same absolute session limits as the supervising runner. A cap refusal never
selects a fallback action.

The native turn ceiling is at most **8 completed turns**: the first masked native
callback with `turnNumber > 8` is rejected before the composed player can choose
or dispatch. Ordinary callbacks require a native turn number; separate opening
mulligan and bottom-card callbacks retain their established formats. A private
stop marker contains only reason/turn/limit. The status poll also stops when the
native game reaches turn 9. This is an intentional experiment interruption, not
a native game loss or natural terminal result.

The work deadline is at most **900 seconds**, starting before isolated service
startup. Both the provider reservation and dispatch boundary check the absolute
deadline. The launcher bounds readiness waits and the runner subprocess by the
remaining time. Process cleanup and final receipt writing can follow the work
deadline; the runtime lock is held until isolated process groups are verified
gone. The final receipt preserves starting/ending ledgers, new unsettled count,
attempt/provenance reconciliation and cleanup status. Missing receipts or new
unsettled requests remain visible, never successful qualification evidence.

`--session-cap-usd` and `--session-max-requests` are absolute ledger values, not
increments. Partial session configuration is rejected. Ambient session/prefix
environment fields are removed by the launcher so they cannot mutate an ordinary
v7 run. The underlying sidecar can also configure the two absolute session fields
directly; complete prefix fields additionally require these bounds.

## Execution is still gated

Merging these controls does not authorize a paid call. Before execution, the owner
must review the minimized fourth-choice evidence, approve the bounded validation,
and arrange live source/catalog/SDK/ledger checks with the sole runtime writer.
Historical ledger figures in private proposals are not fresh readings. Raw
prompts, masked observations, deck payloads and provider receipts remain in the
owner-only experiment store. No automatic deployment or credential change is
part of this mode.
