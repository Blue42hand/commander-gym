"""Default heuristics for every deck, with overridable strategy/scoring hooks.

Native legality and masked observations are authoritative. A missing rule is an
explicit defer, never a fabricated pass or a provider call from inside a heuristic.
"""
from dataclasses import replace
from copy import deepcopy
import math
from .pilot import PilotContractError
from itertools import combinations
from math import comb
from .heuristic_policy import MaskedHeuristicSubsystem, PolicyNotApplicable, ids_in, mana_cost, affordable, BASIC
from .card_policies import SharedCardPolicies
from .pilot_composition import SubsystemDecision


class UnknownCardFacts:
    """No invented facts or engine pin when an offline export was not supplied."""
    digest = 'unavailable'
    engine_revision = None
    creature_types = frozenset()
    def get(self, name): return {}
    def document(self): return {'cards': {}}


def default_document(facts):
    cards = facts.document()['cards']
    roles = {}
    for name, fact in cards.items():
        tags = set(fact.get('tags', []))
        roles[name] = ('mana' if 'RAMP' in tags else 'draw' if 'DRAW' in tags else
                       'removal' if tags & {'REMOVAL', 'EXILE_REMOVAL'} else
                       'creature' if 'CREATURE' in fact.get('types', []) else 'utility')
    return {'policy_id': 'default-policies-v2', 'roles': roles,
            'weights': {'mana': 90, 'draw': 85, 'creature': 60, 'utility': 30},
            'opening_weights': {'mana': 90, 'draw': 85, 'creature': 60, 'utility': 30, 'removal': 50},
            'lands': {**{n: [c] for n,c in BASIC.items()},
                      **{n: c['manaColors'] for n,c in cards.items() if 'LAND' in c.get('types', [])}},
            'color_priorities': dict.fromkeys('WUBRG', 7),
            'opening_color_bonus': dict.fromkeys('WUBRGC', 30),
            'mana_roles': ['mana'],
            'rules': {'develop': 'Make useful land drops and develop mana and card resources.',
                      'mana': 'Choose a useful action before spending mana.',
                      'uncertainty': 'Use bounded inference when a rule does not cover the decision.'}}


