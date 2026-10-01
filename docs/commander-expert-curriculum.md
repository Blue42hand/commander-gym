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

Root repository license: **MIT for repository-owned software/documentation; do not assume this licenses user-submitted deck descriptions.**

The audited database contains 137 entries, including 56 marked `COMPETITIVE`; all 137 have repository-hosted short descriptions, and the data contains 191 linked decklist references marked as primers.

Primary-source rights review of the submission flow establishes authorship/permission-to-submit, but not downstream reuse rights. `submit.html` requires submitters to certify that they are the sole author of submitted content or have permission from other authors; `_includes/markdown/submit.md` says accepted submitted descriptions will be used on the DDB; `_includes/markdown/admin/privacy.md` identifies descriptions as user input and provides a DMCA takedown path. No audited submission/legal surface grants the DDB or downstream users an explicit reusable content license for those descriptions.

Therefore repository-hosted user descriptions are **strategic_reference / pending_permission**. They may guide source discovery, deck-plan taxonomy, capability coverage, and independently authored synthetic scenarios, but their prose must not be copied into `commander-expert-curriculum-v0` or promoted directly to `expert_recommendation` labels merely because the DDB reviewer team accepted them. Linked Moxfield/other primers remain separate provenance and rights domains.

The DDB review process remains useful quality evidence independent of licensing: accepted submissions are reviewed for cEDH viability and require descriptions of main/backup plans, strengths, and weaknesses. A keyword audit of the 56 competitive descriptions found broad concept-frequency cues including combo (43), grind/value (35), speed (30), interaction (18), combat (15), graveyard (13), stax (10), instant-speed (3), and board-wipe (2). These counts may guide curriculum coverage, but they are not action labels or expert recommendations.

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

### cEDH.guide archive

Repository: `rrdelaney/cedh.guide`

Audited revision: `5273ad8d0d3befefb9a41a94f15253e0d17cace2`

The archive contains unusually useful first-party and community-authored cEDH strategy material. In particular, `pages/articles/ben-loeb-interview.mdx` is an interview by Ken Baumann with Silicon Dynasty winner and veteran Malcolm/Tymna pilot Ben Loeb. It contains explicit source-expert guidance on disciplined mulligans, identifying short/long or grindy/all-in game context, comparing present versus future win equity before passing, enumerating stax constraints before a win attempt, reasoning through a complete win line before acting, and mana-versus-card resource valuation in multiplayer midrange.

The same archive includes community framing around cooperative interaction and avoiding kingmaking/spite/random allocation, aggregate analysis of 440+ recorded cEDH games, and Commander variance/evaluation methodology.

Repository metadata reports no license and the audited root tree contains no `LICENSE` file.

Status: **reference_only / pending_permission**.

Do not import prose or source recommendations into training data until permission/license scope is resolved. Tournament-play anecdotes named in the interview are promising reconstruction targets, but the article does not establish complete acting-seat observations or legal alternatives; do not convert them directly into `observed_action` or preferred-action labels.

### Curated cEDH DDB concept targets (pending-permission strategic reference)

The repository-hosted descriptions at `cEDH-Decklist-Database/cEDH-Decklist-Database@c9e503c4e6be77aa90b2a472c34cbc65ef2d6725` are high-value **strategic_reference / pending_permission** concept anchors. The repository's MIT software license must not be used as the content-rights basis for user-submitted descriptions.

Initial high-information candidates:

- `xhy3u2jsb1d` — **Tana Tymna Turbo Naus**: speed-versus-grind framing and stronger mulligan options from persistent engines;
- `ijjs5kgobeyy` — **Plagon Blink**: generous mulligans enabled by command-zone refill, plus pivoting between early tempo and later storm;
- `x7txc2sqb07s` — **Ellivere Stax**: proactive static interaction, combat-driven value, playing through one's own stax, and board-wipe vulnerability;
- `fv0q3xbxye4y` — **Brigid Cradlestorm**: fast-plan mulligan requirements, stax backup plan, and explicit post-board-wipe recovery weakness;
- `x6ljhq8ypi7d` — **Rocco Creature Tutor**: repeated win-attempt resilience in the face of interaction;
- `x6pk5r7yr36` — **K'rrik, Son of Yawgmoth**: pod/deck-plan inference around stax, countermagic, graveyard hate, and expected win window;
- `xwgigg3i3hym` — **Elsha Top**: timing flexibility, advancing one's own plan while hindering others, and interaction density;
- `x1yub0v2fnz` — **Najeela Tempo**: combat pressure as both an independent win vector and a setup for infinite-combat lines.

