# Heuristic-first local pilot handoff

Implemented on `feat/heuristic-first-local`, source commit
`823dcbddd68b457fa6e05053dc2410c1f702f392`, paired with Argentum
`12ab2f0ed5bd018334521dc52497e6444f26f5a9`. Production is unchanged.

## Behavior and provenance

The opt-in local Binding runs `MaskedHeuristicSubsystem` before LM Studio.
The policy returns a validated native choice or an explicit defer reason;
unsupported positions reach the existing model. Invalid policy output and model
failures remain errors. Every callback uses fresh native IDs and parameters.

The policy covers opening hands and bottoming, land/color development, simple
permanents, native-polarity single-target choices, tribe labels, structurally
recognized free optional draws, forced choices, and narrow safe priority/combat
cases. It uses only acting-seat observations, public definition facts and that
Binding's own registered deck. It never bypasses native legality or reads hidden
opponent information. Complex combat, teams, utility activations, uncertain
interaction and unknown cards defer.

Argentum's MIT OpeningHandReader/ChoicePriors algorithms informed the adapted
opening-hand and tribe rules; the native fact exporter directly uses Argentum's
intent, synergy and target-polarity analysis APIs. See `THIRD_PARTY.md`. Forge
contributes the broader architecture lesson: cheap contextual rules should solve
routine actions before expensive strategic search. No Forge source was copied
or translated. Native engine internals and whole-engine AI controllers are not
substituted for the masked pilot boundary.

The public fact export is pinned to the engine revision; policy source and facts
are hashed into a skill-set component. A different policy/fact set creates new
pilot and Binding identities. Existing Bindings without this component retain
their behavior. Local prompts now describe the nested choice contract explicitly.

## Verification

* 103 focused Python tests passed, including 28 heuristic tests covering land
  drops after spending mana, fresh IDs, tribe labels, hidden information,
  exact free-draw ability recognition, draw risks, payments, cancellation,
  complex-choice escalation, prompt fences, hash validation and opt-in routing.
* All 23 JVM adapter tests passed, including actual registry facts for basic
  lands, Sol Ring, Arcane Signet, Sythis, Banner, Mesa Enchantress and Underworld
  Dreams. Facts are definition-only; the exporter cannot accept GameState.
* Matched adapter/server built successfully; manifest source pins checked and
  embedded adapter verified byte-identical. `git diff --check` passed.
* Native synthetic game `4c8a07ed-9207-4efb-b49d-39f0124366b9` completed 121
  actions in 13.42 seconds, native turn 8 deck-out, with provider transport
  forbidden, sidecar/native exit 0, complete recording, no capture errors or
  pending writes, and terminal snapshot restoration verified.
* That fixture uses two eight-Plains decks. It demonstrates native routing and
  recording, not Commander skill or human qualification. An earlier fixture
  reached terminal in 11.85 seconds but its outer runner timed out during
  publication; its sealed journal and failure were retained and publication
  recovered separately.
* The candidate batch plan validates offline in a temporary store. No production
  ledger, services or games were modified by this validation.

Focused Python command:

```sh
PYTHONPATH=.:tests python3 -m unittest test_heuristic_policy test_lmstudio \
  test_lmstudio_bounce test_lmstudio_mixed_payment test_lmstudio_stream \
  test_game_server_binding_lmstudio_sidecar test_batch_local \
  test_native_forced_pass test_pilot_composition test_game_server_seat -q
```

Run native builds through the native checkout's `just gradle`, with the matching
engine directory and JDK 25. Logs, test receipts, extracted callback hashes and
both synthetic recordings live in the private evidence directory below.

## Prepared deployment package

Private root: `/home/commander/heuristic-first-evidence-20261111/release`.

* `artifacts.json`: SHA-256 inventory, exact source/engine pins; `gym-source.tar`,
  `adapter.jar`, `server.jar` and immutable `local-catalog/` payload.
* `batch-config-before.json`, `batch-config-candidate.json`, `batch-config.diff`:
  actual before/proposed configuration, prepared without installation.
* `service-path-replacements.json`: concrete immutable path replacements for the
  isolated batch supervisor and five batch workers. This is a replacement plan,
  not a qualified one-command installer. Human/panel services are outside scope.
* `catalog-identities.json`, `binding-fact-coverage.json` and
  `offline-plan-validation.json`: new Binding identities, coverage and validation.

The candidate catalog preserves original deck/knowledge data. Native facts cover
all distinct cards in the four reviewed decks (Talrand 70, Sythis 73, Lathril 75,
Alela 80). Meren and Rocco have only 24/79 and 16/84 facts respectively; unknown
cards defer. Do not claim full six-deck qualification.

Proposed immutable destinations are `/opt/commander-gym-web/823dcbddd68b457fa6e05053dc2410c1f702f392`,
`/opt/commander-gym-batch-runtime/823dcbddd68b457fa6e05053dc2410c1f702f392`,
and `/opt/commander-gym-local/20261111-heuristic-823dcbd`.
Because batch configuration pins one Gym revision, all isolated worker app paths
must change together. Source archives and native artifacts remain pinned to the
implementation commit above; later documentation commits do not alter them.

## Activation and rollback

Activation requires separate owner approval under `AGENTS.md`: “Present the final
matched artifacts, service/configuration diff, qualification results, and rollback
procedure for owner approval before deployment.” No production credential loading
or production activation was performed. New matched Commander comparison games
and isolated service cutover qualification remain outstanding; this package is
prepared for review, not a completed production qualification.

After approval, wait for all isolated batch workers to be idle, verify hashes,
install immutable destinations, apply the prepared configuration and unit path
changes, and validate the matched isolated services. Start a new draft selecting
new Binding identities; never rewrite old attempts or their pins to force resume.

For rollback, stop only the isolated batch supervisor/workers when idle, restore
`batch-config-before.json` and the previous unit path references recorded in
`service-path-replacements.json`, then restart and verify those services. Retain
all new artifacts, captures and failed attempts. Do not rewrite the ledger or
change human services.

See [the full recorded-game analysis](local-game-heuristic-review-20261111.md)
for observed gameplay regressions, timing and the final prompt-limit diagnosis.