class DefaultPolicySubsystem(SharedCardPolicies, MaskedHeuristicSubsystem):
    name = 'argentum-default-policies'
    version = '2'
    deck_policy = None
    # Trusted deck code can approve specific rescue/combo self-target effects.
    self_target_exceptions = frozenset()
    reaction_timing_exceptions = frozenset()
    counter_target_exceptions = frozenset()

    def __init__(self, facts=None):
        super().__init__(facts or UnknownCardFacts())
        self.document = default_document(self.facts)

    def try_choose(self, observation):
        from .policy_action_bounds import bounded_observation
        tutor_engines = sorted(n for n,role in self.document['roles'].items() if role=='draw_engine')
        bounded, restrictions = bounded_observation(observation, self.facts, allow_self_targets=self.self_target_exceptions, preferred_engines=tutor_engines, reaction_timing_exceptions=self.reaction_timing_exceptions, counter_target_exceptions=self.counter_target_exceptions)
        result = super().try_choose(bounded)
        metadata = {**result.metadata, 'reviewedTutorEngines':tutor_engines, 'approvedReactiveTimings':sorted(self.reaction_timing_exceptions), 'approvedCounterTargets':sorted(self.counter_target_exceptions), 'approvedSelfTargetEffects':sorted(self.self_target_exceptions), 'policyRestrictions': restrictions, 'utilityPlans':bounded.get('policyUtilityPlans',[]), 'modalPlans':deepcopy(list(getattr(self,'_modal_plans',{}).values())), 'policyLayers': {
            'defaults': 'default-policies-v2', 'deck': self.deck_policy,
            'status': 'handled' if result.choice is not None else 'deferred'},
            'policyContext': {'policy': self.document['policy_id'], 'objectives': list(self.document['rules'].values())}}
        if result.choice is not None:
            return replace(result, choice=replace(result.choice, metadata={**result.choice.metadata, 'policyRestrictions':restrictions, 'utilityPlans':bounded.get('policyUtilityPlans',[]), 'policyLayers': metadata['policyLayers']}), metadata=metadata)
        return replace(result, metadata=metadata)

    def _choose(self, o):
        if not isinstance(o.get('state'), dict): return SubsystemDecision(reason='default-missing-masked-state')
        key=(o['agentToAct'],o['state'].get('turnNumber'),o['state'].get('currentStep'))
        if getattr(self,'_unfunded_attack',None)!=key:self._unfunded_attack=None
        elif o.get('pendingDecision') is None:
            declaration=next((a for a in o.get('legalActions',[]) if a.get('kind')=='DeclareAttackers'),None)
            if declaration and not declaration.get('mandatoryAttackers'):
                return self._action(declaration,'default-cancel-unfunded-attack',{'attackers':{}})
        from .modal_policies import remember_modal_plans
        remember_modal_plans(self,o)
        if 'mulligan' in o.get('state', {}): return self._opening(o)
        if o.get('pendingDecision') is not None: return self._pending(o, o['pendingDecision'])
        shared = self._card_actions(o)
        if shared is not None: return shared
        return self._choose_actions(o)

    def _simple_combat(self,o,offer):
        from .combat_policies import combat_choice
        return combat_choice(self,o,offer)

    def _combat(self, o, offer):
        shared=self._simple_combat(o,offer)
        if shared is not None:return shared
        if (offer.get('kind') == 'DeclareBlockers' and offer.get('validBlockers') == []
            and offer.get('validBlockTargets') == {} and not offer.get('mandatoryBlockerAssignments')):
            return self._action(offer, 'default-no-available-blockers', {'blockers':{}})
        return super()._combat(o, offer)

    def _choose_actions(self, o):
        legal = o.get('legalActions', [])
        known = all(any(k in a for k in ['affordable','isAffordable']) and
                    all(type(a[k]) is bool for k in ['affordable','isAffordable'] if k in a) and
                    not ('affordable' in a and 'isAffordable' in a and a['affordable'] != a['isAffordable']) for a in legal)
        offers = [a for a in legal if affordable(a)] if known else []
        passed = next((a for a in offers if a.get('kind') == 'PassPriority'), None)
        if passed and all(a.get('kind') == 'PassPriority' or a.get('isManaAbility') is True for a in offers):
            return self._action(passed, 'default-no-unplanned-mana')
        if known:
            setup=self._commander_mana_setup(o,offers)
            if setup is not None:return setup
        return super()._choose(o)

    def _commander_mana_setup(self,o,offers):
        """Develop toward a commander's printed cost before discretionary plays.

        This forecasts capacity after an untap, never current affordability or a
        guaranteed future cast (taxes and changing boards remain native).
        Deck policies may replace this hook for a different development plan.
        """
        s=o['state'];me=o['agentToAct'];cards=s.get('cards',{})
        if (o.get('nativePaymentError') is not None or o.get('pendingDecision') is not None
            or s.get('activePlayerId')!=me or s.get('currentStep') not in {'PRECOMBAT_MAIN','POSTCOMBAT_MAIN'}
            or ids_in(s,'Stack') or any(a.get('kind')=='PlayLand' for a in offers)):return None
        own=self._own(o)
        if any(c.get('isCommander') for c in own):return None
        commanders=[cards[i] for i in ids_in(s,'Command',me) if cards.get(i,{}).get('isCommander')]
        if len(commanders)!=1:return None
        commander=commanders[0]
        if any(a.get('kind')=='CastSpell' and cards.get(a.get('action',{}).get('cardId'),{}).get('isCommander') for a in offers):return None
        cost=mana_cost(commander.get('manaCost',self.facts.get(commander.get('name')).get('manaCost','')))
        if cost is None:return None
        # Visible immediate survival questions still go through ordinary policy
        # and inference instead of a compulsory setup move.
        life=next((p.get('life') for p in s.get('players',[]) if p.get('playerId')==me),None)
        if type(life) is int and sum(max(0,cards[i].get('power') or 0) for i in ids_in(s,'Battlefield') if cards[i].get('controllerId')!=me)>=life:return None
        sources=[]
        for c in own:
            f=self.facts.get(c.get('name'))
            if c.get('activeEffects') or c.get('attachments') or c.get('isFaceDown') or c.get('isPhasedOut') or 'CREATURE' in c.get('cardTypes',[]) or f.get('variableMana'):continue
            colors=self._land_colors(c,o)
            amount=1 if 'LAND' in c.get('cardTypes',[]) else f.get('developmentMana',0)
            if colors and type(amount) is int and 0<amount<=3:sources.extend([colors]*amount)
        if self._payable(cost,sources):return None
        candidates=[]
        for a in offers:
            c=cards.get(a.get('action',{}).get('cardId'),{});f=self.facts.get(c.get('name'));amount=f.get('developmentMana',0)
            if (a.get('kind')!='CastSpell' or not self._plain_cast(a) or a.get('requiresTargets') is not False
                or set(f.get('types',[]))!={'ARTIFACT'} or type(amount) is not int or not 0<amount<=3):continue
            colors=self._land_colors(c,o)
            if colors and self._payable(cost,sources+[colors]*amount):candidates.append(a)
        if not candidates:return None
        candidates.sort(key=lambda a:(mana_cost(a.get('manaCostString','')) or (99,))[0])
        return self._action(candidates[0],'default-develop-commander-mana-capacity',objective='Develop mana toward the commander before discretionary interaction; reassess after native resolution.')
    def _opening(self, o):
        if any(self.facts.get(c.get('name')).get('variableMana')
               for c in o.get('state', {}).get('mulligan', {}).get('cards', {}).values()):
            return SubsystemDecision(reason='default-variable-opening-mana')
        m=o['state']['mulligan'];required=set(self.document.get('opening_colors',[]))
        hand=[m.get('cards',{}).get(i,{}) for i in m.get('hand',[])]
        lands=[c for c in hand if 'Land' in c.get('typeLine','')]
        # At the first opening decision, repair a known missing primary colour.
        # An unknown strategic role cannot create mana, but unknown definitions,
        # utility lands and possible fixing keep the decision with inference.
        if (m.get('mulliganCount')==0 and len(hand)==7 and m.get('cardsToPutOnBottom',0)==0 and required and lands
            and all(self.facts.get(c.get('name')).get('simpleMana') for c in lands)
            and all(self.facts.get(c.get('name')) for c in hand)
            and not set().union(*(self._land_colors(c) for c in lands)) & required
            and not any('RAMP' in self.facts.get(c.get('name')).get('tags',[]) for c in hand if c not in lands)):
            offered=next((a for a in o['legalActions'] if a.get('kind')=='TakeMulligan' and affordable(a)),None)
            if offered:return self._action(offered,'default-opening-missing-primary-mana')
        return self._mulligan(o)
    def _deck_pending(self, o, p): return None
    def _overrun_is_lethal(self, o, name): return False

    def _opening_value(self, card):
        role = self.document['roles'].get(card.get('name'))
        cost = mana_cost(card.get('manaCost', self.facts.get(card.get('name')).get('manaCost', '')))
        return self.document['opening_weights'].get(role, 40) - (cost[0] * 3 if cost else 18)

    def _development_score(self, o, card, role):
        cost = mana_cost(card.get('manaCost', self.facts.get(card.get('name')).get('manaCost', '')))
        return self.document['weights'].get(role, 30) - (cost[0] * .25 if cost else 0)

    def _search_score(self, o, pending, card):
        if 'Land' in card.get('typeLine', '') or 'LAND' in card.get('cardTypes', []):
            return 100 if pending.get('context', {}).get('sourceName') == 'Wood Elves' else 10
        role = self.document['roles'].get(card.get('name'))
        return self._development_score(o, card, role)

    def _development_value(self, card, o, fact):
        role = self.document['roles'].get(card.get('name'))
        return self._development_score(o, card, role) + (5 if card.get('isCommander') else 0)

    def _land(self, o, lands):
        # Landfall payoff first when the native menu already approves its cost.
        if not any(c.get('name') == 'Felidar Retreat' for c in self._own(o)):
            setup = next((a for a in o.get('legalActions',[]) if a.get('kind')=='CastSpell' and affordable(a)
                          and self._card(o,a.get('action',{}).get('cardId')).get('name')=='Felidar Retreat' and self._plain_cast(a)),None)
            if setup: return self._action(setup, 'shared-card-landfall-before-land')
        def timing_known(a):
            card = self._card(o,a['action'].get('cardId')); f = self.facts.get(card.get('name'))
            return (f.get('unconditionalUntapped') or f.get('entersTapped') or f.get('conditionalTappedEntry') or f.get('untappedIfLandSubtypes')
                    or f.get('untappedPayLife') or card.get('name') in {*BASIC,'Woodland Cemetery','Overgrown Tomb'})
        supported = [a for a in lands if timing_known(a) and self._land_colors(self._card(o,a['action'].get('cardId')),o)]
        if supported and len(supported) < len(lands):
            lands = supported
        if any(not self._land_colors(self._card(o,a['action'].get('cardId')),o) for a in lands):
            return SubsystemDecision(reason='default-unknown-land-mana')
        if any(not timing_known(a) for a in lands):
            return SubsystemDecision(reason='default-unknown-land-timing')
        ranked = sorted(lands, key=lambda a: (-self._land_score(o,a), a['semanticId']))
        return self._action(ranked[0], 'default-develop-colored-mana')

    def _card(self, o, entity):
        return o.get('state', {}).get('cards', {}).get(entity, {})

    def _own(self, o):
        return [self._card(o, i) for i in ids_in(o['state'], 'Battlefield', o['agentToAct'])]

    @staticmethod
    def _pass(o):
        return next((a for a in o['legalActions'] if a.get('kind') == 'PassPriority' and affordable(a)), None)

    @staticmethod
    def _plain_cast(a):
        raw = a.get('action', {})
        return not (a.get('hasXCost') or a.get('additionalCostInfo') or a.get('modalEnumeration')
                    or a.get('hasConvoke') or a.get('hasDelve') or a.get('requiresDamageDistribution')
                    or raw.get('useAlternativeCost') or raw.get('chosenModes') or raw.get('castFaceDown')
                    or raw.get('faceIndex') is not None)

    def _land_colors(self, card, o=None):
        fact = self.facts.get(card.get('name'))
        if fact.get('commanderColorMana') and o is not None:
            return {color for c in o.get('state',{}).get('cards',{}).values() if c.get('isCommander') and c.get('ownerId',c.get('controllerId')) == o['agentToAct'] for color in self.facts.get(c.get('name')).get('colorIdentity',[])}
        colors = set(self.document['lands'].get(card.get('name'), fact.get('manaColors', [])))
        if o is not None and next((p.get('life',0) for p in o.get('state',{}).get('players',[]) if p.get('playerId')==o['agentToAct']),0) <= 2:
            colors -= set(fact.get('painManaColors',[]))
        return colors

    def _enters_tapped(self, card, board, o=None):
        name = card.get('name')
        life_cost = self.facts.get(name).get('untappedPayLife')
        if life_cost:
            return o is None or next((p.get('life',0) for p in o.get('state',{}).get('players',[]) if p.get('playerId')==o['agentToAct']),0) <= life_cost + 2
        condition = self.facts.get(name).get('untappedIfLandSubtypes')
        if condition:
            return not any(('LAND' in c.get('cardTypes', []) or 'Land' in c.get('typeLine', ''))
                           and set(c.get('subtypes', self.facts.get(c.get('name')).get('subtypes', []))) & set(condition) for c in board)
        if name in {'Path of Ancestry', 'Temple of Malady', 'Bojuka Bog'}:
            return True
        if name == 'Woodland Cemetery':
            return not any(c.get('name') in {'Forest', 'Swamp', 'Overgrown Tomb'}
                           or set(c.get('subtypes', [])) & {'Forest', 'Swamp'} for c in board)
        # Tomb can enter untapped by paying life; native owns that decision.
        return self.facts.get(name).get('entersTapped', False) or self.facts.get(name).get('conditionalTappedEntry', False)

    def _land_score(self, o, action):
        card = self._card(o, action['action'].get('cardId'))
        board = self._own(o); colors = self._land_colors(card,o)
        supply = set().union(*(self._land_colors(c,o) for c in board if not self.facts.get(c.get('name')).get('variableMana'))) if board else set()
        score = sum(self.document.get('color_priorities', {}).get(c, 7) for c in colors - supply)
        sources = [self._land_colors(c,o) for c in board if not c.get('isTapped') and self._land_colors(c,o) and not self.facts.get(c.get('name')).get('variableMana')]
        if not self._enters_tapped(card, board,o) and not self.facts.get(card.get('name')).get('variableMana'):
            sources.append(colors)
        hand = [self._card(o, i) for i in ids_in(o['state'], 'Hand', o['agentToAct'])]
        useful = [c for c in hand if self.document['roles'].get(c.get('name')) in self.document['weights']
                  or not self.facts.get(c.get('name')) and mana_cost(c.get('manaCost')) and not self._land_colors(c,o)]
        playable = [self._opening_value(c) for c in useful
                    if (cost := mana_cost(c.get('manaCost', self.facts.get(c.get('name')).get('manaCost', ''))))
                    and cost[0] > 0 and self._payable(cost, sources)]
        # A useful cast this turn takes precedence over abstract color coverage.
        score += max(playable, default=0) * 2
        for c in useful:
            cost = mana_cost(c.get('manaCost', self.facts.get(c.get('name')).get('manaCost', '')))
            if cost and cost[0] <= 4:
                score += sum(min(cost[1][color], 2) for color in colors & set('WUBRGC')) * 2
        return score

    def _pending(self, o, p):
        override = self._deck_pending(o, p)
        if override is not None: return override
        context = p.get('context',{}); source = context.get('sourceId'); card = self._card(o,source)
        if (p.get('kind')=='YesNoDecision' and p.get('responseSpec',{}).get('responseType')=='YesNoResponse'
            and p.get('yesAvailable') is True and p.get('yesText')=='Command zone'
            and context.get('phase')=='STATE_BASED' and card.get('isCommander')
            and card.get('ownerId',card.get('controllerId'))==o['agentToAct']):
            labels={'Graveyard':['the graveyard','graveyard'],'Exile':['exile','the exile'],
                    'Hand':['your hand','hand'],'Library':['your library','the library']}
            for zone,names in labels.items():
                if source in ids_in(o['state'],zone,o['agentToAct']) and p.get('prompt') in {
                    f"Put {card.get('name')} into the command zone instead of leaving it in {name}?" for name in names}:
                    return self._decision(p,'YesNoResponse','default-keep-commander-accessible',choice=zone!='Hand')
        shared = self._card_pending(o, p)
        if shared is not None: return shared
        rt = p.get('responseSpec', {}).get('responseType'); context = p.get('context', {})
        count = p.get('minSelections')
        options = p.get('options', [])
        hand = ids_in(o.get('state', {}), 'Hand', o['agentToAct'])
        if (p.get('kind') == 'SelectCardsDecision' and rt == 'CardsSelectedResponse'
            and type(count) is int and count > 0 and p.get('maxSelections') == count
            and (p.get('prompt') == f'Discard down to 7 cards (choose {count} to discard)'
                 and context.get('phase') == 'STATE_BASED' and o['state'].get('currentStep') == 'CLEANUP' and len(hand)-7 == count
                 or context.get('sourceName') == 'Flow of Knowledge' and context.get('phase') == 'RESOLUTION'
                 and p.get('prompt') == 'Choose two cards to discard' and count == 2)
            and len(options) == len(hand) and set(options) == set(hand)
            and not p.get('librarySearch') and not any(p.get(k) for k in [
                'nonSelectableOptions', 'selectionLimits', 'conditionalMinimums', 'onePerCardType',
                'onePerColor', 'onePerCardName', 'onePerBasicLandType', 'onePerPower',
                'maxTotalManaValue', 'minTotalManaValue', 'maxTotalPower'])):
            if any(not self._card(o, i).get('name') for i in options):
                raise PolicyNotApplicable('default-incomplete-discard-hand')
            lands = sum(bool(self._land_colors(c)) for c in self._own(o))
            hand_lands = sum(bool(self._land_colors(self._card(o, i))) for i in hand)
            def discard_value(entity):
                card = self._card(o, entity)
                if 'Land' in card.get('typeLine', '') or 'LAND' in card.get('cardTypes', []):
                    return 180 if lands < 5 and hand_lands <= 2 else (60 if lands + hand_lands <= 6 else 0)
                if card.get('isCommander'):return 220
                role = self.document['roles'].get(card.get('name'))
                if role == 'finisher' and self._overrun_is_lethal(o, card.get('name')):
                    return 200
                value = self._opening_value(card)
                if role in self.document.get('mana_roles', ['mana']) and lands >= 5:
                    value -= 35
                return value
            chosen = sorted(options, key=lambda i: (discard_value(i), i))[:count]
            return self._decision(p, rt, 'default-discard-for-hand-limit', selectedCards=chosen)
        if p.get('kind') == 'BottomCards':
            cards = p.get('cards', {}); hand = p.get('hand', []); count = p.get('cardsToPutOnBottom')
            if type(count) is not int or not 0 <= count <= len(hand) or any(i not in cards for i in hand):
                raise PolicyNotApplicable('default-incomplete-bottom')
            def retained_value(retained):
                lands = [cards[i] for i in retained if 'Land' in cards[i].get('typeLine', '')]
                colors = set().union(*(self._land_colors(c) for c in lands)) if lands else set()
                pip_support = sum(min(sum(color in self._land_colors(c) for c in lands),
                                      max((cards[i].get('manaCost', '').count('{' + color + '}') for i in retained if cards[i] not in lands), default=0))
                                  for color in 'WUBRGC')
                return (pip_support * 8 + sum(self._opening_value(cards[i]) for i in retained if cards[i] not in lands)
                        + min(len(lands), 3) * 150 + max(0, len(lands)-3) * 5
                        + sum(self.document.get('opening_color_bonus', {}).get(c, 0) for c in colors))
            # Compare complete retained hands; entity IDs only settle equivalent plans.
            if comb(len(hand), count) > 512:
                raise PolicyNotApplicable('default-bottom-combination-budget')
            kept = max(combinations(sorted(hand), len(hand)-count), key=retained_value)
            return self._decision(p, rt, 'default-bottom-delayed-cards', selectedCards=sorted(set(hand)-set(kept)))
        if (p.get('kind') == 'SelectCardsDecision' and rt == 'CardsSelectedResponse'
            and p.get('prompt') == 'Scry 1: choose cards to put on the bottom of your library'
            and p.get('selectedLabel') == 'Put on bottom' and p.get('remainderLabel') == 'Put on top'
            and p.get('minSelections') == 0 and p.get('maxSelections') == 1
            and len(p.get('options', [])) == 1 and not p.get('librarySearch')
            and not any(p.get(k) for k in ['nonSelectableOptions', 'selectionLimits', 'conditionalMinimums', 'onePerCardType', 'onePerColor', 'onePerCardName', 'onePerBasicLandType', 'onePerPower', 'maxTotalManaValue', 'minTotalManaValue', 'maxTotalPower'])):
            entity = p['options'][0]
            card = p.get('cardInfo', {}).get(entity)
            if not isinstance(card, dict) or not card.get('name'):
                raise PolicyNotApplicable('default-incomplete-scry-card')
            board_lands = sum(bool(self._land_colors(c)) for c in self._own(o))
            hand = [self._card(o, i) for i in ids_in(o['state'], 'Hand', o['agentToAct'])]
            hand_lands = sum(bool(self._land_colors(c)) for c in hand)
            is_land = 'Land' in card.get('typeLine', '') or 'LAND' in card.get('cardTypes', [])
            bottom = (board_lands + hand_lands >= 4) if is_land else (self._opening_value(card) < 10 and board_lands < 5)
            return self._decision(p, rt, 'default-scry-for-development', selectedCards=[entity] if bottom else [])
        return super()._pending(o, p)


