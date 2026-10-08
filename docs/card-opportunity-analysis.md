# Private card opportunity analysis v1

This offline derived slice supports the existing pilot-learning evidence architecture.
It does not change recorder behavior, pilot policy, decks, training exports, or promotion gates.
Reports are judgments/metrics beside immutable source records, never raw evidence.

```sh
python -m commander_gym.card_opportunities /private/runs/RUN_ID --generator-revision EXACT_GYM_COMMIT
```

The CLI verifies the finalized manifest and every artifact before and after analysis.
It writes deterministic content-addressed JSON under `analysis/card-opportunities-v1/`
with private directory/file modes 0700/0600. It prints the relative report path and
coverage counts. Sources and manifest remain unchanged. Reports contain private
identities and context: do not publish them in issues or PRs.

## Availability and denominators

The unit is one seat, exact recorded context cohort, distinct observed state, and
visible card identity. Multiple copies of a card, target/mode offers, repeated
callbacks, starts/completions and retries in that state count once. Raw source
references and decision IDs remain inspectable. Different observed states still
count separately; the metric does not claim they are independent strategic decisions.
Per-card distinct observed-turn lists provide a less noisy second denominator.

Own visible zone cards count as seen. Own hand cards count as hand exposure.
Opponent private cards are excluded even if accidentally present. Native printed
identity is preferred; otherwise grouping uses visible name, explicitly recorded
as such. Neither basis proves deck membership (created, stolen, transformed cards
can appear); recorded deck/pilot/binding pins remain context, not attribution.

Availability separates native affordable play offers, unaffordable offers, absent
offers, and unknown affordability/identity. Absent offers do not prove a card was
unplayable throughout a turn. Legality does not establish strategic value. Current
canonical Gym offers can lack `sourceEntityId`: these stay unattributed/unknown;
labels and semantic-ID strings are never parsed to guess card identity.
Structured/payment callbacks are excluded with coverage counts.

The selected-play rate is selected / (selected + held) among observed affordable
opportunities with known choices. Unknown choices are separately counted and
excluded from that denominator. Holds require a known other selection. Observed selections are also counted independently
of affordability; selections without confirmed opportunities never enter that rate. Conflicting
selected cards in the same observed state produce an unknown choice. A selected
callback is not proof of native execution; execution remains unknown.

For each card, the report lists observed hand turns, observed playable hand turns,
and observed unaffordable hand turns with the supporting windows. These sets may
overlap when availability changes during a turn. First hand sighting and the last
observed seat turn expose late-game censoring. Observed subsequent turns are a lower
bound, not elapsed turns. Draw turn, total turns held, and turns remaining after draw
remain null: callback samples do not establish complete zone transitions or full
turn coverage. First/last turns can be partial. No report calls a card dead all game.
Recorded phase/step/active/priority context supports future grouping; there is no
invented “worth countering” label or count of target permutations.

## Sources, coverage, and usefulness

The CLI prefers masked callbacks per seat, including starts whose choice remains
unknown. Native masked observations are used only for seats without callbacks,
with unknown choices. It does not join administrative transitions by timing or card
name. Other native source rows and unsupported perspectives are counted as excluded.
Incomplete recording stays marked with manifest gaps; finalized is not complete.
The first slice does not pool games or context revisions.

`analyze_artifact(layout, source_artifact_id, annotation_artifact_ids=..., generator_revision=...)`
provides the separate canonical raw-evidence path. It validates the existing raw
contract and loads exact immutable AnnotationStore artifacts. Do not conflate a
manifest file's byte digest with an ArtifactStore blob's canonical digest.

Existing AnnotationRecord type `card_usefulness.v1` targets one decision and has
payload fields `card_definition_id`, `basis` (`ex_ante_choice` or `observed_effect`),
`label` (`helpful`, `neutral`, `harmful`, `unknown`), `rationale`, numeric `confidence`
(0–1), `counterevidence` (list), and `evidence_pointers` (nonempty JSON-pointer list).
It must identify the selected visible card and exact source blob. Ex-ante pointers
resolve only inside that decision's observation/legal actions or chosen action ID.
Observed-effect pointers resolve only inside that decision's outcome. The latter
are retrospective judgments, not causal estimates or pilot-visible training input.
Annotator, revision and full supporting annotations remain in the private report.
Repeated artifacts do not inflate counts; differing labels retain counterevidence
and aggregate to unknown. Caller supplies an explicit annotation set; superseded
revisions are not silently selected. Unannotated selections remain unknown in both
channels. The CLI has no annotation source for native-only records yet.

Later run outcomes remain source context and never imply helpfulness. These metrics
can identify investigation candidates; strategic changes still require evidence,
falsifiable hypotheses, held-out tests, bounded experiments and explicit adoption
gates described by the existing evidence-to-improvement workflow.
