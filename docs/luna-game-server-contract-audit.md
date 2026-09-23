# Luna game-server contract audit

Status: active qualification work for Commander Gym #72.

The browser GUI is a final human-usability acceptance surface. Basic communication,
serialization, policy-routing, and provider-shape failures should be found first in the
automated game-server path.

## Audited path

```
Argentum game-server
  -> AiControllerProvider / AiPlayerController
  -> Commander Gym JVM adapter
  -> bearer-authenticated loopback sidecar
  -> GameServerSeatAdapter
  -> RoutingPilot
  -> OpenAIResponsesPilot
  -> OpenAI Responses API
```

## Findings and dispositions

| Boundary | Finding | Disposition |
| --- | --- | --- |
| launcher -> sidecar | The local launcher used an EXIT trap and then `exec`'d Gradle, so an old sidecar could survive with an old bearer token and cause the next run's HTTP 401. | Fixed: launcher owns both child PIDs and kills both on exit. |
| Argentum -> pilot mulligan | Mulligan keep/take has no native legal-action list, so the pilot needs stable callback-local identities. | Fixed and covered by focused tests. Mulligans remain strategic; no blanket auto-keep shortcut. |
| Argentum -> pilot bottom cards | Bottom-card selection is a dedicated callback. | Fixed. Only genuinely forced zero/all selections bypass the model. |
| Argentum -> pilot deck knowledge | `AiPlayerController.setDeckList()` was a no-op in the Commander Gym adapter, so Luna did not know its own deck composition. | Fixed: deck list/archetype crosses the sidecar once and is retained in seat observations. No library order is exposed. |
| native PendingDecision -> Commander Gym observation | Native decisions serialize routing as `id`, while the shared pilot contract expects local `decisionId` and a structured-response marker. | Fixed in the seat adapter. Both routing aliases are removed before model input and the live id is re-injected locally into the native response. |
| PendingDecision -> DecisionResponse ontology | Commander Gym had to infer response types/field names; this produced failures such as `YesNoDecision` / `"YES"` instead of `YesNoResponse` / boolean. | Generic Argentum response-contract work is tracked by Blue42hand/argentum-engine#193. The branch currently has an exhaustive downstream fallback for older Gym observations; the normal game-server path should consume Argentum's contract once #193 lands. |
| native legal action -> model choice | Luna selects by stable Argentum semantic id; volatile action ids are local routing only. | Existing design retained. Request-local schemas constrain semantic ids to the current legal set. |
| model ActionParams -> native action | Previously malformed names/types could reach Kotlin before failing. | Fixed: Commander Gym validates allowed fields/types/applicability and retries once; Argentum remains authoritative and applies params only at the native edge. |
| hidden/trusted state | The JVM provider owns a trusted snapshot for native parameterization, but policy must never receive it. | Existing fail-closed boundary retained; cross-seat projections and explicit snapshot fields are rejected. |
| sidecar/provider errors | Provider and validation failures must not silently choose another strategy. | Existing fail-closed 503 path retained with bounded provider diagnostics. |
| Responses output format | Free-form JSON-object mode allowed avoidable response-shape drift. | Switched to request-local Responses `text.format` JSON Schema. Local validation remains authoritative. |
| deterministic choices | Forge-era wake reduction included forced choices, native payment, no-choice combat, and post-action waits. | Port only engine-certified equivalents: forced parameterless choices, forced structured choices, no-choice combat, native/default damage, native auto-payment. Argentum already owns priority auto-pass/post-action progression. |
| mulligan heuristics | Argentum's built-in LLM auto-keeps after repeated mulligans. | Not adopted as a certified fast path: it is still a strategic choice. |
| full-game debug | Manual GUI play made each contract failure expensive to discover. | Added an automated two-Luna game-server runner with fixed decks, provenance, call/token/retry counts, wake accounting, server-error scan, and conservative skill-review flags. |

## OpenAI API contract

The Luna pilot uses the Responses API with `store=false`. Output is constrained with
request-local `text.format = json_schema` schemas. Strict mode is used only for shapes
that fit the supported closed schema subset; arbitrary-key native maps and cancellation
unions remain non-strict and are validated locally before native submission.

No provider schema is treated as rules authority. Argentum's legal actions,
`DecisionValidators`, and native action parameterizer remain authoritative.

## Wake-reduction rule

A model call may be skipped only when the current Argentum callback certifies that no
meaningful strategic branch remains. Current certified classes include:

- exactly one parameterless legal action;
- no eligible attacker/blocker in the native declaration action;
- fixed/unique structured choices;
- engine-default damage assignment;
- default combat-resolution edge amounts;
- native mana auto-payment when Argentum supplies a solution;
- forced bottom-card selections.

Everything else escalates to the strategic pilot. If a later audit discovers a
strategic call that one of these handlers could have answered, the two-Luna report
marks it as an avoidable strategic wake.

## Two-Luna qualification loop

The runner uses Argentum's existing dev-only AI tournament path. It does not implement
a second game lifecycle.

Default workload:

- two normal game-server AI seats;
- two fixed, implemented 60-card e2e decks;
- one game per run;
- one stable Luna-backed pilot per seat;
- private JSONL policy provenance;
- server log retained for first-failure diagnosis.

Run:

```bash
export OPENAI_API_KEY='...'
bash scripts/run_two_luna_debug_game.sh
```

The runner prints `TWO_LUNA_DEBUG_RESULT=...` with:

- terminal/completion status and max turn;
- callback mix;
- provider call count;
- input/output token totals;
- validation retry count;
- certified strategic wakes avoided;
- strategic wakes that should have been mechanical;
- communication-error excerpts;
- conservative gameplay-review flags.

## #72 gate

Before returning to browser play, one complete game must have:

1. a natural terminal result;
2. zero auth/transport/serialization errors;
3. zero stale or invalid native submissions;
4. zero contract-shape validation retries;
5. zero avoidable strategic wakes;
6. no trusted snapshot or opponent-private leakage;
7. retained own-deck context for both seats;
8. a reviewable call/token/wake report;
9. no obvious gameplay breakdown in the retained trace.

After this passes on the simple fixed-deck workload, repeat the same loop with the
Commander roster before using the real GUI as the final human-play check.
