"""Reviewed competency restrictions on a copy of the current native menu.

These are policy bounds, not new legality claims. Unknown mechanics retain their
native offer. Never mutate the recorded observation or manufacture an action.
"""
from copy import deepcopy
from .heuristic_policy import affordable, ids_in, mana_cost, plain_token_body, draw_neutral_modifiers

# Ordinary bounce can rescue a disabled commander or a visible hostile target.
# Other self-target plans require a deck override; complex slots stay native.
BOUNCE_SOURCES = frozenset({'Boomerang','Into the Roil','Unsummon','Echoing Truth','Blink of an Eye','Otawara, Soaring City'})
ETB_EXILERS = frozenset({'Sheltered by Ghosts','Oblivion Ring','Banishing Light','Detention Sphere','Ossification','Stasis Snare'})
OPPONENT_REMOVAL = frozenset({'Reality Shift','Seal of Primordium','Seal of Cleansing',*BOUNCE_SOURCES})
QUIET_REACTIONS = frozenset({*OPPONENT_REMOVAL,*ETB_EXILERS,'Swords to Plowshares','Generous Gift','Pongify','Rapid Hybridization','Resculpt','Beast Within',"Nature's Claim",'Arrest','Pacifism','Darksteel Mutation','Imprisoned in the Moon','Lignify','Amphibian Downpour'})


NEUTRALIZING_AURAS=frozenset({'Arrest','Pacifism','Darksteel Mutation','Imprisoned in the Moon','Lignify','Noggle the Mind','Amphibian Downpour','Witness Protection',"Faith's Fetters"})


ABILITY_REMOVING_AURAS=frozenset({'Darksteel Mutation','Imprisoned in the Moon','Lignify','Noggle the Mind','Amphibian Downpour'})


def draw_or_library_reorder_only_stack(o,facts):
    state=o.get('state',{});cards=state.get('cards',{});stack=ids_in(state,'Stack')
    if not stack:return False
    for zone in state.get('zones',[]):
        if zone.get('zoneId',{}).get('zoneType')=='Stack' and (
            zone.get('isVisible') is not True or zone.get('size',len(zone.get('cardIds',[])))!=len(zone.get('cardIds',[]))):return False
    return all(not cards.get(i,{}).get('activeEffects') and isinstance(cards.get(i,{}).get('abilityIdentity'),dict)
               and (facts.is_library_reorder_ability(cards[i]['abilityIdentity'].get('abilityId'))
                    or facts.is_free_optional_draw_ability(cards[i]['abilityIdentity'].get('abilityId'))) for i in stack)


def ordinary_cycling_window(o,facts,action):
    state=o['state'];me=o['agentToAct'];raw=action.get('action',{});cards=state.get('cards',{})
    source=cards.get(raw.get('cardId'),{})
    if (raw.get('cardId') not in ids_in(state,'Hand',me) or action.get('hasXCost') is not False
        or raw.get('grantedBy') is not None or action.get('requiresTargets') is not False
        or action.get('additionalCostInfo') or not draw_neutral_modifiers(source) or source.get('isFaceDown') is True
        or facts.get(source.get('name')).get('cyclingSensitive') is not False):return False
    if any(p.get('activeEffects') or type(p.get('librarySize')) is not int or p['librarySize']<=3 for p in state.get('players',[])):return False
    player=next((p for p in state.get('players',[]) if p.get('playerId')==me),{})
    pool=player.get('manaPool')
    if (not isinstance(pool,dict) or pool.get('restrictedMana')
        or any(type(pool.get(c)) is not int or pool[c]!=0 for c in ['white','blue','black','red','green','colorless'])):return False
    battlefield_owners={z.get('zoneId',{}).get('ownerId') for z in state.get('zones',[]) if z.get('zoneId',{}).get('zoneType')=='Battlefield'}
    if not {p.get('playerId') for p in state.get('players',[])}<=battlefield_owners:return False
    if not any(z.get('zoneId',{}).get('zoneType')=='Stack' for z in state.get('zones',[])):return False
    for zone in state.get('zones',[]):
        if zone.get('zoneId',{}).get('zoneType') in {'Stack','Battlefield'} and (
            zone.get('isVisible') is not True or zone.get('size',len(zone.get('cardIds',[])))!=len(zone.get('cardIds',[]))):return False
    for i in ids_in(state,'Battlefield'):
        card=cards.get(i,{});fact=facts.get(card.get('name'))
        if not draw_neutral_modifiers(card) or card.get('isFaceDown') is True or (not fact and not plain_token_body({**card,'activeEffects':[]})):return False
        if fact and (fact.get('cyclingSensitive') is not False or fact.get('drawSensitive')):return False
    stack=ids_in(state,'Stack')
    return not stack or draw_or_library_reorder_only_stack(o,facts)


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


def positive_counter_decision(p,facts):
    if not isinstance(p,dict):return None
    context=p.get('context') or {};rows=p.get('targetRequirements')
    hint=context.get('effectHint')
    normalize=lambda s:s.strip().removesuffix('.').casefold() if isinstance(s,str) else None
    matches=[count for text,count in facts.get(context.get('sourceName')).get('positiveCounterTriggers',{}).items()
             if normalize(text)==normalize(hint)]
    constraints=['sameOwner','sameNumericProperty','selectionGroups','exclusiveGroups','distinctCategories','totalCap','differentNames','differentControllers','mustDifferFromEarlier']
    if (len(matches)==1 and p.get('responseSpec',{}).get('responseType') in {'TargetsResponse','TargetsChosenResponse'}
        and isinstance(rows,list) and len(rows)==1 and rows[0].get('minTargets')==rows[0].get('maxTargets')==1
        and not any(rows[0].get(k) for k in constraints) and isinstance(p.get('legalTargets'),dict)):
        return str(rows[0]['index'])
    return None


def combat_lock_effects(contract):
    return {'preventsAttackingAndBlocking':True,'preventsActivatedAbilities':contract['activatedAbilities'],
            'keepsTriggeredAndStaticAbilities':True}


