# Native draw policy contracts

The default free-draw policy accepts only whole optional controller draws certified
from the exact native ability definition. A granted trigger retains its native
ability ID while its callback names the receiving permanent; a unique certified
granted ID can therefore identify the effect without guessing from English text.
Missing/foreign hosts and conflicting counts remain uncertain. Draw punishers,
replacements, player effects, unknown modifiers, and near-empty libraries retain
inference.

Static granted triggers are analyzed like printed triggers: their event and
conditions can make them draw-sensitive; a draw effect after a combat event does
not itself constitute a reaction to drawing. The rest of the static ability,
including filters, stays in the conservative native scan.

An active card modifier remains uncertain unless every visible badge explicitly
carries the native `drawNeutral: true` certificate. This certificate covers all
underlying modifiers grouped into that badge and never its incomplete display
summary. It currently recognizes only unconditional end-of-turn prohibitions on
hexproof/shroud. Other badge families, unknown mixtures, and identified face-down
sources remain uncertified. Player effects still defer independently.

The native addition changes public metadata only. SDK effects, execution,
triggers, continuations, legality, cleanup, recording ownership, and seat masking
keep their existing behavior. The UI receives an optional field and needs no new
interaction. Engine projection tests exercise whole groups, mixed groups with
indistinguishable display summaries, conditions, unsupported keywords, and a
face-down source. Saved-state probes re-create the acting seat's native view and
verify actual library-to-hand movement for accepted draw choices.

The distinction between drawing and targeting was checked against the official
[Comprehensive Rules](https://media.wizards.com/2026/downloads/MagicCompRules%2020261106.txt):
drawing moves a library card into hand; hexproof and shroud constrain targeting.
No new game rule is implemented here.
