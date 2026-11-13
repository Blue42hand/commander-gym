"""Reviewed competency restrictions on a copy of the current native menu.

These are policy bounds, not new legality claims. Unknown mechanics retain their
native offer. Never mutate the recorded observation or manufacture an action.
"""
from copy import deepcopy
from .heuristic_policy import affordable, ids_in, mana_cost

# Ordinary bounce can rescue a disabled commander or a visible hostile target.
# Other self-target plans require a deck override; complex slots stay native.
BOUNCE_SOURCES = frozenset({'Boomerang','Into the Roil','Unsummon','Echoing Truth','Blink of an Eye','Otawara, Soaring City'})
ETB_EXILERS = frozenset({'Sheltered by Ghosts','Oblivion Ring','Banishing Light','Detention Sphere','Ossification','Stasis Snare'})
OPPONENT_REMOVAL = frozenset({'Reality Shift','Seal of Primordium','Seal of Cleansing',*BOUNCE_SOURCES})
QUIET_REACTIONS = frozenset({*OPPONENT_REMOVAL,*ETB_EXILERS,'Swords to Plowshares','Generous Gift','Pongify','Rapid Hybridization','Resculpt','Beast Within',"Nature's Claim",'Arrest','Pacifism','Darksteel Mutation','Imprisoned in the Moon','Lignify','Amphibian Downpour'})


NEUTRALIZING_AURAS=frozenset({'Arrest','Pacifism','Darksteel Mutation','Imprisoned in the Moon','Lignify','Noggle the Mind','Amphibian Downpour',"Faith's Fetters"})


ABILITY_REMOVING_AURAS=frozenset({'Darksteel Mutation','Imprisoned in the Moon','Lignify','Noggle the Mind','Amphibian Downpour'})


def abilities_removed(o,card,facts=None):
    cards=o['state'].get('cards',{})
    return any(cards.get(i,{}).get('name') in ABILITY_REMOVING_AURAS
        or facts is not None and facts.get(cards.get(i,{}).get('name')).get('removesAttachedAbilities') is True
        for i in card.get('attachments',[]))


def removal_target_value(o,t,facts,*,neutralize=False):
    cards=o['state'].get('cards',{});me=o['agentToAct'];c=cards.get(t,{});f=facts.get(c.get('name'))
    attached={cards.get(i,{}).get('name') for i in c.get('attachments',[])}
    body=max(0,c.get('power') or 0)+max(0,c.get('toughness') or 0)
    if abilities_removed(o,c,facts):score=body
    else:
        score=(1 if attached & NEUTRALIZING_AURAS else body)+f.get('staticValue',0)
        if c.get('isCommander'):score+=30
        if 'DRAW' in f.get('tags',[]) and f.get('repeatable'):score+=15
        # Arrest suppresses activated mana; Faith's Fetters exempts mana abilities.
        if 'RAMP' in f.get('tags',[]) and f.get('variableMana') and 'Arrest' not in attached:score+=15
    if (c.get('name') in NEUTRALIZING_AURAS or f.get('removesAttachedAbilities')) and not neutralize:
        freed=[v for v in cards.values() if t in v.get('attachments',[]) and v.get('controllerId')==me]
        if freed:score+=80+max(40 if v.get('isCommander') else 0 for v in freed)
    if 'LAND' in c.get('cardTypes',[]):score-=15
    return score


def ranked_removal_targets(o,targets,facts,*,neutralize=False):
    score=lambda t:removal_target_value(o,t,facts,neutralize=neutralize)
    active=[t for t in targets if not abilities_removed(o,o['state'].get('cards',{}).get(t,{}),facts)]
    if active:
        best=max(map(score,active))
        targets=[t for t in targets if t in active or score(t)>=best]
    ranked=sorted(targets,key=lambda t:(-score(t),t))
    return [t for t in ranked if score(t)>=score(ranked[0])-3][:3] if ranked else []


