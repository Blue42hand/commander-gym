"""Opt-in primer policy over current masked offers, never native engine state.

Strategic simplifications are deliberate: develop a wide Elf board, avoid unplanned
mana and reserve recovery. Unrepresented decisions stop visibly; they never reopen
an unrestricted model action space. All selected choices use the native validator.
"""
from dataclasses import replace
import hashlib
import json
from itertools import combinations
from pathlib import Path

from .deck_package import ArtifactRef
from .heuristic_facts import canonical
from .heuristic_policy import MaskedHeuristicSubsystem, ids_in, affordable, mana_cost
from .pilot import ArgentumActionChoice, PilotContractError
from .pilot_composition import SubsystemDecision
from .bounded_deck_advisor import BoundedDeckAdvisor

POLICY_PATH = Path(__file__).with_name('deck_policies') / 'lathril_v1.json'


def policy_document():
    return json.loads(POLICY_PATH.read_text())


def lathril_component_ref(facts):
    sources = ['lathril_policy.py', 'bounded_deck_advisor.py', 'heuristic_policy.py', 'heuristic_facts.py']
    payload = {'facts': facts.digest, 'policy': policy_document(),
               'sources': {p: hashlib.sha256(Path(__file__).with_name(p).read_bytes()).hexdigest() for p in sources}}
    return ArtifactRef(kind='skill-set', artifact_id='lathril-primer-policy', version='1',
                       digest='sha256:' + hashlib.sha256(canonical(payload)).hexdigest())


def validate_lathril_binding(binding):
    doc = policy_document()
    if (binding.deck.fingerprint != doc['deck_fingerprint'] or binding.deck_knowledge is None
        or binding.deck_knowledge.fingerprint != doc['primer_fingerprint']):
        raise PilotContractError('Lathril policy requires its exact reviewed deck and primer')