class ProfiledDeckPolicy(DefaultPolicySubsystem):
    """Data-only deck modifiers; subclasses may add hooks for special mechanics.

    Profiles are supplied by trusted registry code, never by executable catalog data.
    Unspecified scoring/behavior inherits the general policy.
    """
    def __init__(self, facts, profile):
        super().__init__(facts)
        if not isinstance(profile.get('policy_id'), str) or not profile['policy_id']:
            raise PilotContractError('deck profile requires a policy_id')
        for key in ['weights', 'opening_weights', 'color_priorities', 'opening_color_bonus']:
            if key in profile and (not isinstance(profile[key], dict) or any(
                not isinstance(k,str) or type(v) not in (int,float) or not math.isfinite(v) for k,v in profile[key].items())):
                raise PilotContractError('deck profile scoring weights must be finite numbers')
        for key in ['roles','rules']:
            if key in profile and (not isinstance(profile[key], dict) or any(
                not isinstance(k,str) or not isinstance(v,str) for k,v in profile[key].items())):
                raise PilotContractError('deck profile roles/rules must be text mappings')
        self.deck_policy = profile['policy_id']
        for key, value in deepcopy(profile).items():
            if key in {'roles','weights','opening_weights','lands','color_priorities','opening_color_bonus','rules'}:
                self.document[key] = {**self.document[key], **value}
            else: self.document[key] = value