def reviewed_exile_decision(p):
    rows=p.get('targetRequirements')
    constraints=['sameOwner','sameNumericProperty','selectionGroups','exclusiveGroups','distinctCategories','totalCap','differentNames','differentControllers','mustDifferFromEarlier']
    return (p.get('context',{}).get('sourceName') in ETB_EXILERS
        and (p.get('context',{}).get('effectHint') or '').startswith('Exile target ')
        and p.get('responseSpec',{}).get('responseType') in {'TargetsResponse','TargetsChosenResponse'}
        and isinstance(rows,list) and len(rows)==1 and rows[0].get('minTargets')==rows[0].get('maxTargets')==1
        and not any(rows[0].get(k) for k in constraints) and isinstance(p.get('legalTargets'),dict))


def fixed_stat_reduction(p,facts):
    """Exact exported primitive; complex native target contracts stay uncertain."""
    rows=p.get('targetRequirements');context=p.get('context',{})
    normalize=lambda text:text.strip().removesuffix('.').casefold() if isinstance(text,str) else None
    matches=[v for k,v in facts.get(context.get('sourceName')).get('fixedStatReductions',{}).items()
             if normalize(k)==normalize(context.get('effectHint'))]
    effect=matches[0] if len(matches)==1 else None
    constraints=['sameOwner','sameNumericProperty','selectionGroups','exclusiveGroups','distinctCategories','totalCap','differentNames','differentControllers','mustDifferFromEarlier']
    if (isinstance(effect,dict) and type(effect.get('power')) is int and effect['power']<=0
        and type(effect.get('toughness')) is int and effect['toughness']<0
        and p.get('responseSpec',{}).get('responseType') in {'TargetsResponse','TargetsChosenResponse'}
        and isinstance(rows,list) and len(rows)==1 and rows[0].get('minTargets')==rows[0].get('maxTargets')==1
        and not any(rows[0].get(k) for k in constraints) and isinstance(p.get('legalTargets'),dict)):
        return effect
    return None


def copied_neutralization_targets(o,p,facts):
    """Spread an ability-removing Aura copy across distinct live threats."""
    ctx=p.get('context',{});name=ctx.get('sourceName');rows=p.get('targetRequirements')
    if (not facts.get(name).get('removesAttachedAbilities') or ctx.get('effectHint')!='Copy of '+name
        or p.get('responseSpec',{}).get('responseType') not in {'TargetsResponse','TargetsChosenResponse'}
        or not isinstance(rows,list) or len(rows)!=1 or rows[0].get('minTargets') not in (0,1) or rows[0].get('maxTargets')!=1
        or any(rows[0].get(k) for k in ['sameOwner','sameNumericProperty','selectionGroups','exclusiveGroups','distinctCategories','totalCap','differentNames','differentControllers','mustDifferFromEarlier'])):return None
    state=o['state'];cards=state.get('cards',{});stack=ids_in(state,'Stack');me=o['agentToAct'];covered=set()
    threatened={t.get('spellEntityId') for i in stack for t in cards.get(i,{}).get('targets',[]) if t.get('type')=='Spell'}
    for i in stack:
        c=cards.get(i,{})
        if (i in threatened or c.get('controllerId')!=me or not facts.get(c.get('name')).get('removesAttachedAbilities')
            or c.get('abilityIdentity') or c.get('activeEffects')):continue
        targets=c.get('targets',[])
        if len(targets)==1 and targets[0].get('type')=='Permanent':covered.add(targets[0].get('entityId'))
    if not covered:return None
    slot=str(rows[0]['index']);targets=(p.get('legalTargets') or {}).get(slot)
    if not isinstance(targets,list):return None
    # Public responses targeting a pending Aura restore uncertainty; its
    # resolution is never assumed in that branch.
    candidates=[t for t in targets if t not in covered and t in ids_in(state,'Battlefield')
                and cards.get(t,{}).get('controllerId') not in (None,me)
                and not abilities_removed(o,cards.get(t,{}),facts)]
    return (slot,ranked_removal_targets(o,candidates,facts,neutralize=True)) if candidates else None


