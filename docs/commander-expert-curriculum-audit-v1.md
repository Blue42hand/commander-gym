# Commander expert curriculum — audit v1

Tracking: #115. Provenance/import contract: #113. Benchmark consumer: #114.

This companion audit records the first high-information, rights-eligible real multiplayer decision family ready for later #113 materialization. It is curation, not a native trajectory or preferred-action dataset.

## Rhystic Gaming MBG report

- snapshot: `detroitpro/blog.rhysticgaming.com@fdc506c3df6c5d26a0f9fabd843f99e0c0c84d24`
- source blob: `src/posts/2025-11-09-TR-MBG.md@77c5fb9dfe4be2ff2babf582591e39d0f0ed29f0`
- rights evidence: repository README and package.json declare ISC
- copyright holder: `unknown_or_not_stated`
- source class: `external_observation_action_record`
- origin: `real_cedh_tournament`
- completeness: `partially_complete_state`
- leakage group: `rhystic_mbg_2025-11-09`

Only repository-original report prose/observations are covered by this audit. Linked card/rules/media material is separate provenance. All examples from the report stay in one leakage group.

Never fill omitted hands, mana, targets, legal actions, priority passes, or motives from plausibility. A source action remains distinct from source explanation, site-author interpretation, and later Commander Gym adjudication.

| ID | High-information decision | Evidence boundary | #114 capability family |
| --- | --- | --- | --- |
| R1 | Acting seat passes during a win attempt knowing a downstream seat has revealed interaction, then responds after that resource is spent. | Observed sequencing plus source explanation; no complete legal-action set or guarantee the downstream seat would act. | `interaction_allocation / who_answers / downstream_responder_order / third_party_interaction_exhaustion` |
| R2 | After a draw-before-tutor sequencing error, acting seat abandons the intended line and pivots into engine denial plus held interaction. | Observed action plus source self-critique; counterfactual line is not observed. | `disruption_recovery / self_critique / line_pivot / resource_replanning` |
| R3 | Early Vexing Bauble is followed by a later tap-out that removes the actor's ability to control the Bauble window. | Observed action plus source recommendation/self-critique, not CG adjudication. | `stax_agency / threat_assessment / interaction_access / tempo_vs_control` |
| R4 | Mindbreak Trap is held through an earlier push and used on a later Oracle/Consultation attempt. | Observed action. The report's claim about what opponents believed is only `site_author_interpretation + uncertainty`. | `win_attempt_response_window / interaction_concealment / resource_preservation` |
| R5 | Early acceleration is partly spent removing an active Mystic Remora before advancing the commander plan. | Observed sequence plus source table-context explanation; removal spell and alternative legal actions are omitted. | `shared_engine_denial / pod_relative_threat_assessment / tempo_vs_resource_engine` |
| R6 | Semifinal mulligan to five retains lands, Mystic Remora, and interaction as a recovery plan. | Source explicitly warns semifinal notes are not fully reliable and contains internally inconsistent card-name text; preserve `source_notes_uncertain` and `source_internal_inconsistency`. | `mulligan_depth / card_economy_recovery / engine_maintenance / pod_speed_context` |

## #114 reconstruction contract

For each family, #114 should build bounded Argentum fixtures that vary only the Commander-specific axis: responder order, downstream interaction, pod speed, shared-engine ownership, stax agency, mulligan depth, or post-disruption recovery. The reconstructed fixture must provide seat-safe observations and native legal actions.

The source may supply an observed action or an explicitly authored explanation. It must not supply a preferred-action target by implication. Any preferred action must come from Commander Gym/native adjudication after reconstruction.

Tournament draw/concession negotiations in the same report are retained only as an environment-gated negative control: identical board state with and without explicit tournament-scoring/qualification context. Do not teach draw/concession behavior from game state alone.

## Materialization gate

Do not emit #113 dataset records yet. #113 remains open and owns the canonical source/version/license, information-boundary, transform, completeness, deduplication, and split-grouping contract. Once that contract lands, Rhystic R1–R6 should be the first real-game materialization family because source revision, rights basis, leakage grouping, and incompleteness are already explicit.
