# Human versus Luna on Tolaria

Owner decision, October 13, 2026: this is the only active milestone. The implementation target is the existing native shared lobby, matched JVM adapter, Binding OpenAI sidecar, and sole native recorder. The model is `gpt-6-luna`.

## First release

- One human and one Luna opponent, with two decks whose required cards are supported by the selected engine release.
- Human starts the game through the existing private Argentum GUI.
- Native server owns legality, state, hidden-information projection, lifecycle, and terminal results.
- Gym consumes the acting seat's masked callbacks, uses the selected Binding and Luna policy, and returns native choices.
- The existing recorder remains the sole finalizer. Failure, usage and decision evidence remains private and durable.
- Systemd keeps the service available after logout and restores service availability after reboot. Mid-game recovery across a native process crash is a separate claim requiring its own successful test.
- Retain the existing tailnet HTTPS endpoint for the initial release. Additional LAN TLS provisioning is not on the initial critical path.

## Acceptance before deployment approval

1. Pin exact engine and Gym commits; record clean source status and artifact hashes.
2. Pass Python provider/Binding/recording tests and matched JVM integration tests. Exercise mulligan/bottom cards, legal action parameters, masking, stale choices, provider failure, and cancellation.
3. Build server, embedded adapter, and matching frontend. Verify manifest pins and byte-identical adapter embedding; inspect the actual packaged lobby catalog.
4. Exercise the real native writer with deterministic provider answers and verify recorder reopening. Report fixture scope separately from a full game and from production qualification.
5. Run isolated synthetic service qualification with real role separation, recorder IPC, ingress checks and cleanup. Correct mismatched production-reference/QA inputs; never replace the expected identity with the observed wrong identity.
6. Run the exact candidate recording inventory reader read-only against the existing production tree, including large captures, declared ancestor ownership, and root-written analysis sidecars. Require unchanged metadata and matching hashes; an empty synthetic tree does not establish production compatibility. Prepare the exact production service/configuration diff, private catalog identity, existing credential-source reference, startup ordering, storage checks, and rollback originals. Keep secrets out of source, command output, and public artifacts.
7. Obtain owner approval of that concrete deployment package. Preparation does not activate production or start paid games.

## Acceptance after approved deployment

1. GUI lists the intended Luna Binding and permits a human to start a two-seat game.
2. Complete a real human-versus-Luna game through an authoritative native terminal result; retain private recording and provider-usage evidence.
3. Reconnect the human browser during play without creating a duplicate seat or game.
4. Verify cancellation stops native delivery; errors remain visible and do not silently switch pilots.
5. Verify service recovery, then start a fresh game after reboot. Claim recovery of an in-progress game only if separately exercised successfully.

The owner performs the interactive human acceptance. Automated clients and deterministic substitutes are reported as synthetic tests.

## Backlog

The full #77 foundation program, local models, transfer learning, mass self-play, optional Redis qualification, broad card qualification, upstream publication, and automated source promotion do not block this milestone unless a demonstrated dependency prevents the acceptance checks above. Historical documents and receipts remain evidence of their original scope; they do not override this delivery order.
