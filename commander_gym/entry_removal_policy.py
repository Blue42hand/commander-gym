"""Proactive plans for certified entry-trigger stat removal, from masked facts only."""
from .heuristic_policy import ids_in
from .policy_action_bounds import abilities_removed,removal_target_value


def entry_reduction_victims(o,card,facts):
    effect=facts.get(card.get('name')).get('entryStatReduction')
    if not effect or card.get('activeEffects'):return []
    s=o['state'];me=o['agentToAct'];cards=s.get('cards',{})
    opponents={p['playerId'] for p in s.get('players',[]) if p.get('playerId')!=me}
    if any(p.get('activeEffects') for p in s.get('players',[])):return []
    victims=[]
    for entity in ids_in(s,'Battlefield'):
        c=cards.get(entity,{})
        if c.get('controllerId') not in opponents or 'CREATURE' not in c.get('cardTypes',[]):continue
        if c.get('activeEffects') or c.get('isPhasedOut') is not False or c.get('isFaceDown') is not False or abilities_removed(o,c,facts):continue
        if any(not isinstance(c.get(k),list) for k in ['keywords','protections','hexproofFromColors','abilityFlags']):continue
        if any(type(c.get(k)) is not bool for k in ['hexproofFromMonocolored','hexproofFromMulticolored']):continue
        keywords=c.get('keywords')
        if not isinstance(keywords,list) or 'SHROUD' in keywords or 'PROTECTION' in keywords or c.get('protections'):continue
        if (not effect['suppressesHexproof'] and ('HEXPROOF' in keywords or c.get('hexproofFromColors') or c.get('hexproofFromMonocolored') or c.get('hexproofFromMulticolored'))):continue
        if not effect['suppressesWard'] and any('WARD' in k for k in keywords+c.get('abilityFlags',[])):continue
        if type(c.get('toughness')) is not int:continue
        remaining=c['toughness']+effect['toughness'];damage=c.get('damage') or 0
        if remaining>0 and ('INDESTRUCTIBLE' in keywords or type(damage) is not int or damage<remaining):continue
        f=facts.get(c.get('name'))
        if not (c.get('isCommander') or f.get('repeatable') and set(f.get('tags',[])) & {'DRAW','RAMP'} or f.get('variableMana')):continue
        victims.append(entity)
    return sorted(victims,key=lambda t:(-removal_target_value(o,t,facts),t))