def ranked_combat_lock_targets(o,targets,facts,contract):
    """Score functions this exact Aura stops; a trigger engine keeps its triggers."""
    state=o['state'];cards=state.get('cards',{})
    if (o.get('nativePaymentError') is not None or state.get('activePlayerId')!=o['agentToAct']
        or state.get('currentStep') not in {'PRECOMBAT_MAIN','POSTCOMBAT_MAIN'}
        or state.get('combat') or ids_in(state,'Stack')):return targets
    if any(type(cards.get(t,{}).get(k)) is not int for t in targets for k in ['power','toughness']):return targets
    if any(cards[t].get('activeEffects') or 'activatedAbilityCounts' not in facts.get(cards[t].get('name')) for t in targets):return targets
    def score(t):
        c=cards[t];f=facts.get(c.get('name'));attached=[facts.get(cards.get(i,{}).get('name')) for i in c.get('attachments',[])]
        combat_disabled=any(a.get('attachedCombatLock') for a in attached)
        power=max(0,c['power']);body=power+max(0,c['toughness'])
        value=0 if combat_disabled else body+(2*power if c.get('isCommander') else 0)
        if abilities_removed(o,c,facts):return value
        blocked={a['attachedCombatLock']['activatedAbilities'] for a in attached if a.get('attachedCombatLock')}
        counts=f['activatedAbilityCounts'];scope=contract['activatedAbilities']
        if scope in {'all','nonMana'} and counts['nonMana'] and not blocked & {'all','nonMana'}:
            value+=max(8,f.get('staticValue',0))
        if scope=='all' and counts['mana'] and 'all' not in blocked:
            value+=15 if f.get('variableMana') else 4*max(1,f.get('developmentMana',1))
        return value
    ordered=sorted(targets,key=lambda t:(-score(t),t))
    if not ordered or score(ordered[0])<=0:return targets
    return [t for t in ordered if score(t)>=score(ordered[0])-3][:3]


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


def bounce_commander_rescue(o, candidates, facts):
    cards=o['state'].get('cards',{});me=o['agentToAct']
    for target in candidates:
        card=cards.get(target,{})
        if card.get('controllerId')!=me or not card.get('isCommander'):continue
        if abilities_removed(o,card,facts) or any(facts.get(cards.get(a,{}).get('name')).get('attachedCombatLock') for a in card.get('attachments',[])):
            return True
    return False


def self_return_is_already_on_top(o,raw):
    # Legacy native ability identities name definitions rather than instances.
    # Apply the basic single-copy policy only to an unmodified unique owned
    # permanent. Copies and intervening responses retain inference. A rare
    # return/recast while an older incarnation's ability remains is a strategic
    # exception; deck reaction_timing_exceptions can opt out.
    state=o['state'];cards=state.get('cards',{});me=o['agentToAct']
    source=cards.get(raw.get('sourceId'),{});name=source.get('name')
    if not name or source.get('controllerId')!=me or source.get('activeEffects') or source.get('copyOf') or source.get('isFaceDown'):return False
    sources=[i for i in ids_in(state,'Battlefield') if cards.get(i,{}).get('controllerId')==me and cards.get(i,{}).get('name')==name]
    stack=ids_in(state,'Stack')
    if sources!=[raw.get('sourceId')] or not stack:return False
    top=cards.get(stack[-1],{});identity=top.get('abilityIdentity') or {};definition=identity.get('cardDefinitionId')
    return (top.get('controllerId')==me and not top.get('activeEffects')
            and isinstance(definition,str) and definition.split('#',1)[0]==name
            and identity.get('abilityId')==raw.get('abilityId'))


def evasion_removal_has_no_subject(o,facts):
    """Only certify current no-op utility when public state rules out new evasion."""
    state=o['state'];cards=state.get('cards',{});me=o['agentToAct']
    if any(p.get('activeEffects') for p in state.get('players',[])):return False
    for entity in ids_in(state,'Battlefield'):
        card=cards.get(entity,{})
        if card.get('activeEffects'):return False
        sensitivity=facts.get(card.get('name')).get('evasionSensitive')
        if sensitivity is not False and not (sensitivity is None and plain_token_body(card)):return False
        if 'CREATURE' not in card.get('cardTypes',[]) or card.get('controllerId')==me:continue
        if card.get('controllerId') is None or not isinstance(card.get('keywords'),list):return False
        if not isinstance(card.get('hexproofFromColors'),list):return False
        if any(type(card.get(k)) is not bool for k in ['hexproofFromMonocolored','hexproofFromMulticolored']):return False
        if ({'HEXPROOF','SHROUD'} & set(card['keywords']) or card['hexproofFromColors']
            or card['hexproofFromMonocolored'] or card['hexproofFromMulticolored']):return False
    for entity in ids_in(state,'Stack'):
        card=cards.get(entity,{});identity=card.get('abilityIdentity') or {};definition=identity.get('cardDefinitionId')
        if card.get('activeEffects') or not isinstance(definition,str):return False
        contract=facts.get(definition.split('#',1)[0]);ability=identity.get('abilityId')
        harmless=set(contract.get('bounceTargetUntapAbilities',[])+contract.get('simpleTargetUntapAbilities',[])+contract.get('manaOnlySelfUntapAbilities',[])) | set(contract.get('simpleLifeGainTriggers',{}))
        if ability not in harmless:return False
    return True


def tutor_payoff_score(name, facts, preferred_engines=()):
    """Shared payoff ordering usable both before sacrifice and at native search."""
    fact = facts.get(name); tags = fact.get('tags',[])
    cost = mana_cost(fact.get('manaCost',''))
    return (400 if name in preferred_engines else 200 if 'DRAW' in tags and fact.get('repeatable') else
            100 if fact.get('repeatable') else 50 if 'RAMP' in tags else 20) + fact.get('staticValue',0) - (cost[0] if cost else 0)


# Native definitions reviewed: these counters have no optional mana payment.
UNCONDITIONAL_COUNTERS = frozenset({'Counterspell','Negate','Dissolve','Swan Song','Arcane Denial'})

