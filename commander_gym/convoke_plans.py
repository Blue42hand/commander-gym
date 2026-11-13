"""Complete conservative convoke payments from authoritative revealed offers.

Choose one resource-preserving allocation per native X value. Unknown costs and
large domains defer; native execution remains the authority on payment legality.
"""
from collections import Counter
from .heuristic_policy import mana_cost,MaskedHeuristicSubsystem
from .records import native_convoke_offer

COLORS={'WHITE':'W','BLUE':'U','BLACK':'B','RED':'R','GREEN':'G'}
PIPS='WUBRGC'


def convoke_plans(o,a,*,require_complete=True):
    """Return canonical allocations; partial mode never narrows the native X menu.

    Frozen choices require complete coverage. Native-schema decoding may bind a
    proved selected X while leaving uncovered X values to the native contract.
    """
    candidates=native_convoke_offer(a)
    if not candidates or a.get('kind')!='CastSpell' or a.get('additionalCostInfo'):return None
    if any(a.get(k) for k in ['hasDelve','hasHarmonize','hasTapForGeneric','modalEnumeration','manaCostByTargetCount','manaCostPerExtraTarget','optionalManaCostString']):return None
    raw=a.get('action',{})
    if any(raw.get(k) for k in ['alternativePayment','chosenModes','useAlternativeCost','castFaceDown','declaredCostSlot','optionalCostPayments','additionalManaForCounters']):return None
    text=a.get('manaCostString')
    if not isinstance(text,str):return None
    if a.get('hasXCost'):
        low=a.get('minX',0);high=a.get('maxAffordableX')
        if type(low) is not int or type(high) is not int or not 0<=low<=high or high-low>=128:return None
        values=range(low,high+1)
    else:values=[None]
    sources=[]
    for src in a.get('availableManaSources') or []:
        entity=src.get('entityId');amount=src.get('manaAmount');colors=set()
        for color in src.get('producesColors') or []:
            color=COLORS.get(color,color)
            if color not in PIPS:return None
            colors.add(color)
        if src.get('producesColorless'):colors.add('C')
        if src.get('requiresSacrifice') or src.get('requiresTappingAnotherPermanent'):continue
        if not entity or not colors or type(amount) is not int or amount<1:return None
        if amount>1 and len(colors)!=1:return None
        sources.append((entity,[colors]*amount))
    # Sources and convokers may overlap: one creature cannot tap twice.
    source_ids={entity:index for index,(entity,_) in enumerate(sources)}
    pool=next((p.get('manaPool',{}) for p in o['state'].get('players',[]) if p.get('playerId')==o['agentToAct']),{})
    if pool.get('restrictedMana'):return None
    floating=[]
    for name,color in [('white','W'),('blue','U'),('black','B'),('red','R'),('green','G'),('colorless','C')]:
        amount=pool.get(name,0)
        if type(amount) is not int or amount<0:return None
        floating.extend([{color}]*amount)
    cards=o['state'].get('cards',{});result=[]
    for x in values:
        cost=mana_cost(text.replace('{X}','{'+str(x)+'}') if x is not None else text)
        if cost is None:return None
        total,pips=cost;generic=total-sum(pips.values())
        states={(generic,tuple(pips[c] for c in PIPS),0):(0,{})}
        for entity,colors in candidates.items():
            next_states=dict(states);card=cards.get(entity,{})
            weight=80 if card.get('isCommander') else 5 if card.get('isToken') else 12+max(0,card.get('power') or 0)
            bit=1<<source_ids[entity] if entity in source_ids else 0
            for (g,counts,mask),(score,paid) in states.items():
                choices=([None] if g else [])+[c for c in colors if counts[PIPS.index(COLORS[c])]>0]
                for color in choices:
                    remaining=list(counts);new_g=g
                    if color is None:new_g-=1
                    else:remaining[PIPS.index(COLORS[color])]-=1
                    key=(new_g,tuple(remaining),mask|bit);value=(score+weight,{**paid,entity:{'color':color}})
                    if key not in next_states or value[0]<next_states[key][0]:next_states[key]=value
            states=next_states
            if len(states)>8192:return None
        best=None
        for (g,counts,mask),(score,paid) in states.items():
            rest=Counter({c:n for c,n in zip(PIPS,counts) if n});amount=g+sum(rest.values())
            units=floating+[unit for n,(_,group) in enumerate(sources) if not mask&(1<<n) for unit in group]
            if not MaskedHeuristicSubsystem._payable((amount,rest),units):continue
            rank=(score+amount*10,len(paid),tuple(sorted(paid)))
            if best is None or rank<best[0]:best=(rank,paid)
        if best is None:
            if require_complete:return None
            continue
        result.append({**({'xValue':x} if x is not None else {}),**({'convokedCreatures':best[1]} if best[1] else {})})
    return result or None
