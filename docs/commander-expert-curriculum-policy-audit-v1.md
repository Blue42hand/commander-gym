# Commander expert curriculum — policy/state audit v1

Tracking: #115. Provenance/import contract: #113. Benchmark consumer: #114.

This companion file records rights-eligible multiplayer/Commander policy and persistent-state families. These are not expert-action labels and are not Commander Gym adjudication. They exist to make #114 fixtures cover Commander-specific rules, information boundaries, and multiplayer state transitions without turning community rules answers into strategy supervision.

## Provenance rule

Board & Card Games Stack Exchange question/answer pairs stay in one leakage family per thread. Preserve contribution-era CC BY-SA attribution and exact post revision metadata where pinned. Paraphrase source material; do not copy card/rules prose into the dataset. Historical rule numbers are source metadata only; expected outcomes come from the current rules version used by #114.

## Audited families

### multiplayer_shortcut_information_reveal

- Q48337 by Aetherfox, 2019-08-21.
- A48338 by J. Sallé, current rev2; CommunityBot edit 2020-06-17.
- classification: `constructed_multiplayer_scenario / community_rules_adjudication / not_observed_action / not_CG_adjudication`.
- information boundary: an intention disclosed through shortcut negotiation becomes public only after that disclosure event; do not expose the underlying hidden card/state earlier.
- #114 fixture axis: hold the loop/state constant while varying which downstream player shortens, responder order/endpoints, and disclosure-present versus disclosure-absent before a later voluntary decision.
- label boundary: native/current-rules adjudication supplies shortcut/priority legality only; no strategic preferred-action label.

### attack_requirement_restriction_allocation

- Q58245 current rev6.
- A58248 by murgatroid99 current rev3; 2022-12-12.
- classification: `constructed_commander_scenario / policy_or_legality_constraint / not_observed_action / not_expert_strategy`.
- capability tags: `combat_attack_allocation_legality / restriction_over_requirement / maximize_satisfied_attack_requirements / forced_commander_attack`.
- #114 fixture axis: hold board state fixed while varying attack-if-able requirements, can-only-attack-alone restrictions, added goad/requirements, optional attack costs, and available defenders.
- label boundary: native/current-rules adjudication determines legal declarations; legality does not imply a strategic preference.

### controlled_player_forced_cast_and_cost_choice

- Q58334 current rev3.
- A58336 by murgatroid99 current rev2; 2023-01-01.
- origin: real Commander question with incomplete state; answer is `community_rules_adjudication`.
- information boundary: temporary control of a player can expose that controlled player's private game information for the duration allowed by the effect. Represent this as `information_visibility_source=player_control` or native equivalent, not ordinary/global seat-private visibility.
- capability tags: `limited_duration_player_control / controlled_player_private_information_visibility / forced_cast_during_resolution / controlled_player_cost_choice / controlled_player_resource_use / decision_authority_transfer`.
- #114 fixture axis: vary timing type of the instructed card/action, variable/additional costs, private information visible only because of player control, and whether the requested choice is actually delegated by the effect/rules.
- uncertainty boundary: do not infer omitted hand contents, resources, alternative legal actions, or motive.

### terminal_player_elimination_transition

- Q39259 current rev6, a rollback to rev4.
- A39261 by doppelgreener current rev4; CommonMark migration 2020-06-17.
- question classification: `real_commander_game_origin / partially_complete_state`.
- answer classification: `community_rules_adjudication / hypothetical_transition / not_observed_action`.
- capability tags: `terminal_player_elimination_transition / last_opponent_exit_immediate_win / owned_protection_source_removed_on_owner_exit / terminal_vs_nonterminal_multiplayer_transition / loss_prevention_source_owner_divergence`.
- #114 fixture axis: hold owner/controller divergence and the protection source constant while varying last-opponent versus third-player-remains, source-owner departure versus survivor ownership, and whether a later state check would matter if play continued.
- uncertainty boundary: do not fabricate the eventual departure event, its cause, hidden resources, or strategic motive.

## #114 consumption contract

Treat these as frozen policy/state fixture families, separate from strategic supervision such as the Rhystic MBG decision records. Each reconstructed fixture must supply seat-safe observations and Argentum-native legal actions. Source answers may motivate what rule/state boundary to test, but expected outcomes must be generated/validated against the current rules version and Commander Gym adjudication must remain a separate field.

Do not materialize #113 dataset records until #113's canonical source/version/license, information-boundary, transform, completeness, deduplication, and split-grouping contract lands.
