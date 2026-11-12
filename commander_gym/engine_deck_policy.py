"""Reviewed primer plans for Talrand, Sythis and Alela over shared mechanics.

Native affordability/targets authorize actions. Conservative mana estimates only
reserve resources; they never fabricate payment, target legality or combat results.
Unknown timing, special costs, closing tools and utility activations defer.
"""
from collections import Counter
from pathlib import Path
import json
from .default_policies import ProfiledDeckPolicy
from .heuristic_policy import affordable, ids_in, mana_cost, UNSAFE_TAGS
from .pilot_composition import SubsystemDecision


def deck_profile(key):
    return json.loads((Path(__file__).with_name('deck_policies')/(key+'_v1.json')).read_text())


def engine_deck_component_ref(facts, key):
    from .deck_policy_registry import profile_component_ref
    return profile_component_ref(facts,deck_profile(key),sources=[Path(__file__),Path(__file__).with_name('deck_policy_registry.py')])


class EngineDeckPolicy(ProfiledDeckPolicy):
    def __init__(self, facts, key):
        self.key = key
        self.profile = deck_profile(key)
        super().__init__(facts, self.profile)
        self.name = self.document['policy_id']

    def _opening(self, o):
        base = super()._opening(o)
        if base.choice is None: return base
        m = o['state']['mulligan']; count = m['mulliganCount']
        if count >= 2: return base
        chosen = next(a for a in o['legalActions'] if a['actionId'] == base.choice.action_id)
        if chosen['kind'] != 'KeepHand': return base
        cards = [m['cards'][i] for i in m['hand']]
        lands = [c for c in cards if 'Land' in c.get('typeLine','')]
        colors = set().union(*(self._land_colors(c) for c in lands)) if lands else set()
        roles = {self._role(c) for c in cards}
        plan = (bool(colors & set(self.document['opening_colors'])) and
                bool(roles & ({'cantrip','interaction','ramp'} if self.key == 'talrand' else
                              {'draw_engine','tutor','reducer','ramp'} if self.key == 'sythis' else {'draw_engine'})))
        if self.key == 'alela' and plan:
            combat_draw = {'Coastal Piracy','Bident of Thassa','Reconnaissance Mission'}
            live_draw = any(self._role(c)=='draw_engine' and c.get('name') not in combat_draw for c in cards)
            body = any('CREATURE' in self.facts.get(c.get('name')).get('types',[]) and self._role(c) not in {'payoff','aura_payoff'} for c in cards)
            plan = live_draw or (body and 'draw_engine' in roles)
        if plan: return base
        # An unreviewed card could supply the missing plan: ask the model rather
        # than reject a hand just because the profile has no label for it.
        if any(not self._role(c) and not self._land_colors(c) for c in cards):
            return SubsystemDecision(reason=self.key+'-opening-plan-uncertain')
        mull = next((a for a in o['legalActions'] if a['kind']=='TakeMulligan' and affordable(a)),None)
        return self._action(mull,self.key+'-opening-needs-engine-plan') if mull else base

    def _role(self, card): return self.profile['roles'].get(card.get('name'))

    def _engines(self, o):
        return [c for c in self._own(o) if self._role(c) in {'draw_engine','commander'}
                and (self.key == 'sythis' or self._role(c) == 'draw_engine')]

    def _development_score(self, o, card, role):
        score = super()._development_score(o,card,role)
        own = self._own(o); engines = self._engines(o)
        lands = sum('LAND' in c.get('cardTypes',[]) for c in own)
        if role == 'ramp' and lands >= 5: score -= 50
        if role == 'draw_engine' and not engines: score += 35
        if role == 'draw_engine' and len(engines) >= 2: score -= 65
        if role == 'commander':
            if self.key == 'talrand': score += 45
            elif self.key == 'sythis': score += 45
            elif engines: score += 40
            else: score -= 45
        if self.key == 'sythis' and role == 'ramp' and 'ENCHANTMENT' in self.facts.get(card.get('name')).get('types',[]): score += 25
        if self.key == 'alela' and role == 'payoff':
            score += min(35,7*sum('Faerie' in c.get('subtypes',[]) for c in own))
        if self.key == 'talrand' and role == 'cantrip':
            commander = any(c.get('name') == self.document['commander'] for c in own)
            score += 35 if commander else -25
        return score

    def _mana_sources(self, o):
        sources = []
        for c in self._own(o):
            f = self.facts.get(c.get('name'))
            if c.get('isTapped') or c.get('hasSummoningSickness') or c.get('activeEffects'): continue
            if not (f.get('simpleMana') or 'LAND' in c.get('cardTypes',[])): continue
            if f.get('variableMana'): continue
            colors = set(f.get('manaColors',[]))
            if colors: sources.append(colors)
        return sources

    def _reserved(self, o, card):
        """Conservative combined pip/total bound, never a mana activation plan."""
        hand = [self._card(o,i) for i in ids_in(o['state'],'Hand',o['agentToAct'])]
        counters = [mana_cost(c.get('manaCost',self.facts.get(c.get('name')).get('manaCost','')))
                    for c in hand if self._role(c) == 'interaction' or 'COUNTERSPELL' in self.facts.get(c.get('name')).get('tags',[])]
        counters = [cost for cost in counters if cost]
        reserve = min(counters,key=lambda cost:cost[0]) if counters else None
        floor = self.document['reserve_mana'] if self._role(card) == 'commander' else 0
        if reserve is None and not floor: return True
        cost = mana_cost(card.get('manaCost',self.facts.get(card.get('name')).get('manaCost','')))
        if cost is None: return False
        reserve_total = max(reserve[0] if reserve else 0,floor)
        pips = cost[1] + (reserve[1] if reserve else Counter())
        return self._payable((cost[0]+reserve_total,pips),self._mana_sources(o))

    def _draw_safe(self, o, card):
        own = self._own(o)
        discovery = any(c.get('name') == 'Kindred Discovery' for c in own)
        if not discovery and card.get('name') != 'Kindred Discovery': return True
        player = next((p for p in o['state'].get('players',[]) if p['playerId']==o['agentToAct']),{})
        # Establish neither a mandatory draw engine nor new token production near
        # deck-out. An alternate-win replacement requires its own policy/model.
        return type(player.get('librarySize')) is int and player['librarySize'] > 8

    def _cast_params(self, o, a, card, role):
        if not self._plain_cast(a): return None
        if a.get('requiresTargets') is False: return {}
        if a.get('targetCount') != 1 or a.get('minTargets') != 1 or a.get('targetRequirements') is not None: return None
        if card.get('name') in {'Fertile Ground','Abundant Growth'}:
            own = set(ids_in(o['state'],'Battlefield',o['agentToAct']))
            targets = [i for i in a.get('validTargets',[]) if i in own and 'LAND' in self._card(o,i).get('cardTypes',[])]
            if not targets: return None
            # Spread land Auras; prefer basics over exposed utility lands.
            targets.sort(key=lambda i:(len(self._card(o,i).get('attachments',[])),not self.facts.get(self._card(o,i).get('name')).get('subtypes'),i))
            return {'targets':[targets[0]]}
        if role == 'aura_payoff':
            own = set(ids_in(o['state'],'Battlefield',o['agentToAct']))
            targets = [i for i in a.get('validTargets',[]) if i in own and 'CREATURE' in self._card(o,i).get('cardTypes',[])
                       and not self._card(o,i).get('hasSummoningSickness') and not self._card(o,i).get('isTapped')
                       and len(self._card(o,i).get('attachments',[])) < 2]
            if not targets or not self._engines(o): return None
            targets.sort(key=lambda i:(len(self._card(o,i).get('attachments',[])), 'FLYING' not in self._card(o,i).get('keywords',[]),-self._card(o,i).get('power',0),i))
            return {'targets':[targets[0]]}
        return None

    def _develop(self, o, productive):
        ranked = []; reserved = False
        own = self._own(o); engines = self._engines(o)
        commander = any(c.get('name') == self.document['commander'] for c in own)
        for a in productive:
            if a['kind'] != 'CastSpell': continue
            card = self._card(o,a.get('action',{}).get('cardId'));role = self._role(card)
            if role not in {'ramp','draw_engine','reducer','commander','cantrip','payoff','aura_payoff','enabler'}: continue
            fact = self.facts.get(card.get('name'))
            if not fact or not self._draw_safe(o,card): continue
            if role == 'draw_engine' and len(engines) >= 2: continue
            # Combat-draw engines need bodies; entry/cast engines don't.
            if card.get('name') in {'Coastal Piracy','Bident of Thassa','Reconnaissance Mission'} and not any('CREATURE' in c.get('cardTypes',[]) for c in own): continue
            if role == 'payoff' and not (commander or engines): continue
            if role == 'aura_payoff' and (card.get('name') == 'Tempest Technique' or len(own)<5): continue
            if role == 'cantrip' and self.key == 'talrand' and not commander and sum('LAND' in c.get('cardTypes',[]) for c in own)>=3: continue
            if set(fact.get('tags',[])) & UNSAFE_TAGS and role != 'aura_payoff': continue
            params = self._cast_params(o,a,card,role)
            if params is None: continue
            if not (role == 'ramp' and sum('LAND' in c.get('cardTypes',[]) for c in own)<3) and not self._reserved(o,card): reserved = True; continue
            ranked.append((self._development_score(o,card,role),a,params))
        if ranked:
            ranked.sort(key=lambda item:(-item[0],item[1]['semanticId']))
            _,a,params = ranked[0]
            return self._action(a,self.key+'-primer-development',params,objective=self.document['rules']['plan'])
        if reserved and all(a['kind']=='CastSpell' and self._role(self._card(o,a.get('action',{}).get('cardId'))) in
                            {'commander','cantrip','interaction'} for a in productive):
            passed = self._pass(o)
            if passed: return self._action(passed,self.key+'-keep-interaction-mana')
        # No blanket pass: unrepresented utility, targeting, closing and recovery
        # decisions always reach inference with the deck's compact objectives.
        return SubsystemDecision(reason=self.key+('-reserve-interaction' if reserved else '-uncovered-strategy'))

    def _deck_pending(self, o, p):
        if p.get('kind') != 'ChooseOptionDecision' or p.get('responseSpec',{}).get('responseType') != 'OptionChosenResponse': return None
        source = p.get('context',{}).get('sourceName')
        desired = ('Enchantment' if source == 'Cloud Key' and self.key == 'alela' else
                   'Faerie' if source in {'Kindred Discovery','Patchwork Banner','Etchings of the Chosen'} and self.key == 'alela' else None)
        if desired is None: return None
        options = p.get('options',[])
        indexes = [i for i,label in enumerate(options) if isinstance(label,str) and label.casefold() == desired.casefold()]
        if len(indexes) != 1: return SubsystemDecision(reason=self.key+'-unrecognized-payoff-options')
        return self._decision(p,'OptionChosenResponse',self.key+'-choose-engine-type',optionIndex=indexes[0])

    def _combat(self, o, offer):
        # Only quiet, unconstrained pressure plans are shortlisted. Mandatory
        # assignments, blocks, taxes, teams and unsupported boards retain native
        # inference. The model compares complete plans, never individual attackers.
        s = o['state']; me = o['agentToAct']
        attackers = offer.get('validAttackers'); targets = offer.get('validAttackTargets')
        if (offer.get('kind') != 'DeclareAttackers' or not isinstance(attackers,list) or not isinstance(targets,list)
            or offer.get('mandatoryAttackers') or offer.get('loneAttackers') or offer.get('additionalCostInfo')
            or any(p.get('teamIndex') is not None or p.get('activeEffects') for p in s.get('players',[]))):
            return super()._combat(o,offer)
        board = [self._card(o,i) for i in ids_in(s,'Battlefield')]
        if any(c.get('activeEffects') or c.get('isFaceDown') or c.get('isPhasedOut') or
               c.get('name') in {'Ghostly Prison','Propaganda','Dueling Grounds','Sphere of Safety'} for c in board):
            return super()._combat(o,offer)
        player = next((p for p in s.get('players',[]) if p['playerId']==me),{})
        if any(c.get('name')=='Kindred Discovery' for c in self._own(o)) and (
            type(player.get('librarySize')) is not int or player['librarySize'] <= len(attackers)*2+4):
            return SubsystemDecision(reason=self.key+'-combat-draw-budget-uncertain')
        enemies = {p['playerId']:p for p in s.get('players',[]) if p['playerId'] != me and not p.get('hasLost')}
        plans = [{}]
        pairs = offer.get('attackTargetsByAttacker')
        for target in sorted((t for t in targets if t in enemies),key=lambda t:(enemies[t].get('life',40),t)):
            ready = []
            blockers = [self._card(o,i) for i in ids_in(s,'Battlefield',target)
                        if 'CREATURE' in self._card(o,i).get('cardTypes',[]) and not self._card(o,i).get('isTapped')]
            for entity in attackers:
                c = self._card(o,entity)
                allowed = pairs.get(entity,[]) if isinstance(pairs,dict) else targets
                if target not in allowed or c.get('isTapped') or c.get('hasSummoningSickness') or (c.get('power') or 0)<=0: continue
                flying = 'FLYING' in c.get('keywords',[])
                if not (c.get('isToken') and flying or c.get('attachments') and (c.get('power') or 0)>=3): continue
                if any(not flying or set(b.get('keywords',[])) & {'FLYING','REACH'} for b in blockers): continue
                ready.append(entity)
            if ready:
                plans.append({i:target for i in ready})
                if len(ready)>1: plans.append({i:target for i in ready[:-1]})
                break  # One reviewed target; no combinatorial assignment search.
        if len(plans)==1: return super()._combat(o,offer)
        return SubsystemDecision(reason=self.key+'-bounded-pressure-plans',metadata={
            'candidateChoices':[{'actionId':offer['actionId'],'params':{'attackers':p}} for p in plans]})