Use these entries only to guide source discovery, capability coverage, synthetic scenario design, and later adjudication queues. Do not copy the user-submitted prose or treat it as `expert_recommendation`, `observed_action`, or preferred-action supervision until rights are independently established. Linked Moxfield primers remain separately licensed sources.

### fbatista Krark/Sakashima tournament report

Source: GitHub Gist `fbatista/ea5a230f6b32dbf8f565fb68d962a04e`, `report.md`

Audited raw revision: `c31ccf72a2f8ebf079ed1e2e42b1bcfc47725ddc`

No explicit reuse license was located.

Status: **reconstruction_candidate / strategic_reference / reference_only / pending_permission**.

Round 4 is unusually well narrated but still insufficient for a native legal-action label. The report says a prior Gitaxian Probe exposed Tasigur's hand; at the key state Tasigur protects Jace at 3 loyalty, Fierce Guardianship is known on top, and Vampiric Tutor, Demonic Consultation, and Thassa's Oracle are reported in hand. The Krark/Sakashima pilot reports holding only Swan Song and Finale of Promise, casts Intuition at end step, and later self-critiques the pile while naming Cephalid Coliseum / Brain Freeze / Grapeshot as a better pile.

Preserve this as a source-narrated observed action plus source self-critique/recommendation candidate. Do **not** treat the later recommendation as independent Commander Gym adjudication. Complete library/graveyard contents, mana/resources, and the full legal Intuition search space are not established, so do not invent `chosen_action_id` or a legal-action set.

The finals Intuition negotiation and semifinal threat-redirection/table-talk are high-value multiplayer incentive evidence, but the pilot's speech is strategic communication rather than an Argentum-native legal game action. Keep it as reasoning/reference context unless #72/#114 intentionally defines a separate communication-action contract.

### CPDH.guide competitive Pauper Commander interviews

Repository: `cpdhleague/Guide-book`

Audited revision: `40609e8b71e53ea8c80ec18851f54c7bf9e3e86b`

No repository/content license was located in the audited public surface.

Status: **Commander-variant strategic_reference / reference_only / pending_permission**.

The archive contains recent competitive Pauper Commander winner interviews with unusually useful multiplayer decision narration. The Hawkeye/Cloudy Commons Cup IV interview includes waiting to deploy a commander into a lower-interaction window, exploiting opponents spending removal elsewhere, accepting combat risk because blocking would sacrifice a next-turn win, recognizing an overconfident declined draw, and identifying a counter war among opponents as the decisive tapped-out window. The Hudson Valley Disciple of Deceit interview adds source recommendations about preserving cards in hand for overlapping combo routes, mulligan discipline, forcing opponents to hold interaction instead of developing, and late-game pivoting after control players exhaust resources fighting each other.

These are retrospective interviews, not native trajectories: they do not establish complete acting hands, every legal action/target, or all hidden information. Preserve source-stated action/reasoning separately from Commander Gym adjudication and uncertainty.

cPDH is a distinct format domain. Its multiplayer incentives are useful transfer hypotheses, but card-pool, interaction-density, combo-structure, and speed priors must not silently become cEDH labels. Carry an explicit format-domain tag and map only abstract capability concepts unless a scenario is independently reconstructed/adjudicated.

### TrainingARK reconstructed cEDH scenarios

Repository: `EshaanS/TrainingARK`

Audited revision: `db7108e5a5893144359b579d0f7ad4d2746282a8`

No project/content license or reuse terms were located in the audited repository surface.

Status: **reconstruction_candidate / strategic_reference / reference_only / pending_permission**.

TrainingARK is unusually close to the desired decision-artifact class: an interactive four-player cEDH training simulator built from real or representative game states, with prompts, choices, explanations, and `best|ok|blunder` quality labels authored by each scenario creator.