def own_stack_can_resolve(o,facts):
    """An owned stack can resolve past unrelated fixed life gain.

    Opposing spells, unknown abilities and targeted responses retain inference.
    No claim is made about their strategic value or later triggered abilities:
    each subsequent native priority window is evaluated again.
    """
    state=o.get('state',{});cards=state.get('cards',{});me=o['agentToAct'];stack=ids_in(state,'Stack')
    if not any(cards.get(i,{}).get('controllerId')==me for i in stack):return False
    for i in stack:
        c=cards.get(i,{})
        if c.get('controllerId')==me:continue
        identity=c.get('abilityIdentity') or {};definition=identity.get('cardDefinitionId')
        if not isinstance(definition,str) or c.get('controllerId') is None:return False
        contract=facts.get(definition.split('#',1)[0]).get('simpleLifeGainTriggers',{})
        if (identity.get('abilityId') not in contract or c.get('targets') or c.get('activeEffects')
            or c.get('perModeTargets')):return False
    return True


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


# These reviewed Auras disable exactly the enchanted permanent. A deck may
# explicitly approve a political counter with counter_target_exceptions.
SINGLE_TARGET_DISABLE_AURAS=frozenset({'Pacifism','Arrest','Darksteel Mutation',
    'Imprisoned in the Moon','Lignify','Noggle the Mind'})


def disables_only_opposing_permanent(o, spell_id):
    state=o.get('state',{});cards=state.get('cards',{});me=o['agentToAct']
    spell=cards.get(spell_id,{})
    if (spell_id not in ids_in(state,'Stack') or spell.get('name') not in SINGLE_TARGET_DISABLE_AURAS
            or spell.get('controllerId') in (None,me) or spell.get('perModeTargets')): return False
    targets=spell.get('targets')
    if not isinstance(targets,list) or len(targets)!=1 or not isinstance(targets[0],dict) or targets[0].get('type')!='Permanent': return False
    target=targets[0].get('entityId');controller=cards.get(target,{}).get('controllerId')
    return target in ids_in(state,'Battlefield') and controller not in (None,me)


def live_combat_draw_decision(o):
    """Keep inference when drawing might change our blocks or save a blocked attacker."""
    state=o.get('state',{});combat=state.get('combat');me=o['agentToAct']
    if state.get('currentStep')=='END_COMBAT' or combat is None:return False
    if any(p.get('teamSharedLife') or p.get('teamSharedTurns') for p in state.get('players',[])):return True
    if not isinstance(combat,dict) or not isinstance(combat.get('attackers'),list):return True
    for attacker in combat['attackers']:
        if not isinstance(attacker,dict):return True
        target=attacker.get('attackingTarget')
        if not isinstance(target,dict) or target.get('type')!='Player':return True
        if target.get('playerId') is None or target['playerId']==me:return True
        if combat.get('attackingPlayerId')==me:
            if attacker.get('isBlocked') is not False or attacker.get('blockedBy'):return True
        elif combat.get('attackingPlayerId') is None:return True
    return False


def simple_permanent_stack(o,facts):
    cards=o['state'].get('cards',{});stack=ids_in(o['state'],'Stack')
    return bool(stack) and all(
        not cards.get(i,{}).get('activeEffects') and not cards.get(i,{}).get('abilityIdentity') and cards.get(i,{}).get('isFaceDown') is False
        and not cards.get(i,{}).get('targets') and facts.get(cards.get(i,{}).get('name')).get('plainPermanentCast') is True
        and bool(set(facts.get(cards.get(i,{}).get('name')).get('types',[])) & {'CREATURE','ARTIFACT','ENCHANTMENT','PLANESWALKER','BATTLE'})
        and not set(facts.get(cards.get(i,{}).get('name')).get('types',[])) & {'INSTANT','SORCERY'}
        for i in stack)


def simple_life_gain_stack(o,facts):
    cards=o['state'].get('cards',{});stack=ids_in(o['state'],'Stack')
    if not stack:return False
    for i in stack:
        c=cards.get(i,{});identity=c.get('abilityIdentity') or {};definition=identity.get('cardDefinitionId')
        if c.get('activeEffects') or c.get('targets') or not isinstance(definition,str):return False
        if identity.get('abilityId') not in facts.get(definition.split('#',1)[0]).get('simpleLifeGainTriggers',{}):return False
    return True


def pump_has_power_mana_payoff(o,entity,facts):
    c=o['state'].get('cards',{}).get(entity,{});f=facts.get(c.get('name'))
    # Preserve unknown or modified targets for inference. A known ordinary body
    # has no mana benefit from a temporary power increase.
    if c.get('activeEffects'):return True
    if not f:return not plain_token_body(c)
    if 'powerManaAbilities' not in f:return True
    return any(a.get('kind')=='ActivateAbility' and a.get('isManaAbility') is True and affordable(a)
               and a.get('action',{}).get('sourceId')==entity
               and a.get('action',{}).get('abilityId') in f['powerManaAbilities'] for a in o.get('legalActions',[]))


def safe_ordinary_player_draw(o,source,facts,action=None):
    count=facts.get(source).get('beneficialPlayerDrawCount')
    if action is not None and action.get('hasXCost'):
        high=action.get('maxAffordableX');low=action.get('minX')
        if (facts.get(source).get('beneficialPlayerDrawX') is not True
            or type(high) is not int or not 1<=high<=100 or type(low) is not int or not 0<=low<=high
            or action.get('action',{}).get('xValue') is not None):return False
        count=high
    if not count:return False
    state=o['state']
    if any(type(p.get('librarySize')) is not int or p['librarySize']<=count+2 or p.get('activeEffects') for p in state.get('players',[])):return False
    for i in ids_in(state,'Battlefield'):
        c=state.get('cards',{}).get(i,{});f=facts.get(c.get('name'))
        if c.get('activeEffects') or (not f and not plain_token_body(c)) or f.get('drawSensitive'):return False
    return True


