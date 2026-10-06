# Revisions, pilots, and testing

## Preserve the candidate

Use the user's current authorized deck collection and its actual interface. Discover its current source of truth before saving; do not treat the old `commander-test-lab` Library/SQLite registry or bundled scripts as current authority. Do not change repository instructions or migrate storage as a side effect of deckbuilding.

Keep a distinct candidate with:
- Exact list and stable identity/fingerprint, commander(s), parent revision, creation date, and change rationale.
- Brief/constraints, budget basis, owned-card assumptions, price and Oracle/source snapshots, legality/bracket review, and unresolved checks.
- Inclusion/cut decisions, functional audit, and meaningful alternatives.
- Matching primer/pilot version and hypotheses with links to evidence when it exists.

Preserve earlier lists, pilots, sources, rejected proposals, and contrary observations. Do not overwrite the adopted deck or present a candidate as adopted without the user's instruction or applicable existing authority. When persistence is unavailable, provide a complete self-contained candidate artifact and disclose that it was not saved to the collection. Saving a candidate does not require launching tests.

## Minimum pilot and optional full primer

Always accompany a completed 100-card build/revision with usable piloting information. Scale detail to the request; a full primer is optional.

Cover the deck's objective, realistic keep/mulligan criteria, opening development, sequencing dependencies, when to commit versus hold resources, win routes, interaction priorities, commander removal, and recovery. State conditional tutor/target priorities rather than prescribing one answer regardless of the board. Explain special lines from exact rules, including constraints and interaction windows.

Play to win within the brief and table agreements. Do not spend interaction or make opponents misplay to showcase a preferred card. Do not encode decisions using unrevealed opposing cards, omniscient future draws, or outcomes learned after the decision. A broad pilot plan supplies strategic intent; native legal actions and the actual permitted observation govern execution.

For a fuller primer, add package-level explanations, critical turn/decision patterns, matchup-specific adjustments clearly labeled as such, common pitfalls, recovery contingencies, and a compact change/evidence history. Keep the primer aligned to the exact list; changing prose is not evidence that a backend actually executes that policy.

## Optional maybeboard

Keep it separate from the 100. For each entry record its proposed role, why it might improve this deck, the possible cut or package trade, and an observation/test that would change the decision. Examples of useful hypotheses are earlier reliable colored mana, fewer stranded expensive spells, more sustainable card access, or better recovery after a wipe. Do not promise gains that have not been measured.

## Paper analysis versus played evidence

Label theoretical sequencing, probability arithmetic, role/curve checks, and rules analysis as paper work. Never describe imagined turns as chat goldfishes, played games, or simulation results. Do not fabricate samples, seeds, logs, wins, or confidence intervals.

Use the user's actual manual Archidekt goldfishes and historical Forge games as their recorded kind of evidence, retaining pilot/backend and scope. A manual unopposed line, a simplified opening sample, a historical Forge game, and a native Argentum multiplayer game do not measure the same thing. Do not pool their win rates or infer current engine capability from an old test.

Refine from concrete observations: was a card drawn, usable, stranded, mistimed, redundant, or essential; did colored mana or tempo block the plan; did the deck recover; was a win available and correctly pursued? Separate construction problems from pilot mistakes, native-engine failures, and missing information. An unsupported card is an implementation limitation, not a fair measure of its deckbuilding value.

## Handoff to current Commander Gym / Argentum

Propose tests when they would answer a real construction question. Execute only within explicit user requests and current authorization, including spending bounds. Do not automatically launch paid/API tests or raise limits. Do not activate the legacy Python/assistant-adjudication or Forge pathways merely because the older skill is installed.

Inspect the current Gym interface and guidance. Pin the exact candidate, matching pilot, backend/rules/catalog version, opponent lists and policies, seating/seeds where supported, and the question under study. Choose challenging opponents appropriate to the brief; do not weaken opponents to flatter the deck. Keep a general benchmark question separate from a specific-meta question and disclose proxy lists or assumptions.

Preserve Argentum's native rules and legal-action authority. Never repair a test by inventing card effects, bypassing legal actions, leaking hidden state, or patching gameplay with assistant adjudication. Report unsupported mechanics and engine failures through the proper current workflow. A deckbuilding request does not authorize repository guidance changes or a broader engine redesign.

Before a comparison, state the hypothesis, changed slots/pilot, success/failure observations, relevant limitations, and stopping scope. Keep opponents and material settings comparable; record any differences. Use the current system's actual reproducibility and review gates rather than inventing commands or guarantees.

Retain failed/censored runs and adverse observations. A time cap does not award a win to the apparent leader. Report actual sample counts with denominators, dependency/coverage limits, and uncertainty. Small or selected samples diagnose behavior; do not promote them into precise real-world win rates. Do not tune pilots with future hidden draws or discard unfavorable outcomes.

Recommend a revision only with its tradeoff and evidence level. A reasoned paper improvement can be proposed as such; it does not need manufactured test support.
