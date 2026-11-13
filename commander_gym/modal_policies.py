"""Reviewed independent removal modes, using the native pre-cast target preview.

The preview supplies target legality. Live pending decisions remain authoritative;
missing previews, extra costs and changed target domains defer to the model.
"""
from .heuristic_policy import ids_in
from .policy_action_bounds import removal_target_value

DESTROY_MODES=frozenset('Destroy target '+kind for kind in ['artifact','creature','enchantment','land','planeswalker'])
REVIEWED_REMOVAL_MODALS={'Casualties of War':DESTROY_MODES}
REVIEWED_SINGLE_REMOVAL_MODES={'Golgari Charm':{1:'Destroy target enchantment'}}
CONSTRAINTS=('sameOwner','sameNumericProperty','selectionGroups','exclusiveGroups','distinctCategories','totalCap',
             'differentNames','differentControllers','mustDifferFromEarlier','validTargetsByPrefix',
             'xConstrainsCount','xConstrainsCountExactly','xConstrainsManaValue','xConstrainsManaValueExactly','xConstrainsPower')


def _key(o):return o['agentToAct'],o['state'].get('turnNumber'),o['state'].get('currentStep')


def _enemy_target(o,entity):
    card=o['state'].get('cards',{}).get(entity,{})
    return (entity in ids_in(o['state'],'Battlefield') and card.get('controllerId') not in (None,o['agentToAct'])
        and 'INDESTRUCTIBLE' not in card.get('keywords',[]))


def commander_rescue_mode(o,a,facts):
    """Restore a commander's printed abilities through a reviewed native mode.

    The native enumerator already fixes the chosen mode and legal targets. We
    implement only its ordinary destruction effect, not unrelated modal choices.
    """
    from .heuristic_policy import affordable
    from .policy_action_bounds import abilities_removed
    raw=a.get('action',{});cards=o['state'].get('cards',{});name=cards.get(raw.get('cardId'),{}).get('name')
    modes=raw.get('chosenModes');reviewed=REVIEWED_SINGLE_REMOVAL_MODES.get(name,{})
    if (a.get('kind')!='CastSpellMode' or not affordable(a) or not isinstance(modes,list) or len(modes)!=1
        or type(modes[0]) is not int or modes[0] not in reviewed or reviewed[modes[0]]!=a.get('description')
        or a.get('requiresTargets') is not True or a.get('minTargets')!=1 or a.get('targetCount')!=1
        or a.get('targetRequirements') is not None or a.get('additionalCostInfo') or a.get('modalEnumeration')
        or raw.get('useAlternativeCost') or any(a.get(k) for k in ['hasXCost','hasConvoke','hasDelve','hasHarmonize','hasTapForGeneric','requiresDamageDistribution'])
        or a.get('parameterSpec',{}).get('allowedFields',{}).get('targets')!='ENTITY_ID_ARRAY'):return None
    candidates=[]
    for target in a.get('validTargets') or []:
        if not _enemy_target(o,target) or not abilities_removed(o,{'attachments':[target]},facts):continue
        for host in ids_in(o['state'],'Battlefield',o['agentToAct']):
            c=cards.get(host,{})
            if c.get('isCommander') and target in c.get('attachments',[]):
                remaining={**c,'attachments':[i for i in c['attachments'] if i!=target]}
                if not abilities_removed(o,remaining,facts):candidates.append(target)
    return {'targets':[sorted(set(candidates),key=lambda t:(-removal_target_value(o,t,facts),t))[0]]} if candidates else None