def stranded_land_mana_boost(o, name, facts, action):
    """Hold a reviewed land boost when paying for it exhausts the sole useful land.

    Other permanents, floating mana, land drops and unknown board details can create
    a plan, so those retain inference. This uses a native effect contract, not a deck name.
    """
    boost=facts.get(name).get('temporaryLandManaBoost');state=o['state'];me=o['agentToAct']
    if not boost or facts.get(name).get('manaCost')!='{'+boost['color']+'}':return False
    raw=action.get('action',{})
    if (not affordable(action) or action.get('manaCostString')!=facts.get(name).get('manaCost')
        or raw.get('useWithoutPayingManaCost') or raw.get('useAlternativeCost')
        or raw.get('alternativePayment') or action.get('additionalCostInfo')):return False
    own=ids_in(state,'Battlefield',me)
    if len(own)!=1 or any(a.get('kind')=='PlayLand' for a in o.get('legalActions',[])):return False
    land=state.get('cards',{}).get(own[0],{})
    if (land.get('isTapped') is not False or land.get('subtypes')!=[boost['subtype']]
        or land.get('cardTypes')!=['LAND'] or land.get('attachments') or land.get('activeEffects')
        or land.get('abilityFlags') or not facts.get(land.get('name')).get('simpleMana')):return False
    player=next((p for p in state.get('players',[]) if p.get('playerId')==me),{})
    pool=player.get('manaPool')
    return (isinstance(pool,dict) and all(pool.get(c)==0 for c in ['white','blue','black','red','green','colorless'])
        and not pool.get('restrictedMana') and not player.get('activeEffects'))


def protection_transfer_targets(o, action, facts, preferred_engines):
    """Keep protection on an engine over a plain token with no haste payoff.

    Restrict only a certified pure protection/haste equipment in a quiet own
    main phase. Unknown or modified creatures, useful haste transfers, richer
    equipment and tactical response windows retain model discretion.
    """
    state=o.get('state',{});cards=state.get('cards',{});me=o['agentToAct']
    source=action.get('action',{}).get('sourceId');equipment=cards.get(source,{})
    if (not facts.get(equipment.get('name')).get('protectionEquipmentKeywords')
        or state.get('activePlayerId')!=me or state.get('currentStep')!='PRECOMBAT_MAIN'
        or ids_in(state,'Stack') or equipment.get('controllerId')!=me or equipment.get('activeEffects')
        or equipment.get('attachments') or equipment.get('copyOf') is not None):return None
    owned=ids_in(state,'Battlefield',me)
    hosts=[i for i in owned if source in (cards.get(i,{}).get('attachments') or [])]
    if len(hosts)!=1:return None
    host=cards[hosts[0]]
    if (host.get('controllerId')!=me or 'CREATURE' not in host.get('cardTypes',[]) or abilities_removed(o,host,facts)
        or host.get('activeEffects') or host.get('hasSummoningSickness') is not False or host.get('isTapped') is not False
        or any(type(host.get(k)) is not int for k in ['power','toughness'])
        or not (host.get('isCommander') is True or host.get('name') in preferred_engines)):return None
    def dominated(target):
        c=cards.get(target,{})
        return (target in owned and c.get('controllerId')==me and plain_token_body(c)
                and c.get('hasSummoningSickness') is False and not c.get('attachments')
                and not c.get('counters') and c.get('isCommander') is not True
                and all(type(c.get(k)) is int and 0<=c[k]<=host[k] for k in ['power','toughness']))
    return [t for t in action['validTargets'] if not dominated(t)]


def remember_equipment_hosts(owner, o, facts):
    """Remember observed attachments, never assume a returned action succeeded.

    Only the current phase is retained per seat; this is bounded harness state,
    not model conversation. A new phase, opening, or source leaving play resets
    its history. Losing a cache entry simply retains model discretion.
    """
    state=o.get('state',{});actor=o['agentToAct']
    players=tuple(p.get('playerId') for p in state.get('players',[]))
    turn=state.get('turnNumber');step=state.get('currentStep')
    cache=getattr(owner,'_equipment_phase_history',{})
    namespace=(actor,players)
    if type(turn) is not int or not isinstance(step,str) or 'mulligan' in state:
        cache.pop(namespace,None);owner._equipment_phase_history=cache
        return {}
    key=(turn,step)
    previous=cache.get(namespace,{})
    hosts=previous.get('hosts',{}) if previous.get('key')==key else {}
    cards=state.get('cards',{});owned=ids_in(state,'Battlefield',actor)
    observed={};tap_states={}
    previous_taps=previous.get('host_taps',{}) if previous.get('key')==key else {}
    for source in owned:
        if not facts.get(cards.get(source,{}).get('name')).get('protectionEquipmentKeywords'):continue
        current=[i for i in owned if source in (cards.get(i,{}).get('attachments') or [])]
        if len(current)!=1:continue
        visited=hosts.get(source,[])
        old_taps=previous_taps.get(source,{})
        # A real tap/untap can justify a new protection plan. Equipment moves
        # alone change haste/sickness and attachments, but do not tap a host.
        if any(t in old_taps and type(cards.get(t,{}).get('isTapped')) is bool
               and old_taps[t]!=cards[t]['isTapped'] for t in visited if t in owned):visited=[]
        observed[source]=list(dict.fromkeys([*visited,current[0]]))
        tap_states[source]={t:cards[t]['isTapped'] for t in observed[source]
                            if t in owned and type(cards[t].get('isTapped')) is bool}
    cache.pop(namespace,None)
    cache[namespace]={'key':key,'hosts':observed,'host_taps':tap_states}
    if len(cache)>64:cache.pop(next(iter(cache)))
    owner._equipment_phase_history=cache
    return {'actor':actor,'players':list(players),'turn':turn,'step':step,'hosts':deepcopy(observed),'host_taps':deepcopy(tap_states)}


