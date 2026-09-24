# Commander expert curriculum

Tracking: #115. Provenance/import dependency: #113. Transfer consumer: #114.

## Named curriculum

The working dataset lineage name is `commander-expert-curriculum-v0`.

It is a curated supervision corpus, not a raw scrape. Its purpose is to teach Commander/multiplayer transformations of general Magic competence while preserving the difference between:

1. an observed player action;
2. a source expert's recommendation or explanation;
3. Commander Gym's later adjudication/preferred alternative;
4. uncertainty or missing information.

The dataset should start small and audited, then grow toward a few hundred decisions. Do not bulk-ingest source prose merely because it is publicly readable.

## Source-use policy

Every source must carry the #113 external-source provenance fields before it becomes training-eligible. In addition to source URL/version/digest and information-boundary metadata, record a conservative `training_use_status`:

- `eligible` — explicit project/owner permission or a compatible license/terms basis has been verified for the intended use;
- `reference_only` — useful for researcher understanding/source discovery, but the source content must not enter the training dataset;
- `pending_permission` — permission or license compatibility is unresolved;
- `prohibited` — source terms or owner instruction exclude the intended use.

A public URL is not evidence of training/redistribution permission. Store only the minimum source material necessary for provenance when terms do not permit corpus reuse.

## Audited source classes

### Owner-authored Commander Gym DeckKnowledge / primers

Status: **eligible for private/internal curriculum use**, subject to #113 recording exact artifact identity and keeping private instance content out of the public repository.

Use for:

- mulligan heuristics;
- deck-plan inference;
- sequencing and resource preservation;
- recovery priorities;
- combat allocation;
- commander-dependence and engine assessment.

Raw private primer text should remain in the private instance/external data store. Public benchmark fixtures should use synthetic or separately licensed material.

### Native Commander Gym decisions + later adjudication

Status: **eligible when the underlying run/evidence is qualified**.

Use for:

- observed action;
- teacher/critic/human adjudication;
- preferred alternative;
- uncertainty/disagreement;
- later search/counterfactual labels.

This is the cleanest long-term source because seat-visible input and legal-action provenance are native.

### Sam Black, "When Should You Interact in cEDH?" (TopDeck.gg)

Source: https://topdeck.gg/articles/when-should-you-interact-cedh

Curriculum relevance:

- interaction allocation when multiple opponents benefit;
- identifying a fundamental/win turn;
- adjusting interaction thresholds based on one's own equity;
- asking whether another player can/should answer;
- distinguishing a card that is merely strong from one that must be stopped;
- deciding whether a stax piece must be removed immediately.

Status: **reference_only / pending_permission**.

Reason: TopDeck's site terms reserve content rights and restrict unauthorized reuse/derivative exploitation. Commander Gym should not copy article prose or turn it directly into training examples unless a compatible license or permission is established. Researchers may use the article to identify concepts that are then independently specified, tested, and adjudicated from Commander Gym-owned/synthetic/native evidence.

This is a conservative project policy, not a legal conclusion.

### Tournament result databases

Status: **metagame_result**, not decision supervision by default.

Tournament standings/decklists can identify archetypes, pod speed assumptions, deck populations, and games worth investigating. They do not establish what a player knew or chose during a specific in-game decision.

### Recorded gameplay / narrated games

Status: **reconstructed_decision** only when acting-seat information is recoverable.

Never infer hidden cards, omitted targets, unshown sequencing, or rationale. If a reconstruction cannot establish what the acting player knew and which relevant alternatives existed, keep it diagnostic/reference-only.

## Initial curriculum slices

The first dataset revisions should target bounded reasoning slices rather than broad "play Commander well" labels.

### Slice A — interaction allocation

Target concepts:

- immediate win/fundamental-turn detection;
- personal cost of spending interaction;
- likelihood another player can answer;
- whether allowing resolution materially reduces one's own winning chances;
- whether a permanent prevents one's own route to victory;
- preserving interaction when a threat is strong but not decisive.

Preferred eligible evidence sources:

1. qualified native Commander Gym states with teacher/human/search adjudication;
2. owner-authored/synthetic scenarios grounded in private DeckKnowledge but rewritten as seat-safe state/action fixtures;
3. permissively licensed expert material if found.

The Sam Black article is a concept reference only until permission is resolved.

### Slice B — deck-plan inference and mulligans

Use owner-authored DeckKnowledge plus qualified native states to test whether a generally competent Magic player adapts to the bound deck rather than applying one universal mulligan heuristic.

### Slice C — recovery and long-game engine assessment

Focus on commander removal, board wipes, preserving independent engines, recast-tax decisions, and deciding when a board can absorb an opponent's medium-strength permanent rather than spending premium interaction.

### Slice D — multiplayer combat allocation

Focus on attack-target selection, crack-back risk, commander damage, whether a wide attack exposes needed engines, and opponent-specific blockers/resources.

## Example record semantics

A curriculum item may omit fields that are genuinely unavailable, but must never fill them by invention.

```json
{
  "source_class": "expert_recommendation",
  "capability_slice": "interaction_allocation",
  "acting_seat_input": {"status": "seat_safe_fixture_or_native_observation"},
  "legal_choices": {"status": "native_or_explicitly_reconstructed"},
  "observed_action": null,
  "expert_recommendation": {"status": "present_if_licensed_or_independently_recorded"},
  "commander_gym_adjudication": null,
  "uncertainty": ["no_observed_player_action"],
  "training_use_status": "eligible_or_reference_only"
}
```

The #113 implementation owns the canonical schema. This example is semantic guidance only and must not become a competing serialization contract.

## Gate to first named dataset revision

`commander-expert-curriculum-v0` becomes a real #114-consumable dataset when:

- #113 can represent its external/private source provenance and training-use status;
- each item is grouped by source game/document/derivation family for leakage-safe splitting;
- every item is seat-safe or explicitly non-policy/reference-only;
- observed action, source recommendation, Commander Gym adjudication, and uncertainty remain separate;
- at least one bounded slice has enough audited eligible items to run as a benchmark/training ablation.

Until then, source discovery and concept extraction may proceed, but reference-only sources must not be silently promoted into supervision.
