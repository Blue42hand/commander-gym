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

Licensing and evidentiary quality are separate gates. A permissive license may permit reuse of prose while the material still remains only a `strategic_reference`; it does not turn a deck description or primer into an observed action, expert decision trace, or adjudicated policy target.

## Audited source classes

### Existing Commander Gym Archidekt primer bundles

Status: **strategic_reference / candidate_hypothesis only** for the seven surviving legacy bundles currently in `commander-gym-private`.

Their own metadata identifies them as generated tactical analysis with `status=initial_unplaytested` and `games_played=0`. They are therefore not owner-authored expert evidence and must not be used as:

- `observed_action`;
- `expert_recommendation`;
- direct preferred-action/training targets.

They may seed seat-safe synthetic scenarios, review queues, deck-plan hypotheses, mulligan hypotheses, and later adjudication work. Preserve the exact Deck revision plus policy/primer digest so any later accepted claim is traceable to the source hypothesis.

The Gitrog bundle additionally contains source-derived material and exact-list adaptations. Direct reuse of outside-source material remains behind #113 license/provenance review.

Future genuinely owner-authored DeckKnowledge may be admitted separately when its authorship and provenance are explicit; do not inherit that status for the generated legacy bundles.

### Native Commander Gym decisions + later adjudication

Status: **eligible when the underlying run/evidence is qualified**.

Use for:

- observed action;
- teacher/critic/human adjudication;
- preferred alternative;
- uncertainty/disagreement;
- later search/counterfactual labels.

This is the cleanest long-term source because seat-visible input and legal-action provenance are native.

### cEDH Decklist Database

Repository: `cEDH-Decklist-Database/cEDH-Decklist-Database`

Audited revision: `c9e503c4e6be77aa90b2a472c34cbc65ef2d6725`

Repository license: **MIT**.

The audited database contains 137 entries, including 56 marked `COMPETITIVE`; all 137 have repository-hosted short descriptions, and the data contains 191 linked decklist references marked as primers.

Repository-hosted descriptions are **strategic_reference** candidates for archetype/deck-plan concept extraction, not observed decisions. The MIT repository license does not automatically extend to third-party Moxfield/other primer links, which require separate provenance and terms review.

A keyword audit of the 56 competitive descriptions found broad concept-frequency cues including combo (43), grind/value (35), speed (30), interaction (18), combat (15), graveyard (13), stax (10), instant-speed (3), and board-wipe (2). These counts may guide curriculum coverage, but they are not action labels or expert recommendations.

### cEDH Yisan primer repository

Repository: `fecet/cedh-yisan`

Audited revision: `9bc57dbfcde655f3f9cab2b36dfddd8356055629`

Repository license: **MIT**.

The in-repository Yisan strategy/combo primer is a permissively licensed **strategic_reference** and may become an `expert_recommendation` source for deck-specific claims only after quality/adjudication review. Keep source-authored recommendations separate from Commander Gym's later judgment.

Do not treat separately linked Chinese/Moxfield primer content as covered by the GitHub repository license.

### headpunter/deck-primers

The repository publishes multiple Commander primers but exposes no public license in the audited repository surface.

Status: **reference_only / pending_permission**.

Do not import its prose into the training corpus unless permission/license terms are resolved.

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

### Magda Community cEDH Mega Primer

Repository: `mattrondel/mtg`

Audited revision: `1ee652cb96132d12dd457f20d6ba221cf14c0bb3`

Source file: `Magda_Community_CEDH_Mega_Primer.md`

This is a high-value deck-specific cEDH strategy source covering combo starters and enders, winning through varying stax pieces, protection, package/card reasoning, and detailed line navigation. The primer also attributes some staple-list choices to consensus among notable pilots in the Open Hands cEDH community.

No repository `LICENSE` or `LICENSE.md` was present at the audited revision, and the primer itself exposes no reuse terms.

Status: **reference_only / pending_permission**.

Do not copy its prose or promote community-consensus recommendations into `expert_recommendation` or training targets until permission/license scope is resolved. If permission is later obtained, preserve the exact repository revision and distinguish source/community recommendation from Commander Gym adjudication.

### Kuuusoda/magic-skill

Repository: `Kuuusoda/magic-skill`

Audited revision: `8894ab1eb94fd71d83e9cc1302b90398260e2a28`

Repository license: **MIT**.

The repository contains cEDH concepts, pod-dynamics material, decision-tree structures, source-aware strategy schemas, and synthesized analyses that point back to other strategy sources. It is useful for concept discovery and for designing Commander-specific benchmark questions.

Status: **strategic_reference / source-discovery framework**.

The permissive repository license does not establish expert authorship, observed decisions, or correctness of synthesized recommendations. Do not use its synthesized preferences as `expert_recommendation` or direct training targets without independent source verification and adjudication. Any third-party material it references must retain its own provenance/terms.