def repeated_protection_targets(o, action, facts, history):
    state=o.get('state',{});me=o['agentToAct'];cards=state.get('cards',{});source=action.get('action',{}).get('sourceId')
    if (not isinstance(history,dict) or history.get('actor')!=me or history.get('turn')!=state.get('turnNumber')
        or history.get('step')!=state.get('currentStep') or history.get('players')!=[p.get('playerId') for p in state.get('players',[])]
        or state.get('activePlayerId')!=me or state.get('currentStep') not in {'PRECOMBAT_MAIN','POSTCOMBAT_MAIN'} or ids_in(state,'Stack')
        or not facts.get(cards.get(source,{}).get('name')).get('protectionEquipmentKeywords')
        or cards.get(source,{}).get('activeEffects')):return None
    owned=ids_in(state,'Battlefield',me)
    hosts=[i for i in owned if source in (cards.get(i,{}).get('attachments') or [])]
    if len(hosts)!=1:return None
    current=cards[hosts[0]]
    taps=(history.get('host_taps') or {}).get(source) if isinstance(history.get('host_taps',{}),dict) else None
    if (current.get('controllerId')!=me or type(current.get('isTapped')) is not bool
        or current.get('hasSummoningSickness') is not False or current.get('activeEffects')):return None
    if taps is None:
        if current['isTapped']:return None
    elif (not isinstance(taps,dict) or any(not isinstance(t,str) or type(v) is not bool for t,v in taps.items())
          or any(t in owned and type(cards.get(t,{}).get('isTapped')) is bool and v!=cards[t]['isTapped'] for t,v in taps.items())):return None
    mapping=history.get('hosts')
    if not isinstance(mapping,dict):return None
    visited=mapping.get(source,[])
    if not isinstance(visited,list) or any(not isinstance(i,str) for i in visited):return None
    def repeated(target):
        c=cards.get(target,{})
        return (target in visited and target!=hosts[0] and target in owned and c.get('controllerId')==me
                and 'CREATURE' in c.get('cardTypes',[]) and type(c.get('hasSummoningSickness')) is bool
                and (type(c.get('isTapped')) is bool if taps is not None else c.get('isTapped') is False) and not c.get('activeEffects'))
    return [t for t in action['validTargets'] if not repeated(t)]


