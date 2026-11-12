"""Reviewed competency restrictions on a copy of the current native menu.

These are policy bounds, not new legality claims. Unknown mechanics retain their
native offer. Never mutate the recorded observation or manufacture an action.
"""
from copy import deepcopy
from .heuristic_policy import affordable, ids_in, mana_cost

# Self-removal needs a separately reviewed rescue/combo policy. None is approved
# for these ordinary removal effects yet; complex multi-slot effects stay native.
OPPONENT_REMOVAL = frozenset({'Reality Shift', 'Seal of Primordium'})


def tutor_payoff_score(name, facts, preferred_engines=()):
    """Shared payoff ordering usable both before sacrifice and at native search."""
    fact = facts.get(name); tags = fact.get('tags',[])
    cost = mana_cost(fact.get('manaCost',''))
    return (400 if name in preferred_engines else 200 if 'DRAW' in tags and fact.get('repeatable') else
            100 if fact.get('repeatable') else 50 if 'RAMP' in tags else 20) + fact.get('staticValue',0) - (cost[0] if cost else 0)


def bounded_observation(o, facts, *, allow_self_targets=(), preferred_engines=()):
    view = deepcopy(o); cards = view.get('state', {}).get('cards', {}); me = view['agentToAct']
    restrictions = []; retained = []; plans = []
    for a in view.get('legalActions', []):
        raw = a.get('action', {}); name = cards.get(raw.get('cardId', raw.get('sourceId')), {}).get('name')
        state = o.get('state',{})
        if (not o.get('pendingDecision') and state.get('activePlayerId') == me
            and state.get('currentStep') not in {'PRECOMBAT_MAIN','POSTCOMBAT_MAIN'} and not ids_in(state,'Stack')
            and (name in {'Think Twice','Flow of Knowledge'} and a.get('kind') in {'CastSpell','ActivateAbility'}
                 or name == 'Flickering Ward' and a.get('kind') == 'ActivateAbility')):
            restrictions.append({'action':a['semanticId'],'rule':'reviewed-resource-spending-waits-for-main'}); continue
        harmful = (a.get('kind') == 'CastSpell' and facts.get(name).get('spellPolarity') == ['HARMFUL']) or name in OPPONENT_REMOVAL
        if harmful and name not in allow_self_targets and a.get('requiresTargets') is True and a.get('targetRequirements') is None and isinstance(a.get('validTargets'),list):
            # Retain unknown ownership rather than inventing an enemy controller.
            targets = [t for t in a['validTargets'] if cards.get(t, {}).get('controllerId', t) != me]
            if targets != a['validTargets']:
                restrictions.append({'action':a['semanticId'],'rule':'opponent-removal-targets'})
                a['validTargets'] = targets
            if len(targets) < a.get('minTargets',1): continue
        if (name == 'Flickering Ward' and a.get('kind') == 'CastSpell' or raw.get('abilityId') == 'Timberwatch Elf:1') and a.get('targetRequirements') is None and isinstance(a.get('validTargets'),list):
            a['validTargets'] = [t for t in a['validTargets'] if cards.get(t,{}).get('controllerId') == me]
            restrictions.append({'action':a['semanticId'],'rule':'beneficial-effect-own-creatures'})
            if len(a['validTargets']) < a.get('minTargets',1): continue
        # For reviewed ordinary hostile Auras, choose strategically meaningful
        # targets. Unknown effects and complex slot grammars remain untouched.
        if name in {'Arrest','Darksteel Mutation','Noggle the Mind'} and a.get('targetRequirements') is None and a.get('minTargets') == a.get('targetCount') == 1:
            targets = a.get('validTargets',[])
            def threat(t):
                c = cards.get(t,{})
                return (100 if c.get('isCommander') else 0) + facts.get(c.get('name')).get('staticValue',0) + max(0,c.get('power') or 0)
            ranked = sorted(targets,key=lambda t:(-threat(t),t))
            if ranked and all(cards.get(t,{}).get('controllerId') not in (None,me) for t in ranked):
                a['validTargets'] = [t for t in ranked if threat(t) >= threat(ranked[0])-3][:3]
                if a['validTargets'] != targets: restrictions.append({'action':a['semanticId'],'rule':'reviewed-removal-threat-shortlist'})
        if name == "Green Sun's Zenith" and a.get('hasXCost') and type(a.get('maxAffordableX')) is int:
            deck = o.get('knownDeck', {}).get('cards', {})
            eligible = []; green_payoffs = []
            # An exact public deck list can prove no eligible payoff. Missing
            # definitions prevent that proof and leave the choice to the model.
            complete = bool(deck) and all(facts.get(n) for n in deck)
            for n,count in deck.items():
                f = facts.get(n); cost = mana_cost(f.get('manaCost',''))
                if count and 'CREATURE' in f.get('types',[]) and cost and cost[0] <= a['maxAffordableX']:
                    eligible.append(cost[0])
                    if cost[1].get('G',0) and count > sum(1 for z in o.get('state',{}).get('zones',[]) if z.get('isVisible') and z.get('zoneId',{}).get('ownerId')==me for i in z.get('cardIds',[]) if cards.get(i,{}).get('name')==n): green_payoffs.append((n,cost[0]))
            if complete and not eligible:
                restrictions.append({'action':a['semanticId'],'rule':'search-without-eligible-payoff'}); continue
            if complete and eligible: a['minX'] = max(a.get('minX') or 0, min(eligible))
            if green_payoffs:
                preferred, x = sorted(green_payoffs,key=lambda item:(-tutor_payoff_score(item[0],facts,preferred_engines), item[1], item[0]))[0]
                plans.append({'action':a['semanticId'],'source':name,'goal':'Put a green development creature onto the battlefield',
                              'preferredCard':preferred,'suggestedX':x,'binding':'Use only current native X bounds; rebind against native search options.'})
        if name == 'Sterling Grove' and a.get('kind') == 'ActivateAbility' and raw.get('abilityId') == 'Sterling Grove:1':
            deck = o.get('knownDeck',{}).get('cards',{})
            visible_names = [cards[i].get('name') for z in o.get('state',{}).get('zones',[]) if z.get('isVisible') and z.get('zoneId',{}).get('ownerId') == me for i in z.get('cardIds',[]) if i in cards]
            candidates = [n for n,count in deck.items() if count > visible_names.count(n) and 'ENCHANTMENT' in facts.get(n).get('types',[]) and n != 'Sterling Grove']
            engines = [n for n in candidates if 'DRAW' in facts.get(n).get('tags',[]) and facts.get(n).get('repeatable')]
            if candidates:
                preferred = sorted(engines or candidates, key=lambda n:(-tutor_payoff_score(n,facts,preferred_engines),n))[0]
                plans.append({'action':a['semanticId'],'source':name,'goal':'Search for a missing draw engine' if engines else 'Search for enchantment development','preferredCard':preferred,'binding':'Recompute against native search options; no queued entity IDs.'})
            elif deck and all(facts.get(n) for n in deck):
                restrictions.append({'action':a['semanticId'],'rule':'tutor-without-known-payoff'}); continue
        if name == 'Wirewood Symbiote' and raw.get('abilityId') == 'Wirewood Symbiote:1':
            # Quiet main-phase untap with no tapped target cannot gain mana or
            # rescue from a stack object. Other windows remain uncertain.
            s = o.get('state', {})
            if s.get('currentStep') in {'PRECOMBAT_MAIN','POSTCOMBAT_MAIN'} and s.get('activePlayerId') == me and not ids_in(s,'Stack'):
                targets = a.get('validTargets')
                if isinstance(targets,list) and targets and all(t in cards and not cards[t].get('isTapped') for t in targets):
                    restrictions.append({'action':a['semanticId'],'rule':'untap-without-tapped-target'}); continue
        retained.append(a)
    # Native AutoPay already funds an affordable cast/cycle. Keep explicit mana
    # decisions and menus without such a line intact (including enabling mana).
    if not o.get('pendingDecision') and any(affordable(a) and a.get('isManaAbility') is not True and a.get('kind') in {'CastSpell','ActivateAbility'} and a.get('action',{}).get('paymentStrategy',{}).get('type') == 'AutoPay' for a in retained):
        productive = [a for a in retained if a.get('isManaAbility') is not True]
        if productive:
            if len(productive)!=len(retained): restrictions.append({'rule':'native-autopay-before-manual-mana'})
            retained=productive
    pending = view.get('pendingDecision')
    if (isinstance(pending,dict) and pending.get('context',{}).get('sourceName') == 'Reclamation Sage' and 'Reclamation Sage' not in allow_self_targets
        and pending.get('responseSpec',{}).get('responseType') == 'TargetsResponse'
        and isinstance(pending.get('legalTargets'),dict)):
        for slot,targets in pending['legalTargets'].items():
            if isinstance(targets,list):
                filtered = [t for t in targets if cards.get(t,{}).get('controllerId',t) != me]
                requirement = next((r for r in pending.get('targetRequirements',[]) if str(r.get('index')) == str(slot)),{})
                # A mandatory target is selected before this optional trigger
                # asks whether to destroy it. Keep a forced self target legal;
                # the resolution policy declines destruction in that case.
                if len(filtered) >= requirement.get('minTargets',1): pending['legalTargets'][slot] = filtered
        restrictions.append({'rule':'reviewed-etb-removal-opponent-targets'})
    view['legalActions']=retained
    view['policyUtilityPlans']=plans
    return view,restrictions
