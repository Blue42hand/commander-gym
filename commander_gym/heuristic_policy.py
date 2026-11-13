"""Heuristic-first strategy over masked Argentum offers; no engine simulation.

Opening-hand checks and choice priors adapt Argentum 12ab2f0's MIT-licensed
OpeningHandReader/ChoicePriors (copyright 2026 Vincent Bons). See THIRD_PARTY.md.
All actions are reselected from the current menu; no queued IDs survive a callback.
Unknown mechanics, payments and strategic ambiguity explicitly defer to the model.
"""
from collections import Counter
from dataclasses import replace
import json
import re
from time import monotonic
from types import SimpleNamespace

from .pilot import (ArgentumActionChoice, ArgentumDecisionChoice, PilotContractError,
                    _require_observation, validate_pilot_choice)
from .pilot_composition import SubsystemDecision
from .openai_responses_pilot import OpenAIResponsesPilot

SYMBOLS = re.compile(r'\{([^}]+)\}')
COLORS = {'W': 'white', 'U': 'blue', 'B': 'black', 'R': 'red', 'G': 'green', 'C': 'colorless'}
BASIC = {'Plains': 'W', 'Island': 'U', 'Swamp': 'B', 'Mountain': 'R', 'Forest': 'G'}
UNSAFE_TAGS = {'REMOVAL', 'EXILE_REMOVAL', 'SWEEPER', 'COUNTERSPELL', 'PACIFISM',
               'COMBAT_TRICK', 'PROTECTION', 'TRIGGER_LOCK', 'COUNTER_LOCK'}


def mana_cost(cost):
    """Only ordinary generic/colored costs are estimated; special symbols defer."""
    if not isinstance(cost, str):
        return None
    symbols = SYMBOLS.findall(cost)
    if ''.join('{' + s + '}' for s in symbols) != cost:
        return None
    if any(s not in COLORS and not s.isdecimal() for s in symbols):
        return None
    pips = Counter(s for s in symbols if s in COLORS)
    return sum(int(s) if s.isdecimal() else 1 for s in symbols), pips


def ids_in(state, zone, owner=None):
    return [i for z in state.get('zones', [])
            if z.get('zoneId', {}).get('zoneType') == zone
            and (owner is None or z['zoneId'].get('ownerId') == owner)
            and z.get('isVisible') is True for i in z.get('cardIds', [])]


def affordable(action):
    flags = [action[k] for k in ('affordable', 'isAffordable') if k in action]
    return bool(flags) and all(v is True for v in flags)


def keyword_set(card):
    return set(card.get('keywords', [])) | set(card.get('abilityFlags', []))


class PolicyNotApplicable(Exception):
    """An expected policy coverage gap; route to the next layer."""


