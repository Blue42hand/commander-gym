# Native certified forced pass

`native-certified-forced-pass@1` is a new deterministic component, resolved by
`BUILTIN_NATIVE_CERTIFIED_FORCED_PASS_COMPONENT_REF`. Existing Pilot revisions and
`native-all-unaffordable-pass` v1/v2 retain their behavior. Adoption requires a new
Pilot/Binding revision using this exact ref; merging this source does not activate it.

The old v2 handler recognizes ordinary casts, kicker, abilities, empty combat and
plain cycling, but rejects `CastWithFlashback`. Argentum's
`CastFromZoneEnumerator.enumerateFlashback` emits unavailable flashback offers as
`CastWithFlashback` wrapping `CastSpell(useAlternativeCost=true,
alternativeCostType=FLASHBACK)`, with `affordable=false` and `sourceZone=GRAVEYARD`.
`LegalActionEnricher` copies that fact to `isAffordable`. Thus an otherwise forced
pass can fall through to the provider because of the offer kind. Source inspected
at Argentum `470ac86484f07086eec08e12c9bd8abc7bc34be5`.

At that engine pin, `GameSession.beginAiCallbackEvidence` compares the supplied
menu to `getLegalActions(seat)` and checks the masked state, enriched pending
decision and current interaction epoch before issuing evidence. The v1 evidence
path has the same full-menu/state comparison.

The new component requires the seat adapter's process-local certificate. The
adapter issues it only after `verified_observation` checks the native enumerated
v1/callback v2 schema, body hash and acting seat, and after matching the full native
body to the callback state, legal actions, pending decision and log. A digest of the
complete resulting observation detects subsequent mutation. JSON fields, serialized
receipts, unverified callbacks and pass-only shortlists cannot assert this marker.
The marker adds no serialized observation fields and exposes no hidden state.
The Binding's deck-knowledge wrapper preserves the certificate through its one
allowed enrichment: it first rechecks the original digest, then attaches the
already resolved knowledge without changing native fields. The enriched digest
also detects later knowledge mutation. Plain mappings cannot gain a certificate
through this wrapper; changed native evidence fails before enrichment.
This is an integrity/completeness check within the existing trusted native callback
transport, not an independent signature or a replacement for native enumeration.

Every non-pass offer must have both native affordability flags explicitly false.
Any affordable alternative, including a mana ability or alternate-cost cast, wakes
the configured provider. Unknown flags and unknown offer kinds defer. Flashback
also needs the native wire discriminator, source zone and exactly one matching card
in the acting seat's graveyard. The existing full-menu identity/wire checks and
cycling safeguards still apply. Missing parameters, targets or mana previews never
prove unavailability. No mana arithmetic or alternative-cost reconstruction is added.

Success records `native-only-affordable-pass`, the native schema hash and original
state digest in the composed routing attempt. Deferrals distinguish missing or
changed evidence, unsupported callbacks, unknown affordability, affordable
alternatives and uncertified menus. Existing choice validation and native
interaction-epoch/state-revision checks still govern delivery; there is no cached
pass lease and no change to the provider's prompt or conditional-wait strategy.

`tests/test_native_forced_pass.py` uses synthetic masked callbacks and the actual
component resolver/composition with a fake v8 provider. It reproduces the old wake,
checks zero provider calls over repeated certified windows, and checks retained
provider choices plus stale/truncated/unknown evidence failures. These tests are
source qualification, not a replay of private recordings or a deployed-game result.
