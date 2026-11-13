"""Native target cardinalities shared by schemas and final action validation.

Cost selections and spell/ability targets are independent. Only explicit native
bounds are used; complex per-group contracts retain their existing native checks.
"""
from copy import deepcopy


def material_cost_domains(action):
    """The native parameterizer's unions of offered material-cost candidates."""
    info=action.get('additionalCostInfo')
    if not isinstance(info,dict):return {}
    entries=[info]+(info.get('alsoRequired') or [])
    if any(not isinstance(c,dict) for c in entries):return {}
    fields={
        'tappedPermanents':('tap',['validTapTargets']),
        'sacrificedPermanents':('sacrifice',['validSacrificeTargets']),
        'discardedCards':('discard',['validDiscardTargets']),
        'exiledCards':('exile',['validExileTargets','validCraftMaterials']),
    }
    result={}
    for field,(alias,keys) in fields.items():
        arrays=[c.get(k,[]) for c in entries for k in keys]
        if any(not isinstance(v,list) or any(not isinstance(i,str) for i in v) for v in arrays):continue
        candidates=list(dict.fromkeys(i for values in arrays for i in values))
        # Only a pure fixed tap cost has a required count here. Composite,
        # repeated and variable costs retain native cardinality semantics.
        count=info.get('tapCount') if (field=='tappedPermanents' and action.get('action',{}).get('type')=='ActivateAbility' and info.get('costType')=='TapPermanents'
            and len(entries)==1 and action.get('action',{}).get('repeatCount',1)==1) else None
        if type(count) is not int or not 0<count<=len(candidates):count=None
        result[field]=(alias,candidates,count)
    return result


def constrain_material_cost_params(schema, action):
    schema=deepcopy(schema);properties=schema.get('properties',{})
    for field,(alias,candidates,count) in material_cost_domains(action).items():
        for key in [field,alias]:
            if key not in properties:continue
            previous=properties[key];item={**previous.get('items',{}),'type':'string'}
            ids=[i for i in candidates if not isinstance(item.get('enum'),list) or i in item['enum']]
            if ids:item['enum']=ids
            properties[key]={**previous,'type':'array','items':item}
            if not ids:properties[key]['maxItems']=0
        if count is not None and (field in properties or alias in properties):
            # One canonical model spelling avoids ambiguous duplicate aliases.
            selected=field if field in properties else alias
            if field in properties:properties.pop(alias,None)
            properties[selected]['minItems']=count;properties[selected]['maxItems']=count
            base=action.get('action',{}).get('costPayment') or {}
            if not base.get(field):schema['required']=list(dict.fromkeys(schema.get('required',[])+[selected]))
    return schema


def validate_material_cost_params(params, action):
    for field,(alias,candidates,count) in material_cost_domains(action).items():
        canonical=params.get(field,[]);short=params.get(alias,[])
        if canonical and short:raise ValueError('native material cost has ambiguous aliases: '+field)
        selected=canonical or short
        if (not isinstance(selected,list) or any(not isinstance(i,str) for i in selected)
            or len(selected)!=len(set(selected)) or not set(selected)<=set(candidates)):
            raise ValueError('native material cost outside offered domain: '+field)
        base=action.get('action',{}).get('costPayment') or {}
        if count is not None and len(selected or base.get(field,[]))!=count:
            raise ValueError('native material cost cardinality outside offered bounds: '+field)


def target_bounds(action):
    if action.get('requiresTargets') is not True or action.get('targetRequirements') is not None:
        return None
    low = action.get('minTargets')
    high = None if action.get('xConstrainsTargetCount') or action.get('xConstrainsTargetCountExactly') else action.get('targetCount')
    return (low if type(low) is int else 1, high if type(high) is int else None)


def constrain_target_params(schema, action):
    schema = deepcopy(schema)
    bounds = target_bounds(action)
    if bounds is None: return schema
    low, high = bounds; fields = schema.get('properties', {})
    offered = action.get('validTargets')
    item = {'type':'string'}
    if isinstance(offered,list) and offered: item['enum'] = list(dict.fromkeys(offered))
    array = {'type':'array','items':item,'minItems':low}
    if high is not None: array['maxItems'] = high
    if offered == []: array['maxItems'] = 0
    aliases = []
    if 'targets' in fields:
        fields['targets'] = array; aliases.append({'type':'object','required':['targets']})
    if 'targetGroups' in fields:
        # With no per-group native contract, one group represents the flat slot.
        fields['targetGroups'] = {'type':'array','items':deepcopy(array),'maxItems':1}
        if low > 0: fields['targetGroups']['minItems'] = 1
        aliases.append({'type':'object','required':['targetGroups']})
    if low > 0 and aliases: schema['anyOf'] = aliases
    return schema


def validate_target_params(params, action):
    bounds = target_bounds(action)
    if bounds is None: return
    low, high = bounds
    flat = params.get('targets', [])
    groups = params.get('targetGroups', [])
    if not isinstance(flat,list) or not isinstance(groups,list) or any(not isinstance(g,list) for g in groups):
        raise ValueError('native action targets must be arrays')
    if len(groups)>1 or flat and groups:
        raise ValueError('native action has ambiguous target representations')
    selected = flat if flat else (groups[0] if groups else [])
    if (any(not isinstance(i,str) for i in selected) or len(selected)!=len(set(selected))
        or len(selected)<low or high is not None and len(selected)>high):
        raise ValueError('native action target cardinality outside offered bounds')
    offered = action.get('validTargets')
    if isinstance(offered,list) and not set(selected)<=set(offered):
        raise ValueError('native action target outside offered domain')