### DeckFlow Commander content knowledge base

Repository: `luntc1972/DeckFlow`

Audited revision: `244f010ee7a90a75140f2e074e747dc245a90a7b`

Repository license: **Apache-2.0**.

The repository contains generated Commander/cEDH knowledge-base summaries with source URLs and time-indexed clips, including material derived from third-party strategy videos. This is useful for finding strategically relevant source moments and concepts.

Status: **strategic_reference / source-discovery framework**.

Do not treat the repository license as licensing the underlying third-party video content or as evidence that generated summaries are expert supervision. Audit the original source and its terms separately before any source recommendation is admitted.

### Additional primer repository license checks

The following repositories expose strategically useful Commander/cEDH primer material but no `LICENSE` or `LICENSE.md` in the audited public surface:

- `AndrewLo42/Battle-Primers@b2b2c90e7ca4e95817daf70533738d0bcb4c73ee` — project explicitly aims to provide competitive-format primers for lending battle-box decks;
- `mbellucio/bia-attendance@2f7602031ae222b77e32ac54c113f1d8cf4a085e` — contains a detailed Vivi Ornitier cEDH primer with mulligan, interaction, engine, and combo guidance;
- `Sephiraxx/Mazos@c5605cf3b58f4f8dbba0ba4bc125bc9f936e3a27` — contains eight Spanish cEDH pilot guides spanning mulligans, combo lines, interaction, matchups, and common errors.

Status for all three: **reference_only / pending_permission**.

Public readability and apparent strategic quality are not enough to import prose or recommendations into the curriculum.

## Initial curriculum slices

The first dataset revisions should target bounded reasoning slices rather than broad "play Commander well" labels.

### Slice A — interaction allocation and table incentives

Target concepts:

- immediate win/fundamental-turn detection;
- sole-answer versus redundant-answer situations;
- personal cost of spending interaction;
- likelihood another player can answer;
- who should spend the answer when several players benefit;
- whether allowing resolution materially reduces one's own winning chances;
- whether a stax piece protects the table from a faster opponent;
- whether a permanent prevents one's own route to victory;
- preserving premium interaction when a threat is strong but not decisive;
- board-wipe/table-incentive asymmetry.

Preferred eligible evidence sources:

1. qualified native Commander Gym states with teacher/human/search adjudication;
2. project-owned synthetic scenarios grounded in strategic references but independently specified and adjudicated;
3. permissively licensed expert material after separate quality/adjudication review.

Reference-only sources may identify concepts but may not contribute copied prose or labels.

### Slice B — deck-plan inference and mulligans

Use qualified DeckKnowledge/strategic references plus native or project-owned seat-safe states to test whether a generally competent Magic player adapts to the bound deck rather than applying one universal mulligan heuristic.

### Slice C — recovery and long-game engine assessment

Focus on commander removal, board wipes, preserving independent engines, recast-tax decisions, graveyard/rebuild resources, and deciding when a board can absorb an opponent's medium-strength permanent rather than spending premium interaction.

### Slice D — multiplayer combat allocation

Focus on attack-target selection, crack-back risk, commander damage, whether a wide attack exposes needed engines, opponent-specific blockers/resources, and political/incentive consequences of allocating combat damage.

### Slice E — combo/win-attempt response windows

Focus on:

- Consult/Oracle-style win attempts;
- Breach/graveyard recursion windows;
- stack-versus-permanent interaction timing;
- holding interaction until the point of maximum certainty;
- deciding whether another player is both able and incentivized to answer.

Do not teach specific preferred responses until the acting-seat observation and legal choices are explicit and independently adjudicated.

## Project-synthetic benchmark seeds

Before #113 lands, synthetic scenarios remain **scenario-design artifacts**, not `BenchmarkCase`, `DecisionRecord`, or training examples. In particular, do not populate a required `chosen_action_id` merely to satisfy an existing schema when no observed action exists.

Every pre-#113 seed must carry these semantics:

```json
{
  "source_class": "project_synthetic",
  "benchmark_seed_only": true,
  "training_eligible": false,
  "observed_action": null,
  "expert_recommendation": null,
  "commander_gym_adjudication": {
    "status": "provisional"
  }
}
```

The initial seed should cover at least:

1. sole-answer immediate win;
2. avoiding redundant interaction when another player can and should answer;
3. holding a stax piece that protects against a faster opponent;
4. board-wipe/table-incentive asymmetry;
5. Consult/Oracle response windows;
6. Breach/graveyard response windows;
7. preserving premium interaction versus a value engine;
8. who-should-answer multiplayer allocation.

These are benchmark-design fixtures only until #113 can materialize them with canonical provenance, leakage groups, acting-seat information boundaries, and independent adjudication.

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

Until then, source discovery, concept extraction, and project-owned synthetic benchmark-seed design may proceed, but reference-only sources and generated legacy primer hypotheses must not be silently promoted into supervision.
