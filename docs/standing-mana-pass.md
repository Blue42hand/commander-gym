# Opt-in standing pass with mana-only alternatives

`BUILTIN_STANDING_MANA_ONLY_PASS_COMPONENT_REF` identifies a new deterministic
Pilot component. Selecting it requires a new exact Pilot and Binding revision. The
qualified Foundation Pilot, its `native-no-choice` component, and existing Bindings
retain their identities and behavior.

The handler first applies the existing native no-choice path. Otherwise, it passes
priority only when the current Argentum legal menu contains exactly one affordable
`PassPriority` and every other action is an `ActivateAbility` marked as a mana
ability. It checks the acting seat, perspective, action IDs, native action types,
decision state, and affordability metadata. A spell, cycling action, nonmana
ability, required decision, or malformed menu wakes the strategic Pilot. The
policy never activates a mana ability. It is stateless, so a changed stack does
not itself cause a wake when the new legal menu still satisfies this rule.

This is a player preference, not a forced rules choice. It skips opportunities
to float mana proactively. In a saved game replay through turn 8, 30 callbacks
were eligible, including 2 already handled by `native-no-choice`; 29 recorded
choices matched this policy and 1 differed because the model activated a mana
ability before passing. These are counterfactual opportunities, not measured API
savings: a different action can change all subsequent callbacks. The saved run
had 88 completed model attempts through turn 8, so this narrow policy alone does
not establish the target of about one API call per turn.

## Forge behavior inventory

| Forge behavior | Current Argentum/Gym status |
| --- | --- |
| Forced pass and empty combat declarations | Implemented by `NativeNoChoiceHandler` using native legal actions. |
| Bounded priority wait after explicit pilot approval | Implemented by `DelegatedAutopassPilot`; it wakes on stack, own-state, phase, or legal-menu changes and saved no requests in the latest run. |
| Passing an unreviewed stack with no nonmana response | This opt-in standing policy implements that narrow case, including stack changes. |
| General `pass_unreviewed_stack` permission | Not implemented; it could skip an actual spell or nonmana ability response and is outside this policy. |
| `then_wait` after a semantic action sequence; `line`, `intent`, `develop`, and `payment` commands | Not implemented in Gym's native-action pilot. These require a separate action-sequence design; rules and payment remain in Argentum. |
| Forge's broader condition watches, such as next own turn, end step, and reviewed life ranges | Partial overlap with current bounded leases; full condition vocabulary is not implemented. |

The Forge transport and UI command vocabulary does not map directly to the
Argentum game-server observation contract.
