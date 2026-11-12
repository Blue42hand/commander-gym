# Three-deck policy expansion qualification

The Talrand, Sythis and Alela overlays implement the exact primers in the prepared
six-deck catalog. Lathril retains its existing overlay; Meren and Rocco retain
shared defaults and bounded inference. No production services were activated.

The source implementation and extension guide is [policy-layers.md](policy-layers.md).
The new profiles pin the deck and primer fingerprints; changed primers cannot
silently activate these plans. Public facts were regenerated from native engine
`12ab2f0ed5bd018334521dc52497e6444f26f5a9` using the updated exporter.

## Historical position qualification

The private comparison corpus contains 462 Talrand/Sythis/Alela callbacks from
games `e21d6595-71be-4649-be54-1d4f1592e0ae` and
`21771f71-7e04-48de-a3c8-2247b7013400`. Its source journals were hash-chain checked
and each native masked observation authenticated. The previous defaults are pinned
to Gym commit `e5e05174f80c2e7324684aacf09df1b6b95fec9b` with their original facts.
The new overlays are applied counterfactually using the reviewed catalog's deck
identities; the old captures do not independently attest primer identity.

For the latest game, local handling includes existing certified forced passes:

| Deck | Callbacks | Previous default local | New local | Previous fallback needed | New fallback needed |
|---|---:|---:|---:|---:|---:|
| Talrand | 80 | 54 | 64 | 26 | 16 |
| Sythis | 74 | 58 | 71 | 16 | 3 |
| Alela | 78 | 58 | 68 | 20 | 10 |
| Total | 232 | 170 | 203 | 62 | 29 |

This is a 53% reduction in required fallback decisions compared with the preceding
shared-default candidate on these states. It is not a full-game speedup or a
win-rate result. The original two games did not finish.

The qualification also includes 1,791 previously authenticated Lathril positions:
2,253 authenticated positions in total. All routes and simulated provider answers
passed without contract or prompt-budget errors. Two separately exported failed
Alela callbacks were also checked as diagnostics; both are now covered by the
Alela plan. No real model requests were made. Frozen-choice responses select a
supplied test index, and native-schema responses reuse the recorded response;
these check the contract rather than model judgment.

The prompt-specific diagnostic forces fallback on the seven formerly oversized
positions and the final failed Alela callback, independent of whether the new
policy now handles them. All fit the unchanged 32 KiB ceiling after lossless
factoring and equivalent action-schema grouping. Arbitrarily larger unsupported
positions can still hit the ceiling and report a visible error.

## Other checks and limits

* 191 focused Python tests pass, including private-directory sealed runtime checks.
* Both native public-facts exporter tests pass against the pinned real registry.
* A fresh six-deck catalog resolves all six shared defaults and four exact overlays.
  Covered callbacks avoid inference; unknown callbacks reach simulated bounded
  inference for all six catalog profiles.
* Unusual interaction, blocks, mandatory or taxed attacks, repeatable Aura loops,
  complex closing tools and alternate wins retain inference. Mana and draw reserves
  are conservative policies, not engine simulation or complete tactical proofs.

Private machine-readable results are in the task's `outputs` directory:
`deck-expansion-validation.json`, `deck-expansion-tests.json`,
`deck-expansion-catalog-validation.json` and
`deck-expansion-prompt-regressions.json`. The corpus and original capture segments
are preserved. These checks do not qualify a completed native game, live model
quality or production activation.
