# Heuristic harness integration pin

The harness algorithms and saved-state regressions are frozen at Gym commit
`9d7bed7254eac89eafc38a704c7490422416cb0a`. The main integration adds only the
matched native CI pin and this handoff.

Build and qualify the adapter against native engine
`53161b2e500d8f72ec43db08bacc1212fef2c5bc`, available in
`Blue42hand/argentum-engine` on `codex/pinned-harness-native-53161b2`.
Use the exact commit, not the moving branch name. The CI integration job selects
this same native commit. This includes typed convoke payment, mana-before-material
payment, colored-convoke affordability, and native draw-neutrality certificates.

Prior qualification of the exact algorithm/native pair passed 683 Python tests,
54 adapter tests, 11 recorded equipment-loop checkpoints, one useful-haste
checkpoint, and 1,864 closed-game callback contract checks with simulated provider
answers. These checks do not establish a new full-game strength or wall-time
result. Captured regressions are diagnostic rather than held-out evaluations.

The comparison task's original source/facts provenance and frozen input corpus
must stay intact; record new routing results separately against this explicit
pin. Historical engine observations are not relabeled as current projections.

Merging source does not activate a production release or authorize another game.
