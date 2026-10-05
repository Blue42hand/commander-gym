# Reviewed delegated priority passing

This opt-in Pilot component adapts the Forge mediator's bounded priority wait to
Argentum's native legal actions and masked game-server observation. It is identified
by `BUILTIN_DELEGATED_AUTOPASS_COMPONENT_REF` and must be selected by a new exact
Pilot and Binding revision. Existing Foundation components and Bindings do not change.

The strategic provider first chooses the current `PassPriority` action. It may add:

```json
"priorityDelegation": {
  "until": "phase_end",
  "reason": "Reviewed this priority plan",
  "watchOpponents": false
}
```

`until` is `phase_end` or `next_own_main`; `watchOpponents` defaults to false,
matching the Forge wait's default empty opponent-watch list. A delegated pass is
permitted only while Argentum exposes PassPriority plus mana abilities and no
required decision. The lease ends at its phase/turn boundary or after 16 passes.
It wakes for a spell or ability added to the stack, new nonmana legal action,
required decision, own hand/board/private-zone change, changed mana or life, or
missing/stale observation evidence. The model can request opponent battlefield
watches; any opponent battlefield change then wakes it. A lease cannot begin with
floating mana. The pilot never activates mana automatically. Every delegated
choice retains lease identity, reason, and ordinal in provenance.

The two retained baseline games stopped at turns 8 and 4. A replay that
hypothetically approves `next_own_main` at every recorded model-selected pass
finds 6 and 1 later pass callbacks handled by the lease, with no recorded-choice
divergence. Some proposals are invalid with floating mana. These counts are
counterfactual: model approvals and subsequent game states may differ. The
strict no-choice handler separately matches one callback in each trace.
