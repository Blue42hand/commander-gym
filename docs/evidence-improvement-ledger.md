# Offline improvement ledger (schema 1)

Implementation contract for `commander_gym.improvement`, subordinate to
[pilot-learning-data-architecture.md](pilot-learning-data-architecture.md),
[ARCHITECTURE.md](../ARCHITECTURE.md), and the existing #113/#114/#115 workstreams.
This does not change the roadmap, recording contract, training eligibility,
retention, pilot policy, or deployment authorization.

The ledger stores private candidate provenance and append-only transition records
in a separate 0700 directory under the configured storage `catalog` route.
For Tolaria, an operator can provision `/var/lib/commander-gym/catalog/improvements`
with private parents. Do not put it in an immutable run artifact manifest or Git.
SQLite transactions serialize concurrent ingestion and revision changes. Back up
this ledger alongside the private evidence. No automatic deletion exists.

## Recorder handoff

The hourly analysis owner can call `ingest_report` after `finish_analysis` succeeds.
The adapter reads the existing finalized manifest and
`analysis/<version>/{review,report}.json`, verifies their identities and exact report
hash, and rechecks source stability. It never writes either recorder file.
The dedupe key includes run ID, artifact hash, analysis version, report SHA-256 and
selected finding IDs. The candidate ID hashes schema version, category and an
explicit `family_key`. Repeated reports keep individual references and assessments;
correlated runs count once through an explicit `correlation_group`. Choose that group
for the source game/derivation family, including reruns sharing the same scenario.
A fingerprint is an adjudicator assertion; the tool cannot infer causal equivalence
or certify statistical independence. Review fingerprints before promotion.

The finding adapter expects `finding_id`, `category`, and nonempty `evidence_refs`
in each report finding. Those names are an explicit adapter profile, not inferred
from prose. Unsupported/missing fields block ingestion rather than fabricate links.
It accepts the recorder's string `artifact_hash` identity. The analysis skill's
conceptual hash object must be mapped to the recorder identity before report
completion. This seam needs parent/recorder review before automation wiring.

A proposal selects `finding_ids` and requires `category`, `family_key`, `confidence`
(low/medium/high), `severity` (informational/low/medium/high/critical),
`priority_reason`, `evidence_quality`, and explicit arrays for
`alternative_explanations`, `counterevidence`, `missing_evidence`. Empty arrays
mean explicitly none recorded, not proof of absence. Reports remain private;
intake copies references and assessments, not observations or hidden hands.

```sh
python -m commander_gym.improvement --ledger "$PRIVATE_LEDGER" ingest \
  --run-directory "$PRIVATE_RUN" --version 1.0.0 \
  --correlation-group "$SOURCE_FAMILY" --proposal "$PRIVATE_PROPOSAL"
python -m commander_gym.improvement --ledger "$PRIVATE_LEDGER" claim "$CANDIDATE" --actor worker
python -m commander_gym.improvement --ledger "$PRIVATE_LEDGER" show "$CANDIDATE"
```

Ingestion is one atomic transaction: repeating it after a crash returns the same
candidate without duplicate evidence. Claims are separate one-hour work leases;
busy work is skipped, expired work resumes with a fresh token. Every transition
checks the expected revision and, if claimed, current actor/token/live lease.
An obsolete worker cannot transition or release its successor's claim. Release
before the lease expires or resume with a fresh claim. A failed transition commits
nothing. Manual unclaimed transitions remain available to explicit operators.
Completed report ingestion does not itself trigger work, notifications or spending.

## Transition contract

`insufficient_evidence -> hypothesis -> reproduced -> implementation_validated -> measured -> adopted`.
Any pre-adoption state may be rejected; adopted records may be rolled back.
History is retained, including rejected/rolled-back records. No state implies a
live deployment. New attempts after rejection use a new explicit family revision.

- **Hypothesis:** falsifiable statement; decision/reliability metric, increase/decrease
  direction and positive minimum delta; exact baseline commit; bounded scope and
  experiment; frozen baseline and held-out fixture artifact IDs with seat-safe
  visibility and disjoint source/derivation groups; regression and rollback plans.
- **Reproduced:** content-addressed repro receipt, deterministic offline command,
  observed failing baseline, tests-before-fix attestation or explicit reason infeasible.
- **Implementation validated:** PR, exact commit, independent approving reviewer,
  passing CI artifact, and changed scope contained in the hypothesis scope.
- **Measured:** exact baseline and changed commits, verification artifact, the same
  frozen fixture sets, numeric before/after values with nonzero denominator, all
  acceptance thresholds met, held-out pass and no regressions. Strategic candidates
  additionally require a shadow qualification artifact.
- **Adopted:** explicit approval actor/reference. Model-judgment and primer changes
  require at least two independent source groups and no low-confidence assessments.
  This is a minimum guard, not proof that two games suffice; reviewers must assess
  coverage, alternative explanations and the bounded experiment. A single game's
  winner, win rate or placement cannot serve as the acceptance metric.
- **Rejected/rolled back:** reason and evidence reference; actual rollback remains
  an independently authorized implementation action.

Fixture and measurement references reuse `sha256:` artifact IDs from `storage.py`.
Native replay, canonical Decision/Binding lineage, annotations and dataset splits
remain owned by their existing contracts. This module does not emit training rows.
Seat-safe labels and review/CI/measurement values are attestations: the reviewer
must inspect referenced artifacts. No evaluator assertion is silently treated as
native authority. Keep privileged critics, future outcomes and opponent information
out of pilot-visible fixtures and labels.

Use `transition CANDIDATE --revision N --target STATE --details PRIVATE_JSON --actor OWNER`
(and `--claim-token TOKEN` for claimed work). `history` reads complete event records;
`release CANDIDATE --actor OWNER --token TOKEN` releases owned live work. CLI output
is private metadata: do not paste it into public issues/PRs.

## Issue/PR integration boundary

No issue automation is implemented. Before publishing findings, parent review
should approve a minimal sanitized issue template: category, falsifiable hypothesis,
synthetic offline reproduction, acceptance metric, scope, regression/rollback plan,
and linked PR/commit/CI. Private candidate IDs, run IDs, report links and source
payloads stay in the private ledger; the private record links outward to the public
PR. One issue per deduplicated engineering problem, only after triage, avoids mass
publication. This proposal does not modify `.github` templates.

No paid calls, games, guidance rewrites, arbitrary policy updates or automatic
adoption/deployment are authorized by this ledger. Independent review and measured
qualification are prerequisites, not actions it performs.