def bounded_observation(o, facts, *, allow_self_targets=(), preferred_engines=(), reaction_timing_exceptions=(), counter_target_exceptions=(), equipment_history=None):
    view = deepcopy(o); cards = view.get('state', {}).get('cards', {}); me = view['agentToAct']
    restrictions = []; retained = []; plans = []
    answered=pending_counter_answers(o,facts);held_autopay=False;state=o.get('state',{});own_stack=own_stack_can_resolve(o,facts)
    can_pass=any(a.get('kind')=='PassPriority' and affordable(a) for a in o.get('legalActions',[]))
    for a in view.get('legalActions', []):
        raw = a.get('action', {}); name = cards.get(raw.get('cardId', raw.get('sourceId')), {}).get('name')
        state = o.get('state',{})
        quiet=not ids_in(state,'Stack')
        if (a.get('kind')=='CastSpell' and a.get('hasXCost') is True and can_pass and quiet
            and not o.get('pendingDecision') and o.get('nativePaymentError') is None
            and state.get('activePlayerId')==me and state.get('currentStep') in {'PRECOMBAT_MAIN','POSTCOMBAT_MAIN'}
            and facts.get(name).get('emptySelfDrawAtZeroX') is True and name not in reaction_timing_exceptions
            and raw.get('xValue') is None and a.get('parameterSpec',{}).get('allowedFields',{}).get('xValue')=='INTEGER'
            and not cards.get(raw.get('cardId'),{}).get('activeEffects') and not raw.get('alternativePayment')
            and type(a.get('minX')) is int and type(a.get('maxAffordableX')) is int):
            if a['maxAffordableX']<1:
                restrictions.append({'action':a['semanticId'],'rule':'hold-zero-X-empty-self-draw'});continue
            if a['minX']<1:
                a['minX']=1
                restrictions.append({'action':a['semanticId'],'rule':'self-draw-X-needs-positive-base-benefit'})
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
        if (a.get('kind')=='ActivateAbility' and can_pass and not o.get('pendingDecision') and o.get('nativePaymentError') is None
            and name not in reaction_timing_exceptions and not cards.get(raw.get('sourceId'),{}).get('activeEffects')):
            if (raw.get('abilityId') in facts.get(name).get('selfReturnToHandAbilities',[])
                and self_return_is_already_on_top(o,raw)):
                restrictions.append({'action':a['semanticId'],'rule':'self-return-already-on-stack'})
                held_autopay |= raw.get('paymentStrategy',{}).get('type')=='AutoPay';continue
            if (raw.get('abilityId') in facts.get(name).get('opponentAttackRequirementAbilities',[])
                and state.get('activePlayerId')==me
                and not any(p.get('teamSharedTurns') or p.get('teamSharedLife') for p in state.get('players',[]))):
                restrictions.append({'action':a['semanticId'],'rule':'opponent-attack-requirement-needs-opponent-turn'})
                held_autopay |= raw.get('paymentStrategy',{}).get('type')=='AutoPay';continue
            if (raw.get('abilityId') in facts.get(name).get('opponentEvasionRemovalAbilities',[])
                and evasion_removal_has_no_subject(o,facts)):
                restrictions.append({'action':a['semanticId'],'rule':'evasion-removal-needs-protected-subject'})
                held_autopay |= raw.get('paymentStrategy',{}).get('type')=='AutoPay';continue
        casting=a.get('kind','').startswith('Cast')
        if (not o.get('pendingDecision') and o.get('nativePaymentError') is None and can_pass
            and a.get('kind')=='ActivateAbility' and raw.get('abilityId') in facts.get(name).get('equipAbilities',[])
            and name not in reaction_timing_exceptions and a.get('requiresTargets') is True
            and a.get('minTargets')==a.get('targetCount')==1 and a.get('targetRequirements') is None
            and isinstance(a.get('validTargets'),list)):
            targets=[t for t in a['validTargets'] if raw.get('sourceId') not in (cards.get(t,{}).get('attachments') or [])]
            if targets!=a['validTargets']:
                restrictions.append({'action':a['semanticId'],'rule':'avoid-equipping-current-host'})
                a['validTargets']=targets
                if not targets:
                    held_autopay |= raw.get('paymentStrategy',{}).get('type')=='AutoPay';continue
            if name not in allow_self_targets:
                targets=repeated_protection_targets(o,a,facts,equipment_history)
                if targets is not None and targets!=a['validTargets']:
                    restrictions.append({'action':a['semanticId'],'rule':'avoid-repeating-protection-host-in-same-phase'})
                    a['validTargets']=targets
                    if not targets:
                        held_autopay |= raw.get('paymentStrategy',{}).get('type')=='AutoPay';continue
                targets=protection_transfer_targets(o,a,facts,preferred_engines)
                if targets is not None and targets!=a['validTargets']:
                    restrictions.append({'action':a['semanticId'],'rule':'preserve-engine-protection-over-non-sick-token'})
                    a['validTargets']=targets
                    if not targets:
                        held_autopay |= raw.get('paymentStrategy',{}).get('type')=='AutoPay';continue
        if (casting and can_pass and (quiet or simple_permanent_stack(o,facts)) and not o.get('pendingDecision') and o.get('nativePaymentError') is None
            and name not in reaction_timing_exceptions and stranded_land_mana_boost(o,name,facts,a)):
            restrictions.append({'action':a['semanticId'],'rule':'hold-land-mana-boost-without-remaining-source'})
            held_autopay |= raw.get('paymentStrategy',{}).get('type')=='AutoPay';continue
        if (a.get('kind')=='ActivateAbility' and can_pass and not o.get('pendingDecision') and o.get('nativePaymentError') is None
            and name not in reaction_timing_exceptions and name not in allow_self_targets
            and raw.get('abilityId') in facts.get(name).get('positiveStatBoostAbilities',[])
            and not cards.get(raw.get('sourceId'),{}).get('activeEffects') and not a.get('additionalCostInfo')
            and a.get('requiresTargets') is True and a.get('targetRequirements') is None
            and a.get('minTargets')==a.get('targetCount')==1 and isinstance(a.get('validTargets'),list)
            and state.get('currentStep') in {'UNTAP','UPKEEP','DRAW','PRECOMBAT_MAIN','POSTCOMBAT_MAIN','END','END_STEP','CLEANUP'}
            and (quiet or simple_life_gain_stack(o,facts))):
            useful=[t for t in a['validTargets'] if pump_has_power_mana_payoff(o,t,facts)]
            if useful!=a['validTargets']:
                restrictions.append({'action':a['semanticId'],'rule':'temporary-pump-waits-for-combat-or-power-mana'})
                a['validTargets']=useful
            if not useful:
                held_autopay |= raw.get('paymentStrategy',{}).get('type')=='AutoPay';continue
        if a.get('kind')=='CastSpellModal' and name not in allow_self_targets:
            from .modal_policies import modal_plan,value_modal_contract
            plan=modal_plan(o,a,facts)
            if plan:view.setdefault('policyModalPlans',[]).append(plan)
            elif value_modal_contract(o,a) is not None:
                restrictions.append({'action':a['semanticId'],'rule':'hold-reviewed-modal-without-useful-complete-plan'});continue
        simple_untap=(a.get('kind')=='ActivateAbility' and raw.get('abilityId') in facts.get(name).get('simpleTargetUntapAbilities',[])
            and a.get('requiresTargets') is True and a.get('minTargets')==a.get('targetCount')==1
            and isinstance(a.get('validTargets'),list)
            and a.get('targetRequirements') is None and not a.get('additionalCostInfo')
            and a.get('parameterSpec',{}).get('allowedFields',{}).get('targets')=='ENTITY_ID_ARRAY')
        combat_step=state.get('currentStep') in {'BEGIN_COMBAT','DECLARE_ATTACKERS','DECLARE_BLOCKERS','FIRST_STRIKE_COMBAT_DAMAGE','COMBAT_DAMAGE','END_COMBAT'}
        if (not o.get('pendingDecision') and o.get('nativePaymentError') is None and quiet and combat_step
            and can_pass and not cards.get(raw.get('sourceId',raw.get('cardId')),{}).get('activeEffects')
            and (a.get('kind')=='ActivateAbility' and raw.get('abilityId') in facts.get(name).get('selfDrawAbilities',[])
                 or a.get('kind')=='CastSpell' and facts.get(name).get('selfResourceDrawSpell') is True
                 and a.get('hasXCost') is False and not a.get('additionalCostInfo')
                 and not raw.get('targets') and not raw.get('alternativePayment') and not raw.get('optionalCostPayments'))
            and a.get('requiresTargets') is False and name not in reaction_timing_exceptions
            and not live_combat_draw_decision(o)):
            restrictions.append({'action':a['semanticId'],'rule':'draw-waits-for-development-or-relevant-combat'})
            held_autopay |= raw.get('paymentStrategy',{}).get('type')=='AutoPay';continue
        if (a.get('kind')=='CycleCard' and not o.get('pendingDecision') and o.get('nativePaymentError') is None
            and can_pass and name not in reaction_timing_exceptions
            and state.get('currentStep') in {'UNTAP','UPKEEP','DRAW','PRECOMBAT_MAIN','POSTCOMBAT_MAIN','BEGIN_COMBAT','DECLARE_ATTACKERS','DECLARE_BLOCKERS','FIRST_STRIKE_COMBAT_DAMAGE','COMBAT_DAMAGE','END_COMBAT','END','END_STEP','CLEANUP'}
            and state.get('activePlayerId') in {p.get('playerId') for p in state.get('players',[]) if p.get('playerId')}
            and ('combat' in state and state['combat'] is None or isinstance(state.get('combat'),dict) and isinstance(state['combat'].get('attackers'),list))
            and not (state.get('activePlayerId')==me and state.get('currentStep') in {'PRECOMBAT_MAIN','POSTCOMBAT_MAIN'}
                     or state.get('activePlayerId')!=me and state.get('currentStep') in {'END','END_STEP'})
            and not live_combat_draw_decision(o) and ordinary_cycling_window(o,facts,a)):
            restrictions.append({'action':a['semanticId'],'rule':'cycle-in-main-or-opponent-end-step'})
            held_autopay |= raw.get('paymentStrategy',{}).get('type')=='AutoPay';continue
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
        combat=state.get('combat')
        no_live_combat=('combat' in state and combat is None or isinstance(combat,dict)
                        and isinstance(combat.get('attackers'),list) and not combat['attackers'])
        known_combat=('combat' in state and combat is None or isinstance(combat,dict) and isinstance(combat.get('attackers'),list))
        # Basic tapped-land development has no immediate mana or combat effect.
        # Keep creature-sacrifice combat tricks and explicit landfall/reaction plans.
        ramp_window=known_combat and (no_live_combat or isinstance(source.get('cardTypes'),list) and 'CREATURE' not in source['cardTypes'])
        if (not o.get('pendingDecision') and o.get('nativePaymentError') is None and can_pass and ramp_window
            and state.get('currentStep') in {'UNTAP','UPKEEP','DRAW','PRECOMBAT_MAIN','POSTCOMBAT_MAIN','BEGIN_COMBAT','DECLARE_ATTACKERS','DECLARE_BLOCKERS','FIRST_STRIKE_COMBAT_DAMAGE','COMBAT_DAMAGE','END_COMBAT','END','END_STEP','CLEANUP'}
            and state.get('activePlayerId') in {p.get('playerId') for p in state.get('players',[]) if p.get('playerId')}
            and a.get('kind')=='ActivateAbility' and a.get('isManaAbility') is not True
            and raw.get('abilityId') in facts.get(name).get('selfTappedLandRampAbilities',[])
            and not source.get('activeEffects') and name not in reaction_timing_exceptions
            and (quiet and all(z.get('isVisible') is True and z.get('size',len(z.get('cardIds',[])))==0
                               for z in state.get('zones',[]) if z.get('zoneId',{}).get('zoneType')=='Stack')
                 or draw_or_library_reorder_only_stack(o,facts))
            and not (state.get('activePlayerId')==me and state.get('currentStep') in {'PRECOMBAT_MAIN','POSTCOMBAT_MAIN'}
                     or state.get('activePlayerId')!=me and state.get('currentStep') in {'END','END_STEP'})):
            restrictions.append({'action':a['semanticId'],'rule':'tapped-land-ramp-in-main-or-opponent-end-step'})
            held_autopay |= raw.get('paymentStrategy',{}).get('type')=='AutoPay';continue
        if (not o.get('pendingDecision') and o.get('nativePaymentError') is None and own_stack and not state.get('combat')
            and name not in reaction_timing_exceptions
            and raw.get('abilityId') not in {'Lathril, Blade of the Elves:2','Sterling Grove:1'}
            and (casting or a.get('kind')=='ActivateAbility' and a.get('isManaAbility') is not True)):
            restrictions.append({'action':a['semanticId'],'rule':'let-own-stack-resolve'})
            held_autopay |= raw.get('paymentStrategy',{}).get('type')=='AutoPay';continue
        if (not o.get('pendingDecision') and o.get('nativePaymentError') is None and quiet and state.get('activePlayerId')==me
            and state.get('currentStep') in {'UNTAP','UPKEEP','DRAW'}
            and (casting or a.get('kind')=='ActivateAbility' and a.get('isManaAbility') is not True and raw.get('abilityId') not in {'Lathril, Blade of the Elves:2','Sterling Grove:1'})):
            restrictions.append({'action':a['semanticId'],'rule':'development-waits-for-main'});held_autopay |= raw.get('paymentStrategy',{}).get('type')=='AutoPay';continue
        if (not o.get('pendingDecision') and o.get('nativePaymentError') is None and quiet and can_pass and 'combat' in state and state['combat'] is None
            and (casting and 'DRAW' in facts.get(name).get('tags',[]) or
                 a.get('kind')=='ActivateAbility' and raw.get('abilityId') in facts.get(name).get('selfDrawAbilities',[]))
            and name not in reaction_timing_exceptions
            and not facts.get(name).get('affectsOpponent') and 'COUNTERSPELL' not in facts.get(name).get('tags',[])
            and not (state.get('activePlayerId')==me and state.get('currentStep') in {'PRECOMBAT_MAIN','POSTCOMBAT_MAIN'}
                     or state.get('activePlayerId')!=me and state.get('currentStep') in {'END','END_STEP'})):
            restrictions.append({'action':a['semanticId'],'rule':'draw-in-main-or-opponent-end-step'});held_autopay |= raw.get('paymentStrategy',{}).get('type')=='AutoPay';continue
        if (casting and name=='Heroic Intervention' and quiet
            and not o.get('pendingDecision') and o.get('nativePaymentError') is None
            and name not in reaction_timing_exceptions
            and state.get('currentStep') in {'UNTAP','UPKEEP','DRAW','PRECOMBAT_MAIN','POSTCOMBAT_MAIN','BEGIN_COMBAT','END_COMBAT','END','END_STEP','CLEANUP'}):
            restrictions.append({'action':a['semanticId'],'rule':'save-instant-protection-for-stack-or-combat'})
            held_autopay |= raw.get('paymentStrategy',{}).get('type')=='AutoPay';continue
        if (casting and not o.get('pendingDecision') and o.get('nativePaymentError') is None
            and 'COUNTERSPELL' in facts.get(name).get('tags',[]) and name not in counter_target_exceptions
            and a.get('minTargets')==a.get('targetCount')==1 and a.get('targetRequirements') is None
            and isinstance(a.get('validTargets'),list)):
            targets=[t for t in a['validTargets'] if not disables_only_opposing_permanent(o,t)]
            if targets!=a['validTargets']:
                restrictions.append({'action':a['semanticId'],'rule':'preserve-counter-while-opponents-disable-each-other'})
                a['validTargets']=targets
                if not targets:continue
        if (casting and name=='Syncopate' and a.get('hasXCost') and name not in counter_target_exceptions
            and type(a.get('minX')) is int and type(a.get('maxAffordableX')) is int):
            if a['maxAffordableX']<1:
                restrictions.append({'action':a['semanticId'],'rule':'preserve-zero-X-conditional-counter'});continue
            if a['minX']<1:
                a['minX']=1
                restrictions.append({'action':a['semanticId'],'rule':'conditional-counter-needs-positive-X'})
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
        # Reviewed neutralizing Auras can have UNKNOWN aggregate polarity
        # (e.g. indestructible plus loss of abilities). Apply their established
        # opposing-target default before threat ranking; friendly candidates
        # must not silently bypass the shortlist. Deck self-target overrides
        # still retain deliberate rescue/combo/protection plans.
        harmful = (casting and facts.get(name).get('spellPolarity') == ['HARMFUL']) or name in OPPONENT_REMOVAL or casting and name in NEUTRALIZING_AURAS or name=='Otawara, Soaring City' and quiet
        if harmful and name not in allow_self_targets and a.get('requiresTargets') is True and a.get('targetRequirements') is None and isinstance(a.get('validTargets'),list):
            # Retain unknown ownership rather than inventing an enemy controller.
            targets = [t for t in a['validTargets'] if cards.get(t, {}).get('controllerId', t) != me or name in BOUNCE_SOURCES and own_bounce_rescue(o,t,facts)]
            if targets != a['validTargets']:
                restrictions.append({'action':a['semanticId'],'rule':'opponent-removal-targets'})
                a['validTargets'] = targets
            if len(targets) < a.get('minTargets',1): continue
        if (casting and not o.get('pendingDecision') and o.get('nativePaymentError') is None
            and not raw.get('targets') and name not in allow_self_targets and a.get('requiresTargets') is True
            and a.get('targetRequirements') is None and a.get('minTargets')==a.get('targetCount')==1
            and isinstance(a.get('validTargets'),list) and all(isinstance(t,str) for t in a['validTargets']) and me in a['validTargets']
            and all(t in {p.get('playerId') for p in state.get('players',[])} for t in a['validTargets'])
            and not cards.get(raw.get('cardId'),{}).get('activeEffects')
            and safe_ordinary_player_draw(o,name,facts,a)):
            if a['validTargets']!=[me]:restrictions.append({'action':a['semanticId'],'rule':'ordinary-player-card-advantage-for-self'})
            a['validTargets']=[me]
            if a.get('hasXCost'):a['minX']=max(1,a['minX'])
        if name not in allow_self_targets and (casting and (name in {'Flickering Ward','Curiosity','Keen Sense','Sixth Sense'} or facts.get(name).get('spellPolarity')==['BENEFICIAL'] and all(t in ids_in(state,'Battlefield') for t in a.get('validTargets') or [])) or raw.get('abilityId') == 'Timberwatch Elf:1' or raw.get('abilityId') in facts.get(name).get('positiveStatBoostAbilities',[])) and a.get('targetRequirements') is None and isinstance(a.get('validTargets'),list):
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
                lock=facts.get(name).get('attachedCombatLock')
                if lock:
                    a['validTargets']=ranked_combat_lock_targets(o,targets,facts,lock)
                elif (name in NEUTRALIZING_AURAS or 'NEUTRALIZE' in facts.get(name).get('tags',[])) and not (
                    facts.get(name).get('removesAttachedAbilities') or name in ABILITY_REMOVING_AURAS):
                    # An uncovered suppression effect is not full removal. Keep
                    # its current native enemy domain for inference.
                    pass
                else:
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
        cost=a.get('additionalCostInfo') or {}
        if (a.get('kind')=='ActivateAbility' and raw.get('abilityId') in facts.get(name).get('bounceTargetUntapAbilities',[])
            and quiet and can_pass and not o.get('pendingDecision') and o.get('nativePaymentError') is None
            and (state.get('currentStep') in {'UNTAP','UPKEEP','DRAW','PRECOMBAT_MAIN','POSTCOMBAT_MAIN','END','END_STEP','CLEANUP'}
                 or state.get('currentStep')=='BEGIN_COMBAT' and 'combat' in state and state['combat'] is None)
            and not state.get('combat')
            and name not in allow_self_targets and name not in reaction_timing_exceptions
            and not cards.get(raw.get('sourceId'),{}).get('activeEffects') and not raw.get('costPayment')
            and a.get('requiresTargets') is True and a.get('minTargets')==a.get('targetCount')==1
            and a.get('targetRequirements') is None and isinstance(a.get('validTargets'),list)
            and cost.get('costType')=='BouncePermanent' and cost.get('bounceCount')==1
            and not cost.get('alsoRequired') and not cost.get('costAfterSacrifice')
            and isinstance(cost.get('validBounceTargets'),list)
            and not bounce_commander_rescue(o,cost['validBounceTargets'],facts)):
            # Pure untap cannot improve an already untapped creature. Preserve
            # unknown status/ownership and defer rebuy plans through deck overrides.
            targets=[t for t in a['validTargets'] if cards.get(t,{}).get('isTapped') is not False
                     and cards.get(t,{}).get('controllerId') in (None,me)]
            if targets!=a['validTargets']:
                restrictions.append({'action':a['semanticId'],'rule':'bounce-untap-needs-useful-target'})
                a['validTargets']=targets
            if not targets:
                held_autopay |= raw.get('paymentStrategy',{}).get('type')=='AutoPay';continue
            # With one remaining target, paying by returning it removes the
            # very object the ability needs. Multiple-target tradeoffs retain
            # their native cost domain for inference.
            if len(targets)==1 and targets[0] in cost['validBounceTargets']:
                cost['validBounceTargets']=[t for t in cost['validBounceTargets'] if t!=targets[0]]
                restrictions.append({'action':a['semanticId'],'rule':'preserve-sole-untap-target-during-bounce-payment'})
                if not cost['validBounceTargets']:
                    held_autopay |= raw.get('paymentStrategy',{}).get('type')=='AutoPay';continue
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
    positive_slot=positive_counter_decision(pending,facts)
    if positive_slot is not None and o.get('nativePaymentError') is None and pending.get('context',{}).get('sourceName') not in allow_self_targets:
        targets=pending['legalTargets'].get(positive_slot)
        if isinstance(targets,list) and any(cards.get(t,{}).get('controllerId')==me and 'CREATURE' in cards[t].get('cardTypes',[]) for t in targets):
            filtered=[t for t in targets if cards.get(t,{}).get('controllerId') in (None,me)]
            if filtered!=targets:
                pending['legalTargets'][positive_slot]=filtered
                restrictions.append({'rule':'positive-counter-on-own-creature'})
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