def modal_plan(o,a,facts):
    source=a.get('action',{}).get('cardId');name=o['state'].get('cards',{}).get(source,{}).get('name')
    reviewed=REVIEWED_REMOVAL_MODALS.get(name);enum=a.get('modalEnumeration') or {}
    if (not reviewed or a.get('kind')!='CastSpellModal' or enum.get('allowRepeat') is not False
        or enum.get('additionalCostPerExtraMode') or enum.get('additionalManaCostPerExtraMode')
        or type(enum.get('chooseCount')) is not int or not 1<=enum['chooseCount']<=5
        or type(enum.get('minChooseCount')) is not int or not 1<=enum['minChooseCount']<=enum['chooseCount']):return None
    modes=enum.get('modes');candidates=[]
    if not isinstance(modes,list):return None
    for mode in modes:
        slots=mode.get('targetRequirements')
        if (type(mode.get('index')) is not int or type(mode.get('available')) is not bool or mode.get('description') not in reviewed or mode.get('additionalCostInfo') or mode.get('additionalManaCost')
            or not isinstance(slots,list) or len(slots)!=1):return None
        slot=slots[0]
        if (slot.get('minTargets')!=1 or slot.get('maxTargets')!=1 or any(slot.get(k) for k in CONSTRAINTS)
            or not isinstance(slot.get('validTargets'),list)):return None
        if mode.get('available') is True:
            targets=[t for t in slot['validTargets'] if _enemy_target(o,t)]
            if targets:candidates.append((mode,targets))
    if len({mode['description'] for mode,_ in candidates})!=len(candidates):return None
    cards=o['state'].get('cards',{})
    def rank(entity):
        controller=cards[entity]['controllerId']
        pressure=sum(max(0,cards.get(i,{}).get('power') or 0) for i in ids_in(o['state'],'Battlefield',controller))
        return removal_target_value(o,entity,facts),pressure
    plan=[];used=set()
    while candidates and len(plan)<enum['chooseCount']:
        available=[(mode,[t for t in targets if t not in used]) for mode,targets in candidates]
        available=[(mode,targets) for mode,targets in available if targets]
        if not available:break
        mode,targets=max(available,key=lambda item:max(rank(t) for t in item[1]))
        target=sorted(targets,key=lambda t:(-rank(t)[0],-rank(t)[1],t))[0]
        plan.append({'description':mode['description'],'target':target,'index':mode['index']});used.add(target)
        candidates=[(m,ts) for m,ts in candidates if m['index']!=mode['index']]
    return {'source':source,'name':name,'modes':plan} if len(plan)>=enum['minChooseCount'] else None


def remember_modal_plans(policy,o):
    key=_key(o)
    if getattr(policy,'_modal_plan_key',None)!=key:policy._modal_plan_key=key;policy._modal_plans={}
    if o.get('pendingDecision') is not None:return
    plans={}
    for a in o.get('legalActions',[]):
        plan=modal_plan(o,a,policy.facts)
        if plan and plan['name'] not in policy.self_target_exceptions:plans[plan['source']]=plan
    policy._modal_plans=plans


def modal_pending(policy,o,p):
    ctx=p.get('context',{});plan=getattr(policy,'_modal_plans',{}).get(ctx.get('sourceId'))
    if (not plan or getattr(policy,'_modal_plan_key',None)!=_key(o) or ctx.get('phase')!='CASTING'
        or ctx.get('sourceName')!=plan['name']):return None
    rt=p.get('responseSpec',{}).get('responseType')
    if (rt=='OptionChosenResponse' and p.get('kind')=='ChooseOptionDecision'
        and p.get('prompt','').startswith('Choose a mode for '+plan['name']+' (')
        and isinstance(p.get('options'),list) and all(label in DESTROY_MODES or label=='Done' for label in p['options'])):
        for mode in plan['modes']:
            if mode['description'] in p['options']:
                return policy._decision(p,rt,'shared-modal-use-independent-removal-mode',optionIndex=p['options'].index(mode['description']))
        if 'Done' in p['options']:return policy._decision(p,rt,'shared-modal-finish-reviewed-modes',optionIndex=p['options'].index('Done'))
    slots=p.get('targetRequirements')
    if (rt in {'TargetsResponse','TargetsChosenResponse'} and isinstance(slots,list) and len(slots)==1
        and slots[0].get('minTargets')==slots[0].get('maxTargets')==1 and not any(slots[0].get(k) for k in CONSTRAINTS)
        and isinstance(p.get('legalTargets'),dict)):
        mode=next((m for m in plan['modes'] if m['description']==ctx.get('effectHint')),None);slot=str(slots[0]['index'])
        if mode and mode['target'] in p['legalTargets'].get(slot,[]) and _enemy_target(o,mode['target']):
            return policy._decision(p,rt,'shared-modal-native-preview-target',selectedTargets={slot:[mode['target']]})
    return None