Those labels are **source-author judgment**, not Commander Gym adjudication or ground truth. The schema does not provide an independently verified observed-player action or complete native legal-action enumeration. Do not map TrainingARK choice IDs directly to #114 `chosen_action_id`.

The UI is acting-seat aware, but the raw data contract is not necessarily seat-safe for ML ingestion. Opponent hands are hidden by the viewer, yet hidden-zone entries can still retain full `Card` objects including true card names. Any future import must derive an actor-visible projection: preserve the acting player's private information, public zones, and explicitly revealed opponent cards; strip or opaque hidden opponent hand/library identities. Renderer visibility is not evidence of data-level information safety.

Scenario JSON also lacks enough canonical provenance to infer training eligibility by itself: source-game URL/event/game ID, real-versus-representative origin, source rights, and confidence are not required fields. #113 must wrap any accepted scenario with exact source revision, scenario author, origin/reference, rights, and leakage-family metadata.

If rights/provenance are later resolved, the safe promotion path is: (1) #113-bind the exact source and origin; (2) derive the seat-visible state; (3) retain author labels as source-author judgment with uncertainty; (4) independently establish observed action and native legal alternatives before #114 action labels; and (5) independently adjudicate preferred alternatives before training eligibility.

### cEDH Wiki (Fandom)

Source: `https://cedh.fandom.com/wiki/CEDH_Wiki` and individual strategy/commander pages.

License: **CC-BY-SA 3.0 for community text unless otherwise noted**. Fandom's licensing guidance states that wiki contributors retain copyright while licensing submitted text under Creative Commons Attribution-ShareAlike; the cEDH Wiki pages themselves display the CC-BY-SA notice.

Status: **licensed strategic_reference / quality-unverified community source**.

This is the first audited external cEDH strategy source in this workstream with an explicit reusable content license rather than an inferred repository-software license. It contains reusable archetype/deck-plan statements relevant to #115, including:

- K'rrik: early repeated win pressure forcing opponents to choose between board development and holding interaction;
- Gitrog: resilience/recovery framing and the importance of interacting before a difficult-to-stop graveyard engine is established;
- Azami/Orvar: draw-go control, holding interaction, value-engine development, and long-game win setup;
- Anje: proactive commander deployment, low-mulligan pivoting toward midrange/beatdown, and graveyard/stax vulnerability;
- Reanimator/Polymorph strategy pages: deck-speed, setup, resource expenditure, and recovery constraints.

Licensing does **not** establish expert quality. The wiki is openly community-edited and does not provide per-claim expert credentials or observed decision provenance. Use it as a licensed strategic-reference/concept source, not as `expert_recommendation`, `observed_action`, or preferred-action supervision without independent quality review/adjudication.

If text is imported rather than merely used for concept discovery, #113 must preserve page URL/revision, attribution, CC-BY-SA-3.0 license metadata, and share-alike obligations. Linked external primers and videos retain their own provenance and rights and are not covered by the wiki page license.


### LearnCEDH expert strategy and mixed-source courses

Primary site: `https://learncedh.com/`

Creator/maintainer: Evan Pierce / FreedomWaffle.

Audited pages include:

- `https://learncedh.com/coaching`;
- `https://learncedh.com/decklists/gitrog`;
- `https://learncedh.com/courses`;
- `https://learncedh.com/intermediate-course/threat-assessment`;
- `https://learncedh.com/intermediate-course/kingmaking`;
- `https://learncedh.com/intermediate-course/sandbagging`;
- `https://learncedh.com/intermediate-course/the-window`;
- `https://learncedh.com/advanced-course/heuristics-goldfishing`;
- `https://learncedh.com/advanced-course/hidden-information`.

LearnCEDH is the strongest audited **expert-quality / rights-unresolved** Commander strategy source in this workstream so far. Its site identifies Evan Pierce / FreedomWaffle as creator and maintainer and presents named cEDH coaches. Independent tournament evidence materially corroborates expert provenance rather than relying only on site self-description: a 2026-09-25 cEDHStats snapshot ranks Evan Pierce #1 with 81 tournaments, 16 tournament wins, and 55/81 top cuts, and ranks Ian Flannery #5 with 114 tournaments, 15 wins, and 68/112 top cuts. These records are evidence of competitive experience only; tournament databases remain metagame/expertise evidence rather than decision supervision.

