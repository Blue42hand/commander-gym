"""Lathril strategy overrides over shared policies and current masked offers.

Strategic simplifications are deliberate: develop a wide Elf board, avoid unplanned
mana and reserve recovery. Unrepresented decisions defer to the bounded fallback. All selected choices use the native validator.
"""
from dataclasses import replace
import hashlib
import json
from pathlib import Path

from .deck_package import ArtifactRef
from .heuristic_facts import canonical
from .heuristic_policy import PolicyNotApplicable, ids_in, affordable, mana_cost
from .default_policies import DefaultPolicySubsystem
from .pilot import ArgentumActionChoice, PilotContractError
from .pilot_composition import SubsystemDecision

POLICY_PATH = Path(__file__).with_name('deck_policies') / 'lathril_v1.json'


def policy_document():
    return json.loads(POLICY_PATH.read_text())


def lathril_component_ref(facts):
    sources = ['lathril_policy.py', 'deck_policy_registry.py']
    from .heuristic_facts import heuristic_component_ref
    payload = {'defaults': heuristic_component_ref(facts).digest, 'policy': policy_document(),
               'sources': {p: hashlib.sha256(Path(__file__).with_name(p).read_bytes()).hexdigest() for p in sources}}
    return ArtifactRef(kind='skill-set', artifact_id='lathril-primer-policy', version='2',
                       digest='sha256:' + hashlib.sha256(canonical(payload)).hexdigest())


def validate_lathril_binding(binding):
    doc = policy_document()
    if (binding.deck.fingerprint != doc['deck_fingerprint'] or binding.deck_knowledge is None
        or binding.deck_knowledge.fingerprint != doc['primer_fingerprint']):
        raise PilotContractError('Lathril policy requires its exact reviewed deck and primer')