class LathrilDeckSubsystem(MaskedHeuristicSubsystem):
    name = 'lathril-primer-policy'
    version = '1'

    def __init__(self, facts, config=None, transport=None):
        super().__init__(facts)
        self.document = policy_document()
        self.advisor = BoundedDeckAdvisor(config, transport) if config is not None else None

    def try_choose(self, observation):
        result = super().try_choose(observation)
        if result.choice is None:
            # No static-order escape into an unrestricted generalist.
            raise PilotContractError('lathril-unsupported: ' + str(result.reason))
        metadata = {**result.metadata, 'deckPolicy': self.document['policy_id'],
                    'policyDigest': lathril_component_ref(self.facts).digest}
        choice_metadata = {**result.choice.metadata, **metadata.pop('advisorMetadata', {})}
        return replace(result, choice=replace(result.choice, metadata={**choice_metadata, 'deckPolicy': metadata}), metadata=metadata)

    def _card(self, o, entity):
        return o.get('state', {}).get('cards', {}).get(entity, {})

    def _own(self, o):
        return [self._card(o, i) for i in ids_in(o['state'], 'Battlefield', o['agentToAct'])]

    def _elves(self, o, untapped=False):
        return [i for i in ids_in(o['state'], 'Battlefield', o['agentToAct'])
                if 'CREATURE' in self._card(o, i).get('cardTypes', [])
                and 'Elf' in self._card(o, i).get('subtypes', [])
                and (not untapped or not self._card(o, i).get('isTapped'))]

    @staticmethod
    def _pass(o):
        return next((a for a in o['legalActions'] if a.get('kind') == 'PassPriority' and affordable(a)), None)

    def _choose(self, o):
        if o.get('nativePaymentError') is not None:
            raise PilotContractError('lathril-unsupported-payment-correction')
        if 'mulligan' in o.get('state', {}):
            return self._opening(o)
        if o.get('pendingDecision') is not None:
            return self._pending(o, o['pendingDecision'])
        legal = o['legalActions']
        if any(not isinstance(a.get('semanticId'), str) or type(a.get('actionId')) is not int
               or not any(k in a for k in ('affordable', 'isAffordable'))
               or any(type(a[k]) is not bool for k in ('affordable', 'isAffordable') if k in a)
               or ('affordable' in a and 'isAffordable' in a and a['affordable'] != a['isAffordable']) for a in legal):
            raise PilotContractError('lathril-incomplete-native-menu')
        offers = [a for a in legal if affordable(a)]
        passed = self._pass(o)
        if len(offers) == 1 and passed:
            return self._action(passed, 'lathril-only-affordable-pass')
        for a in offers:
            if a['kind'] in {'DeclareAttackers', 'DeclareBlockers'}:
                return self._combat(o, a)
        s = o['state']
        quiet = not any(z.get('zoneId', {}).get('zoneType') == 'Stack' and z.get('size', len(z.get('cardIds', []))) for z in s.get('zones', []))
        # Do not spend resources in arbitrary priority windows. Interaction is a
        # separate shortlist; no land/development cast is allowed onto a stack.
        removal = self._removal_candidates(o, offers)
        if not quiet or s.get('activePlayerId') != o['agentToAct'] or s.get('currentStep') not in {'PRECOMBAT_MAIN', 'POSTCOMBAT_MAIN'}:
            return self._interaction(o, removal, passed)
        lands = [a for a in offers if a['kind'] == 'PlayLand']
        if lands:
            ranked = sorted(((self._land_score(o, a), a) for a in lands), key=lambda x: (-x[0], x[1]['semanticId']))
            return self._action(ranked[0][1], 'lathril-develop-colored-mana', objective='mana-before-engines',
                                candidates=[{'semanticId': a['semanticId'], 'score': score} for score, a in ranked])
        # Native owns tap costs. Never invent additional-cost selections.
        for a in offers:
            raw = a.get('action', {}); cost = a.get('additionalCostInfo') or {}
            if (a['kind'] == 'ActivateAbility' and self._card(o, raw.get('sourceId')).get('name') == self.document['commander']
                and raw.get('abilityId') == 'Lathril, Blade of the Elves:2' and a.get('requiresTargets') is False
                and len(self._elves(o, True)) >= 11 and cost.get('costType') == 'TapPermanents'
                and cost.get('tapCount') == 10 and not cost.get('alsoRequired')
                and a.get('parameterSpec', {}).get('allowedFields', {}).get('tappedPermanents') == 'ENTITY_ID_ARRAY'):
                candidates = set(cost.get('validTapTargets', [])) & (set(self._elves(o, True)) - {raw.get('sourceId')})
                if len(candidates) >= 10:
                    return self._action(a, 'lathril-ten-elf-drain', {'tappedPermanents': sorted(candidates)[:10]}, objective='drain-opponents')
        if removal and removal[0][0] >= 10:
            return self._interaction(o, removal, passed)
        ranked = []
        for a in offers:
            if a['kind'] != 'CastSpell' or not self._plain_cast(a):
                continue
            card = self._card(o, a.get('action', {}).get('cardId'))
            role = self.document['roles'].get(card.get('name'))
            if role == 'finisher' and a.get('requiresTargets') is False and card.get('name') in {'Overwhelming Stampede', 'Craterhoof Behemoth'} and self._overrun_is_lethal(o, card.get('name')):
                return self._action(a, 'lathril-demonstrated-overrun', objective='lethal-attack')
            if card.get('name') == 'Demonic Tutor' and a.get('requiresTargets') is False:
                ranked.append((75, a, card.get('name')))
                continue
            if role not in self.document['weights'] or a.get('requiresTargets') is not False:
                continue
            # Instants wait for their policy window, not merely any affordable offer.
            if role == 'token_engine' and card.get('name') == 'Elven Ambush':
                continue
            if (role == 'draw_engine' and len(self._elves(o)) >= 4
                and any(self.document['roles'].get(c.get('name')) == 'draw_engine' for c in self._own(o))):
                continue  # Keep a redundant engine in reserve behind a wide board.
            score = self._development_score(o, card, role)
            ranked.append((score, a, card.get('name')))
        if ranked:
            ranked.sort(key=lambda x: (-x[0], x[1]['semanticId']))
            return self._action(ranked[0][1], 'lathril-weighted-development', objective='mana-engines-commander-wide-board',
                                candidates=[{'card': name, 'score': score} for score, _, name in ranked])
        return self._interaction(o, removal, passed)

    @staticmethod
    def _plain_cast(a):
        raw = a.get('action', {})
        return not (a.get('hasXCost') or a.get('additionalCostInfo') or a.get('modalEnumeration')
                    or a.get('hasConvoke') or a.get('hasDelve') or a.get('requiresDamageDistribution')
                    or raw.get('useAlternativeCost') or raw.get('chosenModes') or raw.get('castFaceDown')
                    or raw.get('faceIndex') is not None)

    def _development_score(self, o, card, role):
        score = self.document['weights'][role]
        own = self._own(o); elves = len(self._elves(o))
        mana = sum('LAND' in c.get('cardTypes', []) or self.document['roles'].get(c.get('name')) in {'mana_elf', 'mana_engine'} for c in own)
        if role in {'mana_elf', 'mana_engine'} and mana >= 5:
            score -= 35
        if card.get('name') in {'Cryptolith Rite', 'Growing Rites of Itlimoc'} and elves < 2:
            score -= 45  # A creature-dependent engine without creatures is not acceleration.
        if role == 'lord':
            score += min(20, 4*elves)
        if role == 'commander' and elves >= 2:
            score += 12
        if role == 'draw_engine' and any(self.document['roles'].get(c.get('name')) == role for c in own):
            score -= 12
        if role == 'utility':
            score -= 10
        cost = mana_cost(card.get('manaCost', self.facts.get(card.get('name')).get('manaCost', '')))
        return score - (cost[0]*.25 if cost else 0)

    def _land_colors(self, card):
        return set(self.document['lands'].get(card.get('name'), self.facts.get(card.get('name')).get('manaColors', [])))

    def _enters_tapped(self, card, board):
        name = card.get('name')
        if name in {'Path of Ancestry', 'Temple of Malady', 'Bojuka Bog'}:
            return True
        if name == 'Woodland Cemetery':
            return not any(c.get('name') in {'Forest', 'Swamp', 'Overgrown Tomb'}
                           or set(c.get('subtypes', [])) & {'Forest', 'Swamp'} for c in board)
        # Tomb can enter untapped by paying life; native owns that decision.
        return False

    def _opening_value(self, card):
        role = self.document['roles'].get(card.get('name'))
        base = self.document['opening_weights'].get(role, 10)
        cost = mana_cost(card.get('manaCost', self.facts.get(card.get('name')).get('manaCost', '')))
        value = base - (cost[0] * 3 if cost else 18)
        if card.get('name') in {"Patriarch's Bidding", 'Cryptolith Rite', 'Growing Rites of Itlimoc', 'Elvish Guidance'}:
            value -= 20  # Requires a developed board or graveyard.
        return value

    def _land_score(self, o, action):
        card = self._card(o, action['action'].get('cardId'))
        board = self._own(o); colors = self._land_colors(card)
        supply = set().union(*(self._land_colors(c) for c in board)) if board else set()
        score = (8 if 'G' in colors - supply else 0) + (6 if 'B' in colors - supply else 0)
        sources = [self._land_colors(c) for c in board if not c.get('isTapped') and self._land_colors(c)]
        if not self._enters_tapped(card, board):
            sources.append(colors)
        hand = [self._card(o, i) for i in ids_in(o['state'], 'Hand', o['agentToAct'])]
        useful = [c for c in hand if self.document['roles'].get(c.get('name')) in self.document['weights']]
        playable = [self._opening_value(c) for c in useful
                    if (cost := mana_cost(c.get('manaCost', self.facts.get(c.get('name')).get('manaCost', ''))))
                    and cost[0] > 0 and self._payable(cost, sources)]
        # A useful cast this turn takes precedence over abstract color coverage.
        score += max(playable, default=0) * 2
        for c in useful:
            cost = mana_cost(c.get('manaCost', self.facts.get(c.get('name')).get('manaCost', '')))
            if cost and cost[0] <= 4:
                score += sum(min(cost[1][color], 2) for color in colors & {'G', 'B'}) * 2
        return score

    def _opening(self, o):
        m = o['state']['mulligan']; hand = m.get('hand', []); cards = m.get('cards', {})
        if any(i not in cards for i in hand) or type(m.get('mulliganCount')) is not int:
            raise PilotContractError('lathril-incomplete-opening')
        lands = [cards[i] for i in hand if 'Land' in cards[i].get('typeLine', '')]
        land_colors = [set(self.document['lands'].get(land.get('name'), [])) for land in lands]
        colors = set().union(*land_colors) if land_colors else set()
        roles = [self.document['roles'].get(cards[i].get('name')) for i in hand]
        early = any(role == 'mana_elf' for role in roles) or any(cards[i].get('name') == 'Sol Ring' for i in hand)
        playable = any((cost := mana_cost(cards[i].get('manaCost'))) is not None and cost[0] <= 2
                       and self._payable(cost, land_colors) and self.document['roles'].get(cards[i].get('name')) in {'mana_elf', 'elf', 'draw_engine', 'mana_engine'}
                       and cards[i].get('name') not in {'Cryptolith Rite', 'Growing Rites of Itlimoc', 'Elvish Guidance'}
                       for i in hand if cards[i] not in lands)
        playable = playable or any(c.get('name') == 'Demonic Tutor' and self._payable(mana_cost('{1}{B}'), land_colors) for c in cards.values())
        keep = m['mulliganCount'] >= 2 or (2 <= len(lands) <= 4 and 'G' in colors and (early or playable))
        offer = next((a for a in o['legalActions'] if a.get('kind') == ('KeepHand' if keep else 'TakeMulligan')), None)
        if offer is None:
            raise PilotContractError('lathril-opening-offer-missing')
        return self._action(offer, 'lathril-opening-mana-and-early-elf', objective='cast-early-elves', landCount=len(lands))

    def _pending(self, o, p):
        context = p.get('context', {}); source = context.get('sourceName'); rt = p.get('responseSpec', {}).get('responseType')
        if (p.get('kind') == 'SelectCardsDecision' and source == self.document['commander']
            and p.get('minSelections') == p.get('maxSelections') == 10):
            options = p.get('options', [])
            ready = set(self._elves(o, True)) - {context.get('sourceId')}
            if len(set(options) & ready) >= 10 and not any(p.get(k) for k in ['selectionLimits', 'nonSelectableOptions', 'conditionalMinimums']):
                return self._decision(p, rt, 'lathril-pay-ten-other-elves', selectedCards=sorted(set(options) & ready)[:10])
        if p.get('kind') == 'ChooseOptionDecision' and source in self.document['tribal_sources'] and 'Elf' in p.get('options', []):
            return self._decision(p, rt, 'lathril-tribe-is-elf', optionIndex=p['options'].index('Elf'))
        if p.get('kind') == 'ChooseColorDecision':
            choices = p.get('availableColors', [])
            for color in ['GREEN', 'BLACK', 'COLORLESS']:
                if color in choices:
                    return self._decision(p, rt, 'lathril-mana-color', color=color)
        if (p.get('kind') == 'YesNoDecision' and rt == 'YesNoResponse' and source == 'Overgrown Tomb'
            and p.get('prompt') == 'Pay 2 life to have Overgrown Tomb enter untapped?'):
            board = [self._card(o, i) for i in ids_in(o['state'], 'Battlefield', o['agentToAct']) if i != context.get('sourceId')]
            sources = [self._land_colors(c) for c in board if not c.get('isTapped') and self._land_colors(c)]
            life = next((x.get('life', 0) for x in o['state'].get('players', []) if x.get('playerId') == o['agentToAct']), 0)
            unlocks = False
            for i in ids_in(o['state'], 'Hand', o['agentToAct']):
                card = self._card(o, i)
                if self.document['roles'].get(card.get('name')) not in self.document['weights'] and card.get('name') != 'Demonic Tutor':
                    continue
                cost = mana_cost(card.get('manaCost', self.facts.get(card.get('name')).get('manaCost', '')))
                if cost and cost[0] > 0 and self._payable(cost, sources + [{'G', 'B'}]) and not self._payable(cost, sources):
                    unlocks = True
            return self._decision(p, rt, 'lathril-pay-life-for-development', choice=p.get('yesAvailable') is True and life > 4 and unlocks)
        if p.get('kind') == 'YesNoDecision' and source == 'Leaf-Crowned Visionary' and (context.get('abilityIdentity') or {}).get('abilityId') == 'Leaf-Crowned Visionary:1':
            me = next((x for x in o['state'].get('players', []) if x.get('playerId') == o['agentToAct']), {})
            return self._decision(p, rt, 'lathril-pay-for-cards', choice=p.get('yesAvailable') is True and me.get('librarySize', 0) > 3)
        if rt == 'ManaSourcesSelectedResponse' and source == 'Leaf-Crowned Visionary' and p.get('requiredCost') == '{G}':
            available = {a['entityId'] for a in p.get('availableSources', []) if not a.get('requiresSacrifice') and not a.get('requiresTappingAnotherPermanent')}
            suggestion = p.get('autoPaySuggestion', [])
            if suggestion and len(suggestion) == len(set(suggestion)) and set(suggestion) <= available:
                return self._decision(p, rt, 'lathril-pay-native-suggestion', autoPay=False, declined=False, selectedSources=suggestion, waterbendPermanents=[])
            if p.get('canAutoPayNow') is True:
                return self._decision(p, rt, 'lathril-native-autopay', autoPay=True, declined=False, selectedSources=[], waterbendPermanents=[])
            if p.get('canDecline') is True:
                return self._decision(p, rt, 'lathril-decline-unfunded-draw', autoPay=False, declined=True, selectedSources=[], waterbendPermanents=[])
        if p.get('kind') in {'SearchLibraryDecision', 'SelectCardsDecision'} and p.get('minSelections') == p.get('maxSelections') == 1 and source in {'Demonic Tutor', 'Elvish Harbinger', 'Wood Elves', 'Sylvan Messenger', 'Collected Company'}:
            if any(p.get(k) for k in ['nonSelectableOptions', 'selectionLimits', 'conditionalMinimums']):
                raise PilotContractError('lathril-unsupported-search-constraints')
            own_power = sum(max(0, c.get('power') or 0) for c in self._own(o))
            enemy_power = max((sum(max(0, self._card(o, i).get('power') or 0) for i in ids_in(o['state'], 'Battlefield', player['playerId'])) for player in o['state'].get('players', []) if player['playerId'] != o['agentToAct']), default=0)
            def search_score(entity):
                name = self._card(o, entity).get('name'); role = self.document['roles'].get(name)
                if role == 'finisher':
                    return 200 if self._overrun_is_lethal(o, name) else -1
                if role == 'removal':
                    return 150 if enemy_power > max(10, own_power*1.5) else 20
                if 'Land' in self._card(o, entity).get('typeLine', '') or 'LAND' in self._card(o, entity).get('cardTypes', []):
                    return 100 if source == 'Wood Elves' else 10
                return self.document['weights'].get(role, -1)
            candidates = [(search_score(i), i) for i in p.get('options', [])]
            # Library order never enters scoring; options are native permitted choices.
            if candidates:
                target = max(candidates, key=lambda x: (x[0], x[1]))[1]
                return self._decision(p, rt, 'lathril-search-for-engine', selectedCards=[target])
        count = p.get('minSelections')
        options = p.get('options', [])
        hand = ids_in(o.get('state', {}), 'Hand', o['agentToAct'])
        if (p.get('kind') == 'SelectCardsDecision' and rt == 'CardsSelectedResponse'
            and type(count) is int and count > 0 and p.get('maxSelections') == count
            and p.get('prompt') == f'Discard down to 7 cards (choose {count} to discard)'
            and context.get('phase') == 'STATE_BASED' and o['state'].get('currentStep') == 'CLEANUP'
            and len(hand) - 7 == count and len(options) == len(hand) and set(options) == set(hand)
            and not p.get('librarySearch') and not any(p.get(k) for k in [
                'nonSelectableOptions', 'selectionLimits', 'conditionalMinimums', 'onePerCardType',
                'onePerColor', 'onePerCardName', 'onePerBasicLandType', 'onePerPower',
                'maxTotalManaValue', 'minTotalManaValue', 'maxTotalPower'])):
            if any(not self._card(o, i).get('name') for i in options):
                raise PilotContractError('lathril-incomplete-discard-hand')
            lands = sum(bool(self._land_colors(c)) for c in self._own(o))
            hand_lands = sum(bool(self._land_colors(self._card(o, i))) for i in hand)
            def discard_value(entity):
                card = self._card(o, entity)
                if 'Land' in card.get('typeLine', '') or 'LAND' in card.get('cardTypes', []):
                    return 150 if lands < 3 else (60 if lands + hand_lands <= 5 else 0)
                role = self.document['roles'].get(card.get('name'))
                if role == 'finisher' and self._overrun_is_lethal(o, card.get('name')):
                    return 200
                value = self._opening_value(card)
                if role in {'mana_elf', 'mana_engine'} and lands >= 5:
                    value -= 35
                return value
            chosen = sorted(options, key=lambda i: (discard_value(i), i))[:count]
            return self._decision(p, rt, 'lathril-discard-for-hand-limit', selectedCards=chosen)
        if p.get('kind') == 'BottomCards':
            cards = p.get('cards', {}); hand = p.get('hand', []); count = p.get('cardsToPutOnBottom')
            if type(count) is not int or not 0 <= count <= len(hand) or any(i not in cards for i in hand):
                raise PilotContractError('lathril-incomplete-bottom')
            def retained_value(retained):
                lands = [cards[i] for i in retained if 'Land' in cards[i].get('typeLine', '')]
                colors = set().union(*(self._land_colors(c) for c in lands)) if lands else set()
                pip_support = sum(min(sum(color in self._land_colors(c) for c in lands),
                                      max((cards[i].get('manaCost', '').count('{' + color + '}') for i in retained if cards[i] not in lands), default=0))
                                  for color in ['G', 'B'])
                return (pip_support * 8 + sum(self._opening_value(cards[i]) for i in retained if cards[i] not in lands)
                        + min(len(lands), 3) * 150 + max(0, len(lands)-3) * 5
                        + (35 if 'G' in colors else 0) + (25 if 'B' in colors else 0))
            # Compare complete retained hands; entity IDs only settle equivalent plans.
            kept = max(combinations(sorted(hand), len(hand)-count), key=retained_value)
            return self._decision(p, rt, 'lathril-bottom-delayed-cards', selectedCards=sorted(set(hand)-set(kept)))
        if (p.get('kind') == 'SelectCardsDecision' and rt == 'CardsSelectedResponse'
            and p.get('prompt') == 'Scry 1: choose cards to put on the bottom of your library'
            and p.get('selectedLabel') == 'Put on bottom' and p.get('remainderLabel') == 'Put on top'
            and p.get('minSelections') == 0 and p.get('maxSelections') == 1
            and len(p.get('options', [])) == 1 and not p.get('librarySearch')
            and not any(p.get(k) for k in ['nonSelectableOptions', 'selectionLimits', 'conditionalMinimums', 'onePerCardType', 'onePerColor', 'onePerCardName', 'onePerBasicLandType', 'onePerPower', 'maxTotalManaValue', 'minTotalManaValue', 'maxTotalPower'])):
            entity = p['options'][0]
            card = p.get('cardInfo', {}).get(entity)
            if not isinstance(card, dict) or not card.get('name'):
                raise PilotContractError('lathril-incomplete-scry-card')
            board_lands = sum(bool(self._land_colors(c)) for c in self._own(o))
            hand = [self._card(o, i) for i in ids_in(o['state'], 'Hand', o['agentToAct'])]
            hand_lands = sum(bool(self._land_colors(c)) for c in hand)
            is_land = 'Land' in card.get('typeLine', '') or 'LAND' in card.get('cardTypes', [])
            bottom = (board_lands + hand_lands >= 4) if is_land else (self._opening_value(card) < 10 and board_lands < 5)
            return self._decision(p, rt, 'lathril-scry-for-development', selectedCards=[entity] if bottom else [])
        # Preserve the shared forced/free-draw contracts, but never its generic
        # tribal inference or unrestricted generalist escalation.
        return super()._pending(o, p)

    def _removal_candidates(self, o, offers):
        candidates = []
        for a in offers:
            card = self._card(o, a.get('action', {}).get('cardId'))
            if (a['kind'] != 'CastSpell' or self.document['roles'].get(card.get('name')) != 'removal'
                or not self._plain_cast(a) or a.get('targetCount') != 1 or a.get('minTargets') != 1 or a.get('targetRequirements') is not None):
                continue
            for target in a.get('validTargets') or []:
                c = self._card(o, target)
                if c.get('controllerId') in (None, o['agentToAct']) or target not in ids_in(o['state'], 'Battlefield'):
                    continue
                score = self.facts.get(c.get('name')).get('staticValue', 0) + max(0, c.get('power') or 0)
                if score >= 5:
                    candidates.append((score, a, target))
        return sorted(candidates, key=lambda x: (-x[0], x[1]['semanticId'], x[2]))

    def _interaction(self, o, removal, passed):
        if removal:
            best = removal[0]
            near = [r for r in removal if best[0] - r[0] < 1][:3]
            options = [(self._card(o, a['action']['cardId']).get('name') + ' on ' + self._card(o, t).get('name', 'unknown'),
                        ArgentumActionChoice(a['actionId'], {'targets': [t]})) for _, a, t in near]
            if len(options) > 1:
                if self.advisor is None:
                    raise PilotContractError('lathril-bounded-advisor-required')
                context = {'objective': 'remove-opponent-engine', 'threats': [
                    {k: self._card(o, t).get(k) for k in ['name', 'power', 'toughness', 'oracleText', 'isTapped', 'isCommander']} for _, _, t in near]}
                choice = self.advisor.choose('Which offered threat should we remove?', options, context)
                return SubsystemDecision(choice=choice, metadata={'reason': 'lathril-bounded-removal', 'advisorMetadata': dict(choice.metadata)})
            return self._action(best[1], 'lathril-remove-opponent-threat', {'targets': [best[2]]})
        if passed:
            return self._action(passed, 'lathril-preserve-resources', objective='no-unplanned-mana-or-utility')
        raise PilotContractError('lathril-unsupported-no-policy-choice')

    def _overrun_is_lethal(self, o, name):
        if name not in {'Overwhelming Stampede', 'Craterhoof Behemoth'}:
            return False
        me = o['agentToAct']; own = [c for c in self._own(o) if 'CREATURE' in c.get('cardTypes', [])]
        enemies = [p for p in o['state'].get('players', []) if p['playerId'] != me and not p.get('hasLost')]
        if (not enemies or any(p.get('teamIndex') is not None or p.get('activeEffects') for p in o['state'].get('players', []))
            or any(any('CREATURE' in self._card(o, i).get('cardTypes', []) and not self._card(o, i).get('isTapped')
                       for i in ids_in(o['state'], 'Battlefield', p['playerId'])) for p in enemies)):
            return False
        boost = max((max(0,c.get('power') or 0) for c in own),default=0) if name == 'Overwhelming Stampede' else len(own)+1
        damage = sorted([max(0,c.get('power') or 0)+boost for c in own if not c.get('isTapped') and not c.get('hasSummoningSickness')],reverse=True)
        # Sufficient condition only: assign actual creatures without splitting damage
        # between players. Unknown life or insufficient force keeps the card reserved.
        lives = sorted([p.get('life') for p in enemies if type(p.get('life')) is int],reverse=True)
        if len(lives) != len(enemies):
            return False
        for life in lives:
            while life > 0 and damage:
                life -= damage.pop(0)
            if life > 0:
                return False
        return True

    def _block(self, o, offer):
        pairs = offer.get('validBlockTargets')
        if not offer.get('validBlockers') and pairs in (None, {}):
            return self._action(offer, 'lathril-no-blockers', {'blockers': {}})
        if not isinstance(pairs, dict) or offer.get('mandatoryBlockers'):
            raise PilotContractError('lathril-unsupported-block-requirements')
        cap = offer.get('maxTotalBlockers')
        if cap is None:
            cap = len(pairs)
        if type(cap) is not int or cap < 0:
            raise PilotContractError('lathril-incomplete-block-cap')
        attackers = sorted(set(i for values in pairs.values() for i in values),
                           key=lambda i: (-(self._card(o,i).get('power') or 0),i))
        selected = {}; life = next((p.get('life',40) for p in o['state'].get('players',[]) if p['playerId']==o['agentToAct']),40)
        incoming = sum(max(0,self._card(o,i).get('power') or 0) for i in attackers)
        for target in attackers:
            enemy = self._card(o,target)
            available = [i for i,targets in pairs.items() if target in targets and i not in selected]
            available.sort(key=lambda i: (self.document['weights'].get(self.document['roles'].get(self._card(o,i).get('name')),0),i))
            required = 2 if 'MENACE' in enemy.get('keywords',[]) else 1
            group = available[:required]
            if len(group)<required or len(selected)+required>cap:
                continue
            powers = sum(max(0,self._card(o,i).get('power') or 0) for i in group)
            survives = any((self._card(o,i).get('toughness') or 0) > (enemy.get('power') or 0) for i in group)
            if incoming >= life or survives or powers >= (enemy.get('toughness') or 0):
                selected.update({i:[target] for i in group});incoming -= max(0,enemy.get('power') or 0)
        return self._action(offer, 'lathril-native-pair-blocks', {'blockers':selected}, objective='preserve-life-and-engines')

    def _combat(self, o, offer):
        if offer['kind'] == 'DeclareBlockers':
            return self._block(o, offer)
        if (offer.get('mandatoryAttackers') or offer.get('loneAttackers')
            or any(p.get('teamIndex') is not None for p in o['state'].get('players', []))):
            raise PilotContractError('lathril-unsupported-combat-requirements')
        targets = offer.get('validAttackTargets')
        attackers = offer.get('validAttackers')
        if not isinstance(targets, list) or not isinstance(attackers, list):
            raise PilotContractError('lathril-incomplete-combat-menu')
        selected = {}; ready = self._elves(o, True)
        for i in attackers:
            c = self._card(o, i)
            if c.get('hasSummoningSickness') or c.get('isTapped') or (c.get('power') or 0) <= 0:
                continue
            if len(ready) >= 11 and i in ready:
                continue  # Preserve the preferred drain plan rather than emptying it.
            pairs = offer.get('attackTargetsByAttacker')
            allowed = pairs.get(i, []) if isinstance(pairs, dict) else targets
            for target in targets:
                if target not in allowed:
                    continue
                blockers = [b for b in ids_in(o['state'], 'Battlefield', target) if 'CREATURE' in self._card(o, b).get('cardTypes', []) and not self._card(o, b).get('isTapped')]
                threshold = 2 if c.get('name') == self.document['commander'] and 'MENACE' in c.get('keywords', []) else 1
                if len(blockers) < threshold:
                    selected[i] = target; break
        return self._action(offer, 'lathril-connect-without-blockers', {'attackers': selected}, objective='commander-tokens-then-drain')