The site-native Evan Pierce deck material is decision-relevant strategic reference. The audited Gitrog page covers mulligan priorities, early/mid/late game plans, adaptation to fast-combo and stax pods, interaction allocation, resilience/recovery, and matchup timing. No explicit Creative Commons/permissive content license or downstream redistribution/training grant was located on the audited LearnCEDH homepage, Start A Community page, coaching page, or Gitrog page.

Status for site-native LearnCEDH prose: **expert strategic_reference / pending_permission**.

Do not copy site-native prose into `commander-expert-curriculum-v0` or promote it directly to reusable `expert_recommendation` labels unless a compatible permission/license basis is established. Before permission, it may guide capability taxonomy, source discovery, independently specified synthetic scenarios, and adjudication queues.

The LearnCEDH course corpus has an additional provenance boundary: it is explicitly mixed-source/derivative rather than simply LearnCEDH-authored. The Courses page thanks **Eisenherz, Playing With Power, Lemora's Cards, and Rebell Lily** for providing videos used to create the courses. Individual lessons attribute source videos to those upstream creators, including:

- Eisenherz — Threat Assessment, Kingmaking, Sandbagging, Hidden Information, Heuristics/Goldfishing, and other advanced/intermediate lessons;
- Rebell Lily — `When to Combo Off (The Window)`;
- Playing With Power MTG — beginner-course material such as `What is cEDH?`;
- Lemora's Cards — deck/staple course material.

These lessons are highly relevant to #115 capability slices such as multiplayer threat assessment, interaction allocation, table incentives/kingmaking, hidden-information discipline, and combo-window timing. They remain **reference_only / pending_permission** unless the derivative lesson itself has a rights basis that covers reuse.

A future site-level LearnCEDH permission must not be assumed to license third-party-derived course content. For any course lesson admitted through #113, preserve at minimum:

1. exact LearnCEDH lesson URL/version/date;
2. upstream creator identity;
3. upstream video/source URL when recoverable;
4. rights/permission basis that covers the derivative lesson;
5. source-class and leakage-family metadata;
6. the distinction between source recommendation, observed action, and Commander Gym adjudication.

No explicit reusable-content grant was located in the audited public surfaces for the upstream course creators during this pass. Their material therefore remains separately unresolved rather than inheriting LearnCEDH permission by implication.

LearnCEDH and the cEDH Wiki solve opposite halves of the source problem: LearnCEDH has much stronger independently corroborated expert provenance but unresolved/mixed rights; the cEDH Wiki has explicit reusable CC-BY-SA rights but weak per-author expert provenance. Do not combine rights or expertise across those sources by inference.

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

#### Yisan quality/adjudication conclusion

Primary-repository review of `fecet/cedh-yisan@9bc57dbfcde655f3f9cab2b36dfddd8356055629` confirms that the repository is explicitly a competitive-EDH Yisan primer. The root license is MIT (copyright 2021 fecet), and the repository-authored README contains deck-specific inclusion/exclusion judgments, infinite-mana and outlet lines, backup-loop guidance, and opening-hand sequencing for Vitalize-like effects.

Do **not** promote this source to `expert_recommendation` supervision yet. Keep it as `strategic_reference` / source-recommendation candidate evidence.

Reasons:

- no independent evidence was located for author tournament results, testing volume, or recommendation accuracy;
- the README explicitly assumes prior material from an externally linked Moxfield/BRC primer and separately links a Chinese primer, so inherited claims are not safely attributable to the MIT repository;
- the strongest self-contained material is mostly deterministic combo sequencing rather than the multiplayer-transformation slices prioritized by #115;
- the source does not provide observed acting-seat states, pod context, legal-action sets, or independently adjudicated alternatives.

Narrow in-repository claims may seed licensed concept/deck-plan anchors or independently adjudicated synthetic work, but they must not become preferred-action labels directly. Keep linked BRC/Chinese content under separate provenance and terms.
