"""Native target cardinalities shared by schemas and final action validation.

Cost selections and spell/ability targets are independent. Only explicit native
bounds are used; complex per-group contracts retain their existing native checks.
"""
from copy import deepcopy


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
