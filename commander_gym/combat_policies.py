"""Conservative pressure and blocks from current native combat domains and live stats."""
from .heuristic_policy import ids_in


def combat_choice(policy,o,a):
    s=o['state'];me=o['agentToAct'];cards=s.get('cards',{});kind=a.get('kind')
    if (kind not in {'DeclareAttackers','DeclareBlockers'} or a.get('additionalCostInfo')
        or any(p.get('teamIndex') is not None or p.get('activeEffects') for p in s.get('players',[]))
        or a.get('mandatoryBlockers') or a.get('mandatoryAttackers') or a.get('loneAttackers') or a.get('mandatoryBlockerAssignments')):return None
    board=[cards[i] for i in ids_in(s,'Battlefield') if i in cards]
    if any(c.get('isFaceDown') or c.get('isPhasedOut') or c.get('name') in {'Propaganda','Ghostly Prison','Sphere of Safety','Dueling Grounds'} for c in board):return None
    def creature(c):return 'CREATURE' in c.get('cardTypes',[])
    def kw(c):return set(c.get('keywords',[]))
    def power(c):return max(0,c.get('power') or 0)
    def tough(c):return (c.get('toughness') or 0)-(c.get('damage') or 0)
    def value(c):
        role=policy.document['roles'].get(c.get('name'))
        return 10+power(c)+max(0,tough(c))+ (30 if c.get('isCommander') or role=='draw_engine' else 8 if role in {'ramp','mana_elf','reducer','mana_engine'} else 0)
    def survives(c,enemy):
        if 'INDESTRUCTIBLE' in kw(c):return True
        if 'DEATHTOUCH' in kw(enemy) and power(enemy)>0:return False
        if kw(enemy)&{'FIRST_STRIKE','DOUBLE_STRIKE'}:return False
        return tough(c)>power(enemy)
    if kind=='DeclareAttackers':
        attackers=a.get('validAttackers');targets=a.get('validAttackTargets')
        if not isinstance(attackers,list) or not isinstance(targets,list):return None
        enemies={p['playerId']:p for p in s.get('players',[]) if p['playerId']!=me and not p.get('hasLost')}
        if any(t not in enemies for t in targets):return None
        if any('CREATURE' in cards[i].get('cardTypes',[]) and not cards[i].get('controllerId') and i not in attackers for i in ids_in(s,'Battlefield') if i in cards):return None
        plans=[]
        for target in targets:
            blockers=[c for c in board if c.get('controllerId')==target and creature(c) and not c.get('isTapped')]
            selected={};open_damage=0
            for i in attackers:
                c=cards.get(i,{})
                pairs=a.get('attackTargetsByAttacker');allowed=pairs.get(i,[]) if isinstance(pairs,dict) else targets
                if target not in allowed or not power(c) or c.get('isTapped') or c.get('hasSummoningSickness') and 'HASTE' not in kw(c):continue
                legal=[b for b in blockers if 'FLYING' not in kw(c) or kw(b)&{'FLYING','REACH'}]
                if 'MENACE' in kw(c) and len(legal)<2:legal=[]
                if legal and any(kw(v)&{'TRAMPLE','DOUBLE_STRIKE','FIRST_STRIKE','PROTECTION','SHADOW','HORSEMANSHIP'} for v in [c,*legal]):return None
                # Open lanes are safe; small ground attackers never trade a
                # commander/draw engine into an arbitrary crowd for chip damage.
                safe=not legal or (all(survives(c,b) for b in legal) and sum(power(b) for b in legal)<tough(c))
                if safe:
                    selected[i]=target
                    if not legal:open_damage+=power(c)
            if selected:
                damage=sum(power(cards[i]) for i in selected)
                plans.append((open_damage>=enemies[target].get('life',float('inf')),damage,-enemies[target].get('life',40),selected))
        chosen=max(plans,key=lambda p:p[:3])[3] if plans else {}
        # Known mandatory draw on attacking needs its deck-out budget checked.
        if any(c.get('name')=='Kindred Discovery' and c.get('controllerId')==me for c in board):
            player=next((p for p in s.get('players',[]) if p['playerId']==me),{})
            if player.get('librarySize',0)<=len(chosen)*2+4:return None
        return policy._action(a,'default-safe-combat-pressure',{'attackers':chosen})
    pairs=a.get('validBlockTargets')
    if not isinstance(pairs,dict):return None
    if any(n is not None and n!=1 for n in (a.get('blockerMaxBlockCounts') or {}).values()):return None
    attackers=sorted({i for targets in pairs.values() for i in targets},key=lambda i:-power(cards.get(i,{})))
    if any(kw(cards.get(i,{}))&{'TRAMPLE','FIRST_STRIKE','DOUBLE_STRIKE','INFECT','WITHER','PROTECTION'} for i in set(pairs)|set(attackers)):return None
    cap=a.get('maxTotalBlockers');cap=len(pairs) if cap is None else cap
    selected={};life=next((p.get('life',40) for p in s.get('players',[]) if p['playerId']==me),40)
    incoming=sum(power(cards.get(i,{})) for i in attackers)
    for target in attackers:
        enemy=cards.get(target,{})
        available=[i for i,targets in pairs.items() if target in targets and i not in selected]
        available.sort(key=lambda i:(not survives(cards[i],enemy),value(cards[i]),i))
        count=2 if 'MENACE' in kw(enemy) else 1;group=available[:count]
        if len(group)<count or len(selected)+count>cap:continue
        safe=all(survives(cards[i],enemy) for i in group)
        trade=sum(power(cards[i]) for i in group)>=tough(enemy) and sum(value(cards[i]) for i in group)<=value(enemy)
        if safe or trade or incoming>=life:
            selected.update({i:[target] for i in group});incoming-=power(enemy)
    return policy._action(a,'default-safe-blocks',{'blockers':selected})
