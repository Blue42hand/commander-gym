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

## Tapped-land development windows

The native `selfTappedLandRampAbilities` list certifies whole activated effects
matching a basic-land search into the controller's battlefield tapped, with one
to three cards and no additional effect. Timing defaults hold these activations
until the acting player's main phase or an opponent's end step. Known whole optional draw and library
reorder-only triggers may finish first. Unknown stacks, payment corrections,
unknown combat data, creature-sacrifice combat responses, and explicit deck
reaction-timing overrides keep their native decision domain. Noncreature sources
normally wait during combat; a landfall deck can override that default.

Small complete return/untap menus also receive literal option facts after context
compression. The returned permanent and untap target retain their own names and
current states, without table or shared-value references. The summary is optional:
its complete request is checked against the existing admission budget, and an
otherwise fitting request proceeds without it when needed. Native choices are
never truncated or removed to fit this diagnostic context.

## Variable player draw targets

`beneficialPlayerDrawX` certifies a whole native instant or sorcery effect that
makes exactly one unconstrained targeted player draw X cards. It requires the
same bound variable as the native target and excludes additional effects,
triggers, static abilities, replacement effects and activated abilities. A
spell's self-shuffle resolution destination is separate from its draw effect.

The default policy narrows these spells to the caster only when the entire
native affordable X range is safely below every player's library size and no
unknown or draw-sensitive battlefield effects are present. It requires positive
X; actual decking opportunities, unclear X bounds, prebound amounts, modifiers,
payment corrections and explicit deck target overrides retain inference. The
model still chooses between the remaining spells and decides how many cards to
draw. No spell name is part of this policy.

The saved game30 callback5456 reproduces the mistaken opponent draw. Both
branches cast the same native spell for X=1; the corrected branch changes only
the player target. Native probes separately verify masked state equality,
acceptance and actual hand changes after resolution. This does not establish
whole-game strength or guarantee model reasoning quality.

## Ordinary cycling timing

The native `cyclingSensitive` flag scans cycling, discard, draw, zone-change,
related mana/activation event patterns and conditions, replacements, and relevant
keyword abilities. Printed cycling itself does not make a card sensitive. A
replacement or trigger confined to battlefield entry cannot react to the cycled
card's hand-to-graveyard discard or library-to-hand draw, so that event boundary
is distinguished from general zone changes. Granted triggers are scanned by
event/condition, independently of their eventual draw effect. Missing facts stay
unknown to the timing policy.

Ordinary, fixed-cost, non-granted `CycleCard` offers can wait until the caster's
main phase or an opponent's end step. The native cost/offer remains authoritative.
Defaults preserve floating or restricted mana, low or unknown libraries, relevant
combat, hidden/incomplete stacks or battlefields, unreviewed modifiers, cycling
or discard payoffs, draw-sensitive mechanics, and explicit deck timing overrides.
Known wholly optional draw/reorder stacks can finish first. Typecycling and X
cycling retain their own decision domain.

A condition-progress badge is informational and now carries the native
`drawNeutral` certificate; no policy trusts its English text or effect ID. Native
whole-badge certification also covers the discard/draw of ordinary non-targeted
cycling. Other badges and the card's own abilities remain independently checked.
Unknown badges still defer. Saved-state fixtures must be regenerated from the
matched native projection and compared against the authenticated originals;
changing annotations by hand is not qualification.
