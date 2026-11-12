"""Immutable public card facts exported from a pinned native registry.

No game state, library order, executable hooks, or provider credentials belong here.
Unknown facts remain unknown. Printed characteristics never replace a live card view.
"""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
from types import MappingProxyType

from .deck_package import ArtifactRef


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


class CardFacts:
    def __init__(self, document):
        self._document = deepcopy(document)
        if (not isinstance(document, dict) or set(document) != {'schemaVersion', 'engineRevision', 'creatureTypes', 'cards'}
            or type(document['schemaVersion']) is not int or document['schemaVersion'] != 1
            or not isinstance(document['engineRevision'], str)
            or len(document['engineRevision']) != 40
            or any(c not in '0123456789abcdef' for c in document['engineRevision'])
            or not isinstance(document['cards'], dict)
            or not isinstance(document['creatureTypes'], list)
            or any(not isinstance(t, str) or not t for t in document['creatureTypes'])):
            raise ValueError('invalid native card facts envelope')
        fields = {'manaCost', 'types', 'subtypes', 'keywords', 'tags', 'spellPolarity',
                  'manaColors', 'simpleMana', 'simplePermanent', 'unconditionalUntapped', 'entersTapped',
                  'staticValue', 'repeatable', 'affectsOpponent', 'feeds', 'rewards', 'tribesRewarded'}
        fields |= {'freeOptionalDraws', 'drawSensitive'}
        for name, fact in document['cards'].items():
            if not isinstance(name, str) or not name or not isinstance(fact, dict) or not fields <= set(fact) or set(fact) - fields - {'developmentMana', 'manaOnlySelfUntapAbilities', 'fixedStatReductions', 'removesAttachedAbilities', 'untappedIfLandSubtypes', 'variableMana', 'commanderColorMana', 'painManaColors', 'colorIdentity', 'untappedPayLife'}:
                raise ValueError('invalid native card fact fields')
            if 'developmentMana' in fact and (type(fact['developmentMana']) is not int or not 0<=fact['developmentMana']<=20):
                raise ValueError('invalid native development mana')
            untaps=fact.get('manaOnlySelfUntapAbilities',[])
            if not isinstance(untaps,list) or any(not isinstance(v,str) or not v for v in untaps):
                raise ValueError('invalid native untap ability contract')
            reductions=fact.get('fixedStatReductions',{})
            if not isinstance(reductions,dict) or any(not isinstance(k,str) or not k or not isinstance(v,dict) or set(v)!={'power','toughness'} or type(v['power']) is not int or v['power']>0 or type(v['toughness']) is not int or v['toughness']>=0 for k,v in reductions.items()):
                raise ValueError('invalid native stat reduction contract')
            for key in ['painManaColors','colorIdentity']:
                if key in fact and (not isinstance(fact[key],list) or any(not isinstance(c,str) or c not in 'WUBRG' for c in fact[key])):
                    raise ValueError('invalid native mana colors')
            if 'commanderColorMana' in fact and type(fact['commanderColorMana']) is not bool:
                raise ValueError('invalid commander mana flag')
            if 'untappedPayLife' in fact and (type(fact['untappedPayLife']) is not int or not 0 <= fact['untappedPayLife'] <= 20):
                raise ValueError('invalid land life payment')
            entry = fact.get('untappedIfLandSubtypes', [])
            if (not isinstance(entry, list) or any(not isinstance(v,str) for v in entry)
                or set(entry) - {'Forest','Swamp','Plains','Island','Mountain'}):
                raise ValueError('invalid native land entry condition')
            if 'removesAttachedAbilities' in fact and type(fact['removesAttachedAbilities']) is not bool:
                raise ValueError('invalid native attached ability suppression flag')
            if 'variableMana' in fact and type(fact['variableMana']) is not bool:
                raise ValueError('invalid variable mana flag')
            for key in ('simpleMana', 'simplePermanent', 'unconditionalUntapped', 'entersTapped', 'repeatable', 'affectsOpponent', 'drawSensitive'):
                if type(fact[key]) is not bool:
                    raise ValueError('invalid native card fact boolean')
            for key in ('types', 'subtypes', 'keywords', 'tags', 'spellPolarity', 'manaColors',
                        'feeds', 'rewards', 'tribesRewarded'):
                if not isinstance(fact[key], list) or any(not isinstance(v, str) for v in fact[key]):
                    raise ValueError('invalid native card fact list')
            if (not isinstance(fact['manaCost'], str) or type(fact['staticValue']) not in (int, float)
                or not math.isfinite(fact['staticValue']) or fact['staticValue'] < 0
                or set(fact['spellPolarity']) - {'HARMFUL', 'BENEFICIAL', 'FLEXIBLE', 'UNKNOWN'}
                or set(fact['manaColors']) - set('WUBRGC')):
                raise ValueError('invalid native card fact value')
            if (not isinstance(fact['freeOptionalDraws'], dict) or any(
                not isinstance(ability, str) or not ability or type(count) is not int or not 1 <= count <= 10
                for ability, count in fact['freeOptionalDraws'].items())):
                raise ValueError('invalid free optional draw contract')
        canonical(document)
        self.engine_revision = document['engineRevision']
        self.creature_types = frozenset(t.lower() for t in document['creatureTypes'])
        self.digest = hashlib.sha256(canonical(document)).hexdigest()

    @classmethod
    def load(cls, path):
        return cls(json.loads(Path(path).read_text()))

    def document(self):
        return deepcopy(self._document)

    def get(self, name):
        # Never hand a mutable reference into the catalog to a policy or caller.
        return MappingProxyType(deepcopy(self._document['cards'].get(name, {})))


def heuristic_component_ref(facts=None):
    """Identity of defaults and reusable card mechanics, including optional public facts."""
    names = ['heuristic_facts.py', 'heuristic_policy.py', 'default_policies.py', 'card_policies.py',
             'resource_policies.py', 'combat_policies.py', 'modal_policies.py', 'bounded_policy_fallback.py', 'lmstudio_pilot.py', 'pilot_composition.py', 'action_contracts.py', 'selection_contracts.py', 'records.py', 'convoke_plans.py', 'openai_responses_pilot.py', 'policy_action_bounds.py']
    payload = {'version': 2, 'factsSha256': facts.digest if facts else 'unavailable',
               'sources': {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in names}}
    return ArtifactRef(kind='skill-set', artifact_id='argentum-default-policies', version='2',
                       digest='sha256:' + hashlib.sha256(canonical(payload)).hexdigest())