def own_bounce_rescue(o,target,facts):
    cards=o['state'].get('cards',{});card=cards.get(target,{})
    if card.get('controllerId')!=o['agentToAct'] or target not in ids_in(o['state'],'Battlefield',o['agentToAct']):return False
    if card.get('isCommander') and abilities_removed(o,card,facts):return True
    for entity in ids_in(o['state'],'Stack'):
        threat=cards.get(entity,{})
        if threat.get('controllerId') in (None,o['agentToAct']) or facts.get(threat.get('name')).get('spellPolarity')!=['HARMFUL']:continue
        if any(t.get('entityId')==target and t.get('type')=='Permanent' for t in threat.get('targets',[])):return True
    return False


def tutor_payoff_score(name, facts, preferred_engines=()):
    """Shared payoff ordering usable both before sacrifice and at native search."""
    fact = facts.get(name); tags = fact.get('tags',[])
    cost = mana_cost(fact.get('manaCost',''))
    return (400 if name in preferred_engines else 200 if 'DRAW' in tags and fact.get('repeatable') else
            100 if fact.get('repeatable') else 50 if 'RAMP' in tags else 20) + fact.get('staticValue',0) - (cost[0] if cost else 0)


# Native definitions reviewed: these counters have no optional mana payment.
UNCONDITIONAL_COUNTERS = frozenset({'Counterspell','Negate','Dissolve','Swan Song','Arcane Denial'})


def pending_counter_answers(o,facts=None):
    """Targets of an unopposed own counter attempt that should resolve first.

    This does not assume a conditional counter succeeds. Priority returns if its
    target remains after resolution. A response threatening our counter restores
    uncertainty now; unknown effects and ability copies retain model control.
    """
    state=o.get('state',{});cards=state.get('cards',{});stack=ids_in(state,'Stack')
    threatened={t.get('spellEntityId') for i in stack for t in cards.get(i,{}).get('targets',[]) if t.get('type')=='Spell'}
    covered=set()
    for i in stack:
        c=cards.get(i,{})
        if (i in threatened or c.get('controllerId')!=o['agentToAct'] or not (c.get('name') in UNCONDITIONAL_COUNTERS or facts is not None and 'COUNTERSPELL' in facts.get(c.get('name')).get('tags',[]))
            or c.get('abilityIdentity') or c.get('activeEffects')):continue
        targets=c.get('targets',[])
        if len(targets)==1 and targets[0].get('type')=='Spell' and targets[0].get('spellEntityId') in stack:
            covered.add(targets[0]['spellEntityId'])
    return covered


# Reviewed filtering spells replace themselves without establishing a permanent
# engine. Keep costly counters for material threats; deck exceptions can approve
# combo-specific uses. Unknown draw spells and tutors retain model control.
LOW_IMPACT_FILTERS=frozenset({'Brainstorm','Opt','Ponder','Preordain','Consider','Peek','Serum Visions',
    'Sleight of Hand','Impulse','Anticipate'})
ROUTINE_MANA_ROCKS=frozenset({'Thought Vessel','Mind Stone','Arcane Signet','Fellwar Stone',
    "Commander's Sphere","Wayfarer's Bauble"})


ATTACK_TAX_SOURCES=frozenset({'Propaganda','Ghostly Prison','Sphere of Safety'})


def attack_tax_defenders(o,facts=None):
    state=o.get('state',{});cards=state.get('cards',{})
    return {cards[i].get('controllerId') for i in ids_in(state,'Battlefield') if i in cards
        and cards[i].get('name') in ATTACK_TAX_SOURCES and not abilities_removed(o,cards[i],facts)}



def removal_reaction(name,facts,casting=True):
    if name in QUIET_REACTIONS:return True
    fact=facts.get(name);tags=set(fact.get('tags',[]));removal={'REMOVAL','EXILE_REMOVAL','NEUTRALIZE'}
    # Whole-card intent cannot identify a mixed permanent's individual ability.
    return bool((casting or tags<=removal) and set(fact.get('spellPolarity',[])) & {'HARMFUL','FLEXIBLE'}
        and tags & removal)