class MaskedHeuristicSubsystem:
    name = 'argentum-masked-heuristics'
    version = '1'

    def __init__(self, facts):
        self.facts = facts
        # Reuse the existing native response/parameter validator, never its client.
        self.validator = OpenAIResponsesPilot(
            client=SimpleNamespace(responses=SimpleNamespace(create=self._never_dispatch)), model='heuristic-validator')

    @staticmethod
    def _never_dispatch(**_):
        raise PilotContractError('heuristic validator cannot dispatch provider requests')

    def try_choose(self, observation):
        _require_observation(observation)
        from .local_callback_context import current_cancel
        from .lmstudio_pilot import LMStudioPilotError
        cancel = current_cancel.get()
        if cancel is not None and cancel.is_set():
            raise LMStudioPilotError('cancelled')
        started = monotonic()
        if observation.get('nativePaymentError') is not None:
            return SubsystemDecision(reason='heuristic-payment-correction')
        try:
            result = self._choose(observation)
        except PolicyNotApplicable as exc:
            result = SubsystemDecision(reason=str(exc))
        metadata = {**result.metadata, 'policyVersion': self.version,
                    'factsSha256': self.facts.digest, 'engineRevision': self.facts.engine_revision,
                    'policyWallTimeMs': round((monotonic() - started) * 1000, 3)}
        if result.choice is None:
            return replace(result, metadata=metadata)
        # Reuse request-local native semanticId and decision validation. Invalid
        # heuristic output is an implementation failure, not a model fallback.
        c = result.choice
        if isinstance(c, ArgentumActionChoice):
            offer = next(a for a in observation['legalActions'] if a['actionId'] == c.action_id)
            output = {'channel': 'action', 'choice': {'semanticId': offer['semanticId'], 'params': dict(c.params)}}
        else:
            output = {'channel': 'decision', 'response': {k: v for k, v in c.response.items() if k != 'decisionId'}}
        validated = self.validator._choice_from_response(
            {'status': 'completed', 'output_text': json.dumps(output)}, observation, retry_count=0)
        validate_pilot_choice(validated, observation)
        return SubsystemDecision(choice=replace(validated, metadata={'heuristic': metadata}), metadata=metadata)

    def _handled(self, choice, reason, **metadata):
        return SubsystemDecision(choice=choice, metadata={'reason': reason, **metadata})

    def _action(self, offer, reason, params=None, **metadata):
        return self._handled(ArgentumActionChoice(offer['actionId'], params or {}), reason, **metadata)

    def _decision(self, pending, response_type, reason, **fields):
        return self._handled(ArgentumDecisionChoice(
            {'type': response_type, 'decisionId': pending['decisionId'], **fields}), reason)

    def _choose(self, o):
        s = o.get('state', {})
        if 'mulligan' in s:
            return self._mulligan(o)
        pending = o.get('pendingDecision')
        if pending is not None:
            return self._pending(o, pending)
        legal = o.get('legalActions', [])
        if not legal or any(type(a.get('actionId')) is not int or not a.get('semanticId') for a in legal):
            return SubsystemDecision(reason='heuristic-incomplete-native-menu')
        # Unknown affordability is never a reason to pretend an action is disabled.
        if any(not any(k in a for k in ('affordable', 'isAffordable')) or
               any(type(a[k]) is not bool for k in ('affordable', 'isAffordable') if k in a) or
               'affordable' in a and 'isAffordable' in a and a['affordable'] != a['isAffordable'] for a in legal):
            return SubsystemDecision(reason='heuristic-unknown-affordability')
        offers = [a for a in legal if affordable(a)]
        declarations = [a for a in offers if a.get('kind') in {'DeclareAttackers', 'DeclareBlockers'}]
        if declarations:
            return self._combat(o, declarations[0])
        if len(offers) == 1 and offers[0].get('kind') == 'PassPriority':
            return self._action(offers[0], 'only-affordable-pass')
        active = s.get('activePlayerId') == o['agentToAct']
        quiet = not any(z.get('zoneId', {}).get('zoneType') == 'Stack' and z.get('size', len(z.get('cardIds', [])))
                        for z in s.get('zones', []))
        if not quiet:
            return SubsystemDecision(reason='heuristic-stack-interaction')
        productive = [a for a in offers if a.get('kind') != 'PassPriority' and a.get('isManaAbility') is not True]
        if not productive:
            passed = next((a for a in offers if a.get('kind') == 'PassPriority'), None)
            if passed and self._plain_mana_window(o, offers):
                return self._action(passed, 'no-use-for-plain-mana')
            return SubsystemDecision(reason='heuristic-mana-side-effects-or-resources')
        if not active or s.get('currentStep') not in {'PRECOMBAT_MAIN', 'POSTCOMBAT_MAIN'}:
            return SubsystemDecision(reason='heuristic-strategic-priority-window')
        lands = [a for a in productive if a.get('kind') == 'PlayLand']
        if lands:
            return self._land(o, lands)
        return self._develop(o, productive)

    def _mulligan(self, o):
        m = o['state']['mulligan']
        hand, cards, count = m.get('hand'), m.get('cards', {}), m.get('mulliganCount')
        bottom = m.get('cardsToPutOnBottom', 0)
        if (not isinstance(hand, list) or type(count) is not int or type(bottom) is not int
            or any(i not in cards for i in hand)):
            return SubsystemDecision(reason='heuristic-incomplete-opening-hand')
        lands = [cards[i] for i in hand if 'Land' in cards[i].get('typeLine', '')]
        keep = len(hand) - bottom <= 5 or count >= 2
        if not keep:
            if any(not self.facts.get(c.get('name')).get('manaColors') for c in lands):
                return SubsystemDecision(reason='heuristic-unreadable-opening-mana')
            colors = [set(self.facts.get(c['name'])['manaColors']) for c in lands]
            spells = [mana_cost(cards[i].get('manaCost')) for i in hand if cards[i] not in lands]
            if any(c is None for c in spells):
                return SubsystemDecision(reason='heuristic-special-opening-cost')
            available = set().union(*colors) if colors else set()
            colorable = lambda cost: set(cost[1]) <= available
            on_two = sum(c[0] <= 2 and self._payable(c, colors) for c in spells)
            by_three = sum(c[0] <= 3 and colorable(c) for c in spells)
            keep = 2 <= len(lands) <= 5 and any(colorable(c) for c in spells)
            if len(lands) == 2:
                keep = keep and (on_two >= 2 if m.get('isOnThePlay') else on_two >= 1 and by_three >= 2)
        kind = 'KeepHand' if keep else 'TakeMulligan'
        offer = next((a for a in o['legalActions'] if a.get('kind') == kind), None)
        return self._action(offer, 'opening-hand-castability') if offer else SubsystemDecision(reason='heuristic-opening-offer-missing')

    @staticmethod
    def _payable(cost, colors):
        if cost[0] > len(colors):
            return False
        # Matching identical colored pips only needs Hall's condition over the
        # requested colors (at most six), avoiding permutations of identical mana.
        requested=[c for c,n in cost[1].items() if n>0]
        for mask in range(1,1<<len(requested)):
            subset={c for i,c in enumerate(requested) if mask & (1<<i)}
            if sum(cost[1][c] for c in subset)>sum(bool(subset & choices) for choices in colors):return False
        return True

    def _pending(self, o, p):
        kind = p.get('kind')
        spec = p.get('responseSpec', {})
        rt = spec.get('responseType')
        if rt == 'ManaSourcesSelectedResponse':
            return SubsystemDecision(reason='heuristic-payment-is-strategic')
        cancellable = p.get('canCancel') is True or spec.get('cancelAllowed') is True
        if kind == 'BottomCards':
            hand, count, cards = p.get('hand'), p.get('cardsToPutOnBottom'), p.get('cards', {})
            if (not isinstance(hand, list) or type(count) is not int or not 0 <= count <= len(hand)
                or any(i not in cards for i in hand)):
                return SubsystemDecision(reason='heuristic-incomplete-bottom-hand')
            lands = [i for i in hand if 'Land' in cards[i].get('typeLine', '')]
            target = 2 if len(hand) - count <= 5 else 3
            excess = lands[target:]
            spells = [i for i in hand if i not in lands]
            if any(mana_cost(cards[i].get('manaCost')) is None for i in spells):
                return SubsystemDecision(reason='heuristic-special-bottom-cost')
            # Shared keep valuation; color/curve corrections precede expensive spells.
            ordered = excess + sorted(spells, key=lambda i: self.card_value(cards[i], o), reverse=False)
            ordered += [i for i in lands if i not in excess]
            return self._decision(p, rt, 'bottom-excess-and-delayed-cards', selectedCards=ordered[:count])
        if not cancellable:
            if kind == 'ChooseOptionDecision' and len(p.get('options', [])) == 1:
                return self._decision(p, rt, 'forced-option', optionIndex=0)
            if kind == 'ChooseNumberDecision' and type(p.get('minValue')) is int and p['minValue'] == p.get('maxValue'):
                return self._decision(p, rt, 'forced-number', number=p['minValue'])
            if kind == 'ChooseColorDecision' and len(p.get('availableColors', [])) == 1:
                return self._decision(p, rt, 'forced-color', color=p['availableColors'][0])
            if kind == 'YesNoDecision' and p.get('yesAvailable') is False:
                return self._decision(p, rt, 'forced-no', choice=False)
            if kind == 'YesNoDecision':
                draw = self._free_draw(o, p)
                if draw is not None:
                    return draw
            if kind in {'OrderObjectsDecision', 'ReorderLibraryDecision'}:
                options = p.get('objects' if kind == 'OrderObjectsDecision' else 'cards')
                if isinstance(options, list) and len(options) <= 1:
                    return self._decision(p, rt, 'forced-order', orderedObjects=options)
            if kind in {'SelectCardsDecision', 'SearchLibraryDecision'}:
                options = p.get('options')
                constraints = ('onePerCardType', 'onePerColor', 'onePerCardName', 'onePerBasicLandType',
                               'onePerPower', 'maxTotalManaValue', 'minTotalManaValue', 'maxTotalPower',
                               'conditionalMinimums', 'selectionLimits', 'nonSelectableOptions')
                if (isinstance(options, list) and len(set(options)) == len(options)
                    and not any(p.get(k) is not None and p.get(k) is not False and p.get(k) != [] for k in constraints)
                    and (not p.get('ordered') or len(options) <= 1)):
                    if p.get('minSelections') == p.get('maxSelections') == len(options):
                        return self._decision(p, rt, 'forced-card-selection', selectedCards=options)
                    if not options and p.get('minSelections') == 0:
                        return self._decision(p, rt, 'empty-card-selection', selectedCards=[])
            if kind == 'ChooseTargetsDecision':
                reqs, targets = p.get('targetRequirements', []), p.get('legalTargets', {})
                if len(reqs) == 1:
                    req = reqs[0]
                    candidates = targets.get(str(req['index']), targets.get(req['index'], []))
                    if (len(candidates) == 1 and req.get('minTargets') == req.get('maxTargets') == 1
                        and req.get('totalCap') is None and not req.get('exclusiveGroups')
                        and not req.get('distinctCategories') and not req.get('selectionGroups')):
                        return self._decision(p, rt, 'forced-target', selectedTargets={str(req['index']): candidates})
        if kind == 'ChooseOptionDecision':
            return self._tribe(o, p)
        if kind == 'ChooseColorDecision':
            return self._mana_color(o, p)
        return SubsystemDecision(reason='heuristic-unsupported-or-strategic-decision')

    def _free_draw(self, o, p):
        context = p.get('context', {})
        identity = context.get('abilityIdentity') or {}
        fact = self.facts.get(context.get('sourceName'))
        count = fact.get('freeOptionalDraws', {}).get(identity.get('abilityId'))
        if (not count or context.get('phase') != 'RESOLUTION' or p.get('yesAvailable') is not True
            or p.get('peerOptions') or p.get('yesDetail') or p.get('noDetail')):
            return None
        players = o['state'].get('players', [])
        player = next((v for v in players if v.get('playerId') == o['agentToAct']), {})
        # Deck-out, draw replacement/punishment, and dynamic board effects stay
        # strategic. Never use the English prompt alone to certify a free draw.
        if type(player.get('librarySize')) is not int or player['librarySize'] <= count + 2:
            return None
        if any(v.get('activeEffects') for v in players):
            return None
        for i in ids_in(o['state'], 'Battlefield'):
            c = o['state'].get('cards', {}).get(i, {})
            f = self.facts.get(c.get('name'))
            if not f or f['drawSensitive'] or c.get('activeEffects'):
                return None
        return self._decision(p, p['responseSpec']['responseType'], 'free-optional-draw', choice=True)

    def _mana_color(self, o, p):
        context = p.get('context', {})
        source = context.get('sourceName') or o['state'].get('cards', {}).get(context.get('sourceId'), {}).get('name')
        f = self.facts.get(source)
        available = p.get('availableColors', [])
        if (not f or set(f['tags']) != {'RAMP'} or f['affectsOpponent']
            or not available or any(c not in {'WHITE', 'BLUE', 'BLACK', 'RED', 'GREEN'} for c in available)):
            return SubsystemDecision(reason='heuristic-color-purpose-unknown')
        scores = Counter()
        for name, count in o.get('knownDeck', {}).get('cards', {}).items():
            cost = mana_cost(self.facts.get(name).get('manaCost'))
            if cost and type(count) is int:
                for color, n in cost[1].items():
                    scores[COLORS[color].upper()] += n * count
        ranked = sorted(available, key=lambda c: (-scores[c], c))
        if not scores[ranked[0]] or len(ranked) > 1 and scores[ranked[0]] == scores[ranked[1]]:
            return SubsystemDecision(reason='heuristic-close-color-alternatives')
        return self._decision(p, p['responseSpec']['responseType'], 'own-mana-color-demand', color=ranked[0])

    def _tribe(self, o, p):
        options = p.get('options', [])
        source = p.get('context', {}).get('sourceName')
        fact = self.facts.get(source)
        if (not options or not all(isinstance(t, str) and t.lower() in self.facts.creature_types for t in options)
            or not fact or fact['affectsOpponent'] or 'ANTHEM' not in fact['tags']):
            return SubsystemDecision(reason='heuristic-choice-purpose-unknown')
        weights = Counter()
        # Own registered deck is public-to-this-pilot knowledge, never hidden entity order.
        deck = o.get('knownDeck', {}).get('cards', {})
        for name, count in deck.items():
            f = self.facts.get(name)
            if 'CREATURE' in f.get('types', []) and type(count) is int:
                for t in f['subtypes']:
                    weights[t.lower()] += count
        for i in ids_in(o['state'], 'Battlefield', o['agentToAct']):
            card = o['state'].get('cards', {}).get(i, {})
            if 'CREATURE' in card.get('cardTypes', []):
                for t in card.get('subtypes', []):
                    weights[t.lower()] += 2
        ranked = sorted(enumerate(options), key=lambda x: (-weights[x[1].lower()], x[0]))
        if weights[ranked[0][1].lower()] <= 0 or len(ranked) > 1 and weights[ranked[0][1].lower()] == weights[ranked[1][1].lower()]:
            return SubsystemDecision(reason='heuristic-ambiguous-tribal-choice')
        return self._decision(p, p['responseSpec']['responseType'], 'tribal-payoff-prior', optionIndex=ranked[0][0])

    def _plain_mana_window(self, o, offers):
        s, cards = o['state'], o['state'].get('cards', {})
        if any(p.get('activeEffects') for p in s.get('players', [])):
            return False
        # Exclude trigger, replacement and dynamic boards: tapping can itself matter.
        for i in ids_in(s, 'Battlefield'):
            c = cards.get(i, {})
            f = self.facts.get(c.get('name'))
            if not f or not f['simplePermanent'] or c.get('activeEffects') or c.get('attachments'):
                return False
        for a in offers:
            if a.get('kind') == 'PassPriority':
                continue
            c = cards.get(a.get('action', {}).get('sourceId'), {})
            if a.get('isManaAbility') is not True or not self.facts.get(c.get('name')).get('simpleMana'):
                return False
        return True

    def _land(self, o, lands):
        s, me = o['state'], o['agentToAct']
        cards = s.get('cards', {})
        demand = Counter()
        for i in ids_in(s, 'Hand', me) + ids_in(s, 'Command', me):
            cost = mana_cost(cards.get(i, {}).get('manaCost'))
            if cost:
                demand.update(cost[1])
        supply = Counter()
        for i in ids_in(s, 'Battlefield', me):
            for color in self.facts.get(cards.get(i, {}).get('name')).get('manaColors', []):
                supply[color] += 1
        ranked = []
        for a in lands:
            c = cards.get(a.get('action', {}).get('cardId'), {})
            f = self.facts.get(c.get('name'))
            if f and f['entersTapped']:
                # A known always-tapped alternative cannot supply this turn's
                # missing color. Keep it for the model unless an ordinary land
                # clearly develops a color the current hand needs now.
                ranked.append((0, a, c.get('name')))
                continue
            if not f or not f['manaColors'] or not f['simpleMana']:
                return SubsystemDecision(reason='heuristic-special-land-sequencing')
            # An untapped land and a missing color both buy immediate development.
            score = (4 if f['unconditionalUntapped'] else 0) + sum(demand[v] / (1 + supply[v]) for v in f['manaColors'])
            ranked.append((score, a, c.get('name')))
        ranked.sort(key=lambda item: (-item[0], item[1]['actionId']))
        if ranked[0][0] <= 4 and any(score == 0 for score, _, _ in ranked):
            return SubsystemDecision(reason='heuristic-land-utility-needs-assessment')
        # Equal copies are interchangeable. Different unknown utility is not.
        if len(ranked) > 1 and ranked[0][0] == ranked[1][0] and ranked[0][2] != ranked[1][2]:
            return SubsystemDecision(reason='heuristic-close-land-alternatives')
        return self._action(ranked[0][1], 'land-before-development', goal='develop-mana-then-reassess',
                            candidates=[{'actionId': a['actionId'], 'score': score} for score, a, _ in ranked])

    def _develop(self, o, productive):
        cards, s = o['state'].get('cards', {}), o['state']
        ranked = []
        for a in productive:
            c = cards.get(a.get('action', {}).get('cardId'), {})
            f = self.facts.get(c.get('name'))
            ac = a.get('action', {})
            if a.get('kind') == 'CastSpell' and f and a.get('requiresTargets') is True:
                targeted = self._targeted_cast(o, a, f)
                if targeted is None:
                    return SubsystemDecision(reason='heuristic-target-purpose-or-value-uncertain')
                ranked.append(targeted)
                continue
            if (a.get('kind') != 'CastSpell' or not f or a.get('requiresTargets') is not False
                or a.get('hasXCost') or a.get('additionalCostInfo') or a.get('modalEnumeration')
                or a.get('hasConvoke') or a.get('hasDelve') or ac.get('useAlternativeCost')
                or ac.get('chosenModes') or ac.get('castFaceDown') or ac.get('faceIndex') is not None
                or f['affectsOpponent'] or set(f['tags']) & (UNSAFE_TAGS-{'PROTECTION'} if f.get('friendlyStaticPermanent') else UNSAFE_TAGS)
                or not ({'CREATURE'} & set(f['types']) or f['simpleMana'] or
                        f['repeatable'] and 'DRAW' in f['tags'])):
                return SubsystemDecision(reason='heuristic-competing-special-action')
            score = self._development_value(c, o, f)
            ranked.append((score, a, {}))
        if not ranked:
            return SubsystemDecision(reason='heuristic-no-supported-development')
        ranked.sort(key=lambda item: (-item[0], item[1]['actionId']))
        if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < .25:
            return SubsystemDecision(reason='heuristic-close-development-alternatives',
                                     metadata={'candidateScores': [score for score, _, _ in ranked]})
        return self._action(ranked[0][1], 'productive-development', ranked[0][2], goal='cast-with-native-autopay-then-reassess',
                            candidates=[{'actionId': a['actionId'], 'score': score} for score, a, _ in ranked])

    def _targeted_cast(self, o, action, fact):
        """One ordinary permanent-target slot, with native structural polarity."""
        if (any(p.get('teamIndex') is not None for p in o['state'].get('players', []))
            or action.get('targetRequirements') is not None or action.get('targetCount') != 1
            or action.get('minTargets') != 1 or action.get('hasXCost')
            or action.get('additionalCostInfo') or action.get('modalEnumeration')
            or action.get('hasDelve') or action.get('hasConvoke')
            or action.get('requiresDamageDistribution') or action.get('requiresManaColorChoice')
            or action.get('action', {}).get('useAlternativeCost') or action.get('action', {}).get('chosenModes')
            or action.get('action', {}).get('castFaceDown') or action.get('action', {}).get('faceIndex') is not None
            or set(fact['tags']) & {'SWEEPER', 'COMBAT_TRICK', 'COUNTERSPELL'}
            or len(fact['spellPolarity']) != 1):
            return None
        polarity = fact['spellPolarity'][0]
        if polarity not in {'HARMFUL', 'BENEFICIAL'}:
            return None
        cards, me = o['state'].get('cards', {}), o['agentToAct']
        battlefield = set(ids_in(o['state'], 'Battlefield'))
        targets = action.get('validTargets')
        if not isinstance(targets, list):
            return None
        ranked = []
        for target in targets:
            if target not in battlefield:
                return None
            card = cards.get(target, {})
            owner = card.get('controllerId')
            if owner is None or (polarity == 'BENEFICIAL') != (owner == me):
                continue
            f = self.facts.get(card.get('name'))
            if not f:
                return None
            score = f['staticValue'] + max(0, card.get('power') or 0) * .5 + max(0, card.get('toughness') or 0) * .2
            if f['repeatable'] and 'DRAW' in f['tags']:
                score += 3
            if polarity == 'BENEFICIAL' and (card.get('isTapped') or card.get('hasSummoningSickness')):
                score -= 2
            ranked.append((score, target))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        if not ranked or len(ranked) > 1 and ranked[0][0] - ranked[1][0] < .5:
            return None
        # Avoid spending removal on negligible material just because it is legal.
        if polarity == 'HARMFUL' and ranked[0][0] < 3:
            return None
        return ranked[0][0], action, {'targets': [ranked[0][1]]}

    def _development_value(self, card, observation, fact):
        return self.card_value(card, observation) + (6 if fact['simpleMana'] else 0) + (5 if card.get('isCommander') else 0)

    def card_value(self, card, o):
        """Shared keep/develop value: useful soon, castable colors, repeatable payoff."""
        f = self.facts.get(card.get('name'))
        cost = mana_cost(card.get('manaCost', f.get('manaCost', '')))
        if cost is None:
            return 0.0
        s, me = o.get('state', {}), o['agentToAct']
        live = s.get('cards', {})
        sources = [self.facts.get(live.get(i, {}).get('name')) for i in ids_in(s, 'Battlefield', me)]
        count = sum(bool(f.get('manaColors')) for f in sources)
        colors = set(v for f in sources for v in f.get('manaColors', []))
        for i in ids_in(s, 'Hand', me):
            other = self.facts.get(live.get(i, {}).get('name'))
            if 'LAND' in other.get('types', []):
                colors.update(other.get('manaColors', []))
        m = s.get('mulligan', {})
        if m:
            count = sum('Land' in c.get('typeLine', '') for c in m.get('cards', {}).values())
            colors = set(v for c in m.get('cards', {}).values()
                         for v in self.facts.get(c.get('name')).get('manaColors', []))
        score = 4 + f.get('staticValue', 0) - max(0, cost[0] - max(2, count)) * 2
        if colors:
            score -= 3 * len(set(cost[1]) - colors)
        if 'CREATURE' in f.get('types', []) or 'Creature' in card.get('typeLine', ''):
            score += 2
        if f.get('repeatable') and 'DRAW' in f.get('tags', []):
            score += 4
        return score

    def _combat(self, o, offer):
        # Native pairwise legality is necessary but not sufficient for group combat.
        # Only an uncontested ordinary attack is covered here; all blocks and tricks defer.
        if offer.get('kind') != 'DeclareAttackers':
            return SubsystemDecision(reason='heuristic-blocking-needs-search')
        attackers, targets = offer.get('validAttackers'), offer.get('validAttackTargets')
        if not isinstance(attackers, list) or not isinstance(targets, list) or not attackers:
            return SubsystemDecision(reason='heuristic-combat-offer-incomplete')
        s, me = o['state'], o['agentToAct']
        players = {p['playerId']: p for p in s.get('players', [])}
        enemies = [p for p in players if p != me and not players[p].get('hasLost')]
        # Multiplayer retaliation, teams, taxes, triggers, abilities and mandatory
        # assignments stay strategic until explicitly represented by a supported policy.
        if (offer.get('action', {}).get('playerId') != me or len(enemies) != 1 or players[me].get('teamIndex') is not None
            or any(p.get('activeEffects') for p in players.values())
            or enemies[0] not in targets or offer.get('mandatoryAttackers')
            or offer.get('loneAttackers') or offer.get('attackTargetsByAttacker')):
            return SubsystemDecision(reason='heuristic-complex-or-multiplayer-combat')
        cards = s.get('cards', {})
        for i in ids_in(s, 'Battlefield'):
            c = cards.get(i, {})
            if (not self.facts.get(c.get('name')).get('simplePermanent') or c.get('attachments')
                or c.get('activeEffects') or keyword_set(c) or c.get('counters')
                or c.get('isFaceDown') or c.get('isPhasedOut')):
                return SubsystemDecision(reason='heuristic-combat-mechanic-unsupported')
        enemy = enemies[0]
        blockers = [cards[i] for i in ids_in(s, 'Battlefield', enemy)
                    if 'CREATURE' in cards[i].get('cardTypes', []) and not cards[i].get('isTapped')]
        if blockers:
            return SubsystemDecision(reason='heuristic-contested-combat')
        selected = {i: enemy for i in attackers if cards.get(i, {}).get('power', 0) > 0}
        damage = sum(cards[i]['power'] for i in selected)
        # Keep defenders unless this attack itself wins; no automatic all-out race.
        if not selected or damage < players[enemy].get('life', float('inf')):
            return SubsystemDecision(reason='heuristic-nonlethal-race-needs-search')
        return self._action(offer, 'uncontested-simple-lethal', {'attackers': selected})


class HeuristicOnlyPilot:
    """Evaluation baseline: unsupported decisions fail explicitly, never auto-pass."""
    name = 'argentum-masked-heuristics-only'
    version = '1'

    def __init__(self, facts):
        self.policy = MaskedHeuristicSubsystem(facts)

    def choose(self, observation):
        result = self.policy.try_choose(observation)
        if result.choice is None:
            raise PilotContractError('heuristic-only pilot deferred: ' + result.reason)
        return result.choice