class LathrilDeckSubsystem(DefaultPolicySubsystem):
    name = 'lathril-primer-policy'
    version = '2'
    deck_policy = 'lathril-primer-v2'

    def __init__(self, facts, config=None, transport=None):
        super().__init__(facts)
        self.document = policy_document()

    def try_choose(self, observation):
        result = super().try_choose(observation)
        metadata = {**result.metadata, 'deckPolicy': self.document['policy_id'],
                    'policyDigest': lathril_component_ref(self.facts).digest}
        if result.choice is None: return replace(result, metadata=metadata)
        return replace(result, choice=replace(result.choice, metadata={**result.choice.metadata, 'deckPolicy': metadata}), metadata=metadata)

    def _elves(self, o, untapped=False):
        return [i for i in ids_in(o['state'], 'Battlefield', o['agentToAct'])
                if 'CREATURE' in self._card(o, i).get('cardTypes', [])
                and 'Elf' in self._card(o, i).get('subtypes', [])
                and (not untapped or not self._card(o, i).get('isTapped'))]

    def _drain_payment(self, o, candidates):
        # Sick tokens can pay the native tap-other-Elves cost. Keep useful
        # attackers, mana creatures and engines available whenever possible.
        return sorted(candidates,key=lambda i:(not self._card(o,i).get('hasSummoningSickness'),
            not self._card(o,i).get('isToken'),self._card(o,i).get('power') or 0,i))[:10]

    def _choose_actions(self, o):
        if o.get('nativePaymentError') is not None:
            raise PolicyNotApplicable('lathril-unsupported-payment-correction')
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
        for a in offers:
            raw = a.get('action',{}); cost = a.get('additionalCostInfo') or {}
            if (quiet and s.get('activePlayerId') == o['agentToAct'] and raw.get('abilityId') == 'Quirion Ranger:1'
                and cost.get('bounceCount') == 1 and not cost.get('alsoRequired')
                and a.get('minTargets') == a.get('targetCount') == 1 and a.get('targetRequirements') is None):
                targets = [t for t in a.get('validTargets',[]) if self._card(o,t).get('name') == self.document['commander'] and self._card(o,t).get('isTapped')]
                forests = [t for t in cost.get('validBounceTargets',[]) if self._card(o,t).get('controllerId') == o['agentToAct'] and 'Forest' in self._card(o,t).get('subtypes',[])]
                if targets and forests and len(self._elves(o,True)) >= 10:
                    forests.sort(key=lambda t:(not self._card(o,t).get('isTapped'),t))
                    return self._action(a,'lathril-ranger-enable-second-drain',{'targets':[targets[0]],'bouncedPermanents':[forests[0]]})
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
                    return self._action(a, 'lathril-ten-elf-drain', {'tappedPermanents': self._drain_payment(o,candidates)}, objective='drain-opponents')
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

    def _search_score(self, o, pending, card):
        name = card.get('name'); role = self.document['roles'].get(name)
        if name == 'Quirion Ranger' and any(c.get('name') == self.document['commander'] and c.get('isTapped') for c in self._own(o)) and len(self._elves(o,True)) >= 10 and any('Forest' in c.get('subtypes',[]) for c in self._own(o)): return 500
        if role == 'finisher': return 200 if self._overrun_is_lethal(o, name) else -1
        if role == 'removal':
            own_power = sum(max(0, c.get('power') or 0) for c in self._own(o))
            enemy_power = max((sum(max(0, self._card(o, i).get('power') or 0)
                              for i in ids_in(o['state'], 'Battlefield', player['playerId']))
                              for player in o['state'].get('players', []) if player['playerId'] != o['agentToAct']), default=0)
            return 150 if enemy_power > max(10, own_power*1.5) else 20
        if 'Land' in card.get('typeLine', '') or 'LAND' in card.get('cardTypes', []):
            return 100 if pending.get('context', {}).get('sourceName') == 'Wood Elves' else 10
        return self.document['weights'].get(role, -1)

    def _opening_value(self, card):
        role = self.document['roles'].get(card.get('name'))
        base = self.document['opening_weights'].get(role, 10)
        cost = mana_cost(card.get('manaCost', self.facts.get(card.get('name')).get('manaCost', '')))
        value = base - (cost[0] * 3 if cost else 18)
        if card.get('name') in {"Patriarch's Bidding", 'Cryptolith Rite', 'Growing Rites of Itlimoc', 'Elvish Guidance'}:
            value -= 20  # Requires a developed board or graveyard.
        return value

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

    def _deck_pending(self, o, p):
        context = p.get('context', {}); source = context.get('sourceName'); rt = p.get('responseSpec', {}).get('responseType')
        if (p.get('kind') == 'SelectCardsDecision' and source == self.document['commander']
            and p.get('minSelections') == p.get('maxSelections') == 10):
            options = p.get('options', [])
            ready = set(self._elves(o, True)) - {context.get('sourceId')}
            if len(set(options) & ready) >= 10 and not any(p.get(k) for k in ['selectionLimits', 'nonSelectableOptions', 'conditionalMinimums']):
                return self._decision(p, rt, 'lathril-pay-ten-other-elves', selectedCards=self._drain_payment(o,set(options) & ready))
        if p.get('kind') == 'ChooseOptionDecision' and source in self.document['tribal_sources'] and 'Elf' in p.get('options', []):
            return self._decision(p, rt, 'lathril-tribe-is-elf', optionIndex=p['options'].index('Elf'))
        if p.get('kind') == 'ChooseColorDecision':
            choices = p.get('availableColors', [])
            for color in ['GREEN', 'BLACK', 'COLORLESS']:
                if color in choices:
                    return self._decision(p, rt, 'lathril-mana-color', color=color)
        return None

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
                from .policy_action_bounds import removal_target_value
                score=removal_target_value(o,target,self.facts,neutralize='NEUTRALIZE' in self.facts.get(card.get('name')).get('tags',[]))
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
                return SubsystemDecision(reason='lathril-uncertain-removal', metadata={
                    'candidateChoices': [{'actionId': c.action_id, 'params': dict(c.params), 'label': label} for label,c in options]})
            return self._action(best[1], 'lathril-remove-opponent-threat', {'targets': [best[2]]})
        offers = [a for a in o['legalActions'] if affordable(a)]
        unknown = [a for a in offers if a['kind'] != 'PassPriority' and a.get('isManaAbility') is not True
                   and not (a['kind'] == 'CastSpell' and self.document['roles'].get(self._card(o, a.get('action', {}).get('cardId')).get('name')) in {'finisher', 'recovery', 'sweeper'} and self._plain_cast(a))]
        if passed and not unknown:
            return self._action(passed, 'lathril-preserve-resources', objective='no-unplanned-mana-or-utility')
        raise PolicyNotApplicable('lathril-unsupported-no-policy-choice')

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
            raise PolicyNotApplicable('lathril-unsupported-block-requirements')
        cap = offer.get('maxTotalBlockers')
        if cap is None:
            cap = len(pairs)
        if type(cap) is not int or cap < 0:
            raise PilotContractError('lathril-incomplete-block-cap')
        participants=set(pairs)|{i for values in pairs.values() for i in values}
        if any(set(self._card(o,i).get('keywords',[])) & {'TRAMPLE','FIRST_STRIKE','DOUBLE_STRIKE','INFECT','WITHER','PROTECTION'} for i in participants):
            raise PolicyNotApplicable('lathril-complex-combat-needs-inference')
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
            survives = any('INDESTRUCTIBLE' in self._card(o,i).get('keywords',[]) or (self._card(o,i).get('toughness') or 0) > (enemy.get('power') or 0) for i in group)
            if incoming >= life or survives or powers >= (enemy.get('toughness') or 0):
                selected.update({i:[target] for i in group});incoming -= max(0,enemy.get('power') or 0)
        return self._action(offer, 'lathril-native-pair-blocks', {'blockers':selected}, objective='preserve-life-and-engines')

    def _combat(self, o, offer):
        if offer['kind'] == 'DeclareBlockers':
            shared=self._simple_combat(o,offer)
            if shared is not None:return shared
            if offer.get('validBlockers') == [] and offer.get('validBlockTargets') == {} and not offer.get('mandatoryBlockerAssignments'):
                return super()._combat(o, offer)
            return self._block(o, offer)
        if (offer.get('mandatoryAttackers') or offer.get('loneAttackers')
            or any(p.get('teamIndex') is not None for p in o['state'].get('players', []))):
            raise PolicyNotApplicable('lathril-unsupported-combat-requirements')
        targets = offer.get('validAttackTargets')
        attackers = offer.get('validAttackers')
        if not isinstance(targets, list) or not isinstance(attackers, list):
            raise PilotContractError('lathril-incomplete-combat-menu')
        if offer.get('additionalCostInfo') or any(p.get('activeEffects') for p in o['state'].get('players',[])):
            raise PolicyNotApplicable('lathril-unsupported-combat-effects')
        selected = {}; ready = self._elves(o, True)
        commander = next((i for i in ready if self._card(o,i).get('name') == self.document['commander']),None)
        reserved = set(self._drain_payment(o,set(ready)-{commander})+[commander]) if commander and not self._card(o,commander).get('hasSummoningSickness') and len(ready)>=11 else set()
        # Take a visible unblocked elimination before preserving a future drain.
        for player in sorted(o['state'].get('players',[]),key=lambda p:p.get('life',40)):
            target = player['playerId']
            if target not in targets or player.get('hasLost'): continue
            if any('CREATURE' in self._card(o,b).get('cardTypes',[]) and not self._card(o,b).get('isTapped') for b in ids_in(o['state'],'Battlefield',target)): continue
            pairs = offer.get('attackTargetsByAttacker')
            lethal = [i for i in attackers if not self._card(o,i).get('isTapped') and (not self._card(o,i).get('hasSummoningSickness') or 'HASTE' in self._card(o,i).get('keywords',[])) and (self._card(o,i).get('power') or 0)>0 and (not isinstance(pairs,dict) or target in pairs.get(i,[]))]
            if sum(self._card(o,i)['power'] for i in lethal) >= player.get('life',float('inf')):
                lethal.sort(key=lambda i:(-self._card(o,i)['power'],i))
                chosen = []; damage = 0
                for i in lethal:
                    chosen.append(i); damage += self._card(o,i)['power']
                    if damage >= player['life']: break
                return self._action(offer,'lathril-visible-unblocked-lethal',{'attackers':{i:target for i in chosen}})

        for i in attackers:
            c = self._card(o, i)
            if c.get('hasSummoningSickness') and 'HASTE' not in c.get('keywords',[]) or c.get('isTapped') or (c.get('power') or 0) <= 0:
                continue
            if i in reserved:
                continue  # Preserve the preferred drain plan rather than emptying it.
            pairs = offer.get('attackTargetsByAttacker')
            allowed = pairs.get(i, []) if isinstance(pairs, dict) else targets
            for target in targets:
                if target not in allowed:
                    continue
                from .combat_policies import has_landwalk_lane
                blockers = [] if has_landwalk_lane(o,c,target) else [b for b in ids_in(o['state'], 'Battlefield', target) if 'CREATURE' in self._card(o, b).get('cardTypes', []) and not self._card(o, b).get('isTapped')]
                threshold = 2 if c.get('name') == self.document['commander'] and 'MENACE' in c.get('keywords', []) else 1
                if len(blockers) < threshold:
                    selected[i] = target; break
        return self._action(offer, 'lathril-connect-without-blockers', {'attackers': selected}, objective='commander-tokens-then-drain')