def bounded_observation(o, facts, *, allow_self_targets=(), preferred_engines=(), reaction_timing_exceptions=(), counter_target_exceptions=()):
    view = deepcopy(o); cards = view.get('state', {}).get('cards', {}); me = view['agentToAct']
    restrictions = []; retained = []; plans = []
    answered=pending_counter_answers(o,facts);held_autopay=False;state=o.get('state',{})
    for a in view.get('legalActions', []):
        raw = a.get('action', {}); name = cards.get(raw.get('cardId', raw.get('sourceId')), {}).get('name')
        state = o.get('state',{})
        quiet=not ids_in(state,'Stack')
        if a.get('kind')=='DeclareAttackers' and isinstance(a.get('validAttackTargets'),list) and not a.get('mandatoryAttackers'):
            taxed=attack_tax_defenders(o,facts);free=[t for t in a['validAttackTargets'] if t not in taxed]
            if free and len(free)!=len(a['validAttackTargets']):
                a['validAttackTargets']=free
                if isinstance(a.get('attackTargetsByAttacker'),dict):a['attackTargetsByAttacker']={i:[t for t in ts if t in free] for i,ts in a['attackTargetsByAttacker'].items()}
                restrictions.append({'action':a['semanticId'],'rule':'prefer-untaxed-defenders'})
        if a.get('kind')=='DeclareBlockers' and a.get('maxTotalBlockers')==1 and isinstance(a.get('validBlockTargets'),dict) and not a.get('mandatoryBlockerAssignments'):
            pairs={i:[t for t in ts if 'MENACE' not in cards.get(t,{}).get('keywords',[])] for i,ts in a['validBlockTargets'].items()}
            a['validBlockTargets']={i:ts for i,ts in pairs.items() if ts}
            if isinstance(a.get('validBlockers'),list):a['validBlockers']=[i for i in a['validBlockers'] if i in a['validBlockTargets']]
        casting=a.get('kind','').startswith('Cast')
        simple_untap=(a.get('kind')=='ActivateAbility' and raw.get('abilityId') in facts.get(name).get('simpleTargetUntapAbilities',[])
            and a.get('requiresTargets') is True and a.get('minTargets')==a.get('targetCount')==1
            and isinstance(a.get('validTargets'),list)
            and a.get('targetRequirements') is None and not a.get('additionalCostInfo')
            and a.get('parameterSpec',{}).get('allowedFields',{}).get('targets')=='ENTITY_ID_ARRAY')
        combat_step=state.get('currentStep') in {'BEGIN_COMBAT','DECLARE_ATTACKERS','DECLARE_BLOCKERS','FIRST_STRIKE_COMBAT_DAMAGE','COMBAT_DAMAGE','END_COMBAT'}
        if combat_step and state.get('activePlayerId')!=me:
            combat=state.get('combat');empty_combat=('combat' in state and combat is None or
                isinstance(combat,dict) and isinstance(combat.get('attackers'),list) and not combat['attackers'])
            if state.get('currentStep')=='BEGIN_COMBAT' or empty_combat:combat_step=False
        if (simple_untap and not o.get('pendingDecision') and o.get('nativePaymentError') is None and quiet and not combat_step and name not in allow_self_targets):
            targets=[t for t in a.get('validTargets') or [] if cards.get(t,{}).get('isTapped') is not False and cards.get(t,{}).get('controllerId') in (None,me)]
            if not targets:restrictions.append({'action':a['semanticId'],'rule':'untap-without-useful-own-target'});continue
            a['validTargets']=targets
            own_main=state.get('activePlayerId')==me and state.get('currentStep') in {'PRECOMBAT_MAIN','POSTCOMBAT_MAIN'}
            if not own_main and state.get('currentStep') not in {'END','END_STEP'} and name not in reaction_timing_exceptions:
                restrictions.append({'action':a['semanticId'],'rule':'untap-waits-for-development-combat-or-end'});continue
        if (quiet and not o.get('pendingDecision') and o.get('nativePaymentError') is None
            and a.get('kind')=='ActivateAbility' and raw.get('abilityId') in facts.get(name).get('manaOnlySelfUntapAbilities',[])
            and name not in allow_self_targets and cards.get(raw.get('sourceId'),{}).get('isTapped') is False):
            restrictions.append({'action':a['semanticId'],'rule':'avoid-untapping-untapped-source'});continue
        source=cards.get(raw.get('sourceId'),{});linked=source.get('linkedExile',[])
        if (quiet and not o.get('pendingDecision') and o.get('nativePaymentError') is None
            and a.get('kind')=='ActivateAbility' and raw.get('abilityId')=='Isochron Scepter:2'
            and name not in allow_self_targets and isinstance(linked,list) and len(linked)==1
            and linked[0] in ids_in(state,'Exile') and cards.get(linked[0],{}).get('name') in UNCONDITIONAL_COUNTERS):
            # Reviewed copy-and-cast mechanic plus a known mandatory spell target.
            # Unknown imprints and live stacks still need their actual decision.
            restrictions.append({'action':a['semanticId'],'rule':'counter-imprint-needs-live-spell'});continue
        stack=ids_in(state,'Stack')
        own_stack=bool(stack) and all(cards.get(i,{}).get('controllerId')==me for i in stack)
        if (not o.get('pendingDecision') and o.get('nativePaymentError') is None and own_stack and not state.get('combat')
            and raw.get('abilityId') not in {'Lathril, Blade of the Elves:2','Sterling Grove:1'}
            and (casting or a.get('kind')=='ActivateAbility' and a.get('isManaAbility') is not True)):
            restrictions.append({'action':a['semanticId'],'rule':'let-own-stack-resolve'});continue
        if (not o.get('pendingDecision') and o.get('nativePaymentError') is None and quiet and state.get('activePlayerId')==me
            and state.get('currentStep') in {'UNTAP','UPKEEP','DRAW'}
            and (casting or a.get('kind')=='ActivateAbility' and a.get('isManaAbility') is not True and raw.get('abilityId') not in {'Lathril, Blade of the Elves:2','Sterling Grove:1'})):
            restrictions.append({'action':a['semanticId'],'rule':'development-waits-for-main'});held_autopay |= raw.get('paymentStrategy',{}).get('type')=='AutoPay';continue
        if (not o.get('pendingDecision') and o.get('nativePaymentError') is None and quiet and not state.get('combat') and (casting or a.get('kind')=='ActivateAbility' and a.get('isManaAbility') is not True) and 'DRAW' in facts.get(name).get('tags',[])
            and not facts.get(name).get('affectsOpponent') and 'COUNTERSPELL' not in facts.get(name).get('tags',[])
            and not (state.get('activePlayerId')==me and state.get('currentStep') in {'PRECOMBAT_MAIN','POSTCOMBAT_MAIN'}
                     or state.get('activePlayerId')!=me and state.get('currentStep') in {'END','END_STEP'})):
            restrictions.append({'action':a['semanticId'],'rule':'draw-in-main-or-opponent-end-step'});held_autopay |= raw.get('paymentStrategy',{}).get('type')=='AutoPay';continue
        # Counters with a card cost or Treasure refund are saved for material threats.
        # Unknown spells and engines keep their native choices for the model.
        exile_cost=a.get('additionalCostInfo') or {}
        if (casting and 'COUNTERSPELL' in facts.get(name).get('tags',[]) and (exile_cost.get('costType')=='ExileFromHand' or name=="An Offer You Can't Refuse")
            and a.get('minTargets')==a.get('targetCount')==1 and a.get('targetRequirements') is None
            and isinstance(a.get('validTargets'),list) and name not in counter_target_exceptions):
            targets=[t for t in a['validTargets'] if cards.get(t,{}).get('name') not in ROUTINE_MANA_ROCKS | LOW_IMPACT_FILTERS]
            if targets!=a['validTargets']:
                restrictions.append({'action':a['semanticId'],'rule':'preserve-costly-counter-for-material-threat'})
                a['validTargets']=targets
                if not targets:continue
        if (casting and 'COUNTERSPELL' in facts.get(name).get('tags',[]) and answered
            and a.get('minTargets')==a.get('targetCount')==1 and a.get('targetRequirements') is None
            and isinstance(a.get('validTargets'),list)):
            targets=[t for t in a['validTargets'] if t not in answered]
            if targets!=a['validTargets']:
                restrictions.append({'action':a['semanticId'],'rule':'avoid-redundant-pending-counter'})
                a['validTargets']=targets
                if not targets:continue
        if (not o.get('pendingDecision') and o.get('nativePaymentError') is None and quiet and removal_reaction(name,facts,casting) and name not in reaction_timing_exceptions
            and (casting or a.get('kind')=='ActivateAbility' and a.get('isManaAbility') is not True)
            and not (state.get('activePlayerId')==me and state.get('currentStep') in {'PRECOMBAT_MAIN','POSTCOMBAT_MAIN'}
                or state.get('activePlayerId')!=me and state.get('currentStep') in {'END','END_STEP'}
                or state.get('currentStep') in {'DECLARE_ATTACKERS','DECLARE_BLOCKERS'} and (state.get('combat') or {}).get('attackers'))):
            restrictions.append({'action':a['semanticId'],'rule':'hold-reviewed-reaction-for-useful-window'})
            held_autopay |= raw.get('paymentStrategy',{}).get('type')=='AutoPay'
            continue
        harmful = (casting and facts.get(name).get('spellPolarity') == ['HARMFUL']) or name in OPPONENT_REMOVAL or name=='Otawara, Soaring City' and quiet
        if harmful and name not in allow_self_targets and a.get('requiresTargets') is True and a.get('targetRequirements') is None and isinstance(a.get('validTargets'),list):
            # Retain unknown ownership rather than inventing an enemy controller.
            targets = [t for t in a['validTargets'] if cards.get(t, {}).get('controllerId', t) != me or name in BOUNCE_SOURCES and own_bounce_rescue(o,t,facts)]
            if targets != a['validTargets']:
                restrictions.append({'action':a['semanticId'],'rule':'opponent-removal-targets'})
                a['validTargets'] = targets
            if len(targets) < a.get('minTargets',1): continue
        if (casting and (name in {'Flickering Ward','Curiosity','Keen Sense','Sixth Sense'} or facts.get(name).get('spellPolarity')==['BENEFICIAL'] and all(t in ids_in(state,'Battlefield') for t in a.get('validTargets') or [])) or raw.get('abilityId') == 'Timberwatch Elf:1') and a.get('targetRequirements') is None and isinstance(a.get('validTargets'),list):
            a['validTargets'] = [t for t in a['validTargets'] if cards.get(t,{}).get('controllerId') == me]
            restrictions.append({'action':a['semanticId'],'rule':'beneficial-effect-own-creatures'})
            if len(a['validTargets']) < a.get('minTargets',1): continue
        if ((harmful or name in NEUTRALIZING_AURAS or name=="Faith's Fetters") and name not in allow_self_targets
            and a.get('targetRequirements') is None and a.get('minTargets')==a.get('targetCount')==1):
            targets=a.get('validTargets') or []
            if name=="Faith's Fetters":
                targets=[t for t in targets if not ('LAND' in cards.get(t,{}).get('cardTypes',[]) and facts.get(cards.get(t,{}).get('name')).get('simpleMana'))]
                a['validTargets']=targets
                if not targets:restrictions.append({'action':a['semanticId'],'rule':'removal-without-useful-target'});continue
            if targets and all(t in ids_in(state,'Battlefield') and cards.get(t,{}).get('controllerId') not in (None,me) for t in targets):
                a['validTargets']=ranked_removal_targets(o,targets,facts,neutralize='NEUTRALIZE' in facts.get(name).get('tags',[]) or name in NEUTRALIZING_AURAS)
                if a['validTargets']!=targets:restrictions.append({'action':a['semanticId'],'rule':'reviewed-removal-threat-shortlist'})
        if name in {"Green Sun's Zenith",'Chord of Calling'} and a.get('hasXCost') and type(a.get('maxAffordableX')) is int:
            deck = o.get('knownDeck', {}).get('cards', {})
            eligible = []; green_payoffs = []
            # An exact public deck list can prove no eligible payoff. Missing
            # definitions prevent that proof and leave the choice to the model.
            complete = bool(deck) and all(facts.get(n) for n in deck)
            for n,count in deck.items():
                f = facts.get(n); cost = mana_cost(f.get('manaCost',''))
                if count and 'CREATURE' in f.get('types',[]) and cost and cost[0] <= a['maxAffordableX']:
                    eligible.append(cost[0])
                    if (name=='Chord of Calling' or cost[1].get('G',0)) and count > sum(1 for z in o.get('state',{}).get('zones',[]) if z.get('isVisible') and z.get('zoneId',{}).get('ownerId')==me for i in z.get('cardIds',[]) if cards.get(i,{}).get('name')==n): green_payoffs.append((n,cost[0]))
            if complete and not eligible:
                restrictions.append({'action':a['semanticId'],'rule':'search-without-eligible-payoff'}); continue
            if complete and eligible: a['minX'] = max(a.get('minX') or 0, min(eligible))
            if green_payoffs:
                preferred, x = sorted(green_payoffs,key=lambda item:(-tutor_payoff_score(item[0],facts,preferred_engines), item[1], item[0]))[0]
                plans.append({'action':a['semanticId'],'source':name,'goal':'Put a development creature onto the battlefield',
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
    if not o.get('pendingDecision') and (held_autopay or any(affordable(a) and a.get('isManaAbility') is not True and a.get('action',{}).get('paymentStrategy',{}).get('type') == 'AutoPay' for a in retained)):
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
                if len(filtered) >= requirement.get('minTargets',1):
                    ranked=sorted(filtered,key=lambda t:(-removal_target_value(o,t,facts),t))
                    pending['legalTargets'][slot]=[t for t in ranked if removal_target_value(o,t,facts)>=removal_target_value(o,ranked[0],facts)-3][:3] if ranked else []
        restrictions.append({'rule':'reviewed-etb-removal-opponent-targets'})
    if isinstance(pending,dict) and reviewed_exile_decision(pending) and pending['context']['sourceName'] not in allow_self_targets:
        slot=str(pending['targetRequirements'][0]['index']);targets=pending['legalTargets'].get(slot)
        if isinstance(targets,list) and targets and all(t in ids_in(state,'Battlefield') and cards.get(t,{}).get('controllerId') not in (None,me) for t in targets):
            pending['legalTargets'][slot]=ranked_removal_targets(o,targets,facts)
            restrictions.append({'rule':'reviewed-etb-exile-threat-shortlist'})
    reduction=fixed_stat_reduction(pending,facts) if isinstance(pending,dict) else None
    if reduction and pending['context']['sourceName'] not in allow_self_targets:
        slot=str(pending['targetRequirements'][0]['index']);targets=pending['legalTargets'].get(slot)
        if isinstance(targets,list) and targets and all(t in ids_in(state,'Battlefield') and cards.get(t,{}).get('controllerId') not in (None,me) and 'CREATURE' in cards[t].get('cardTypes',[]) for t in targets):
            # Prefer actual kills, then existing removal valuation (which values
            # printed abilities only while they are active). Unknown stats defer.
            if all(type(cards[t].get('toughness')) is int and not cards[t].get('activeEffects') for t in targets):
                kills=[t for t in targets if cards[t]['toughness']+reduction['toughness']<=0]
                if kills:
                    pending['legalTargets'][slot]=ranked_removal_targets(o,kills,facts)
                    restrictions.append({'rule':'fixed-stat-removal-live-threat-shortlist'})
    copy_targets=copied_neutralization_targets(o,pending,facts) if isinstance(pending,dict) and pending.get('context',{}).get('sourceName') not in allow_self_targets else None
    if copy_targets:
        slot,targets=copy_targets
        pending['legalTargets'][slot]=targets
        # An empty selection keeps the original target in this native contract.
        # Require one explicit alternative, each still offered by native legality.
        pending['targetRequirements'][0]['minTargets']=1
        restrictions.append({'rule':'spread-neutralization-copies-across-live-threats'})
    view['legalActions']=retained
    view['policyUtilityPlans']=plans
    return view,restrictions
