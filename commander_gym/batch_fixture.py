"""Synthetic keep/pass/discard fixture driven only by the current masked offers.

No SDK or credentials. Selects only unconstrained native entity selections; every
choice still passes the ordinary pilot schema, receipt and native validators.
"""
import json
import threading
import time
from types import SimpleNamespace
from .qa_legal_responses import _observation, legal_fixture_value


def value(request):
    observation=_observation(request)
    pending=observation.get('pendingDecision')
    if not isinstance(pending,dict) or pending.get('kind')!='SelectCardsDecision':return legal_fixture_value(request)
    options=pending.get('options');count=pending.get('minSelections');maximum=pending.get('maxSelections')
    constraints=('conditionalMinimums','selectionLimits','minTotalManaValue','maxTotalManaValue','maxTotalPower',
                 'onePerBasicLandType','onePerCardName','onePerCardType','onePerColor','onePerPower','librarySearch')
    if (not isinstance(options,list) or not all(isinstance(x,str) and x for x in options)
        or len(options)>512 or len(set(options))!=len(options) or type(count) is not int or type(maximum) is not int
        or not 0<=count<=maximum<=len(options) or any(pending.get(k) for k in constraints)
        or pending.get('responseSpec',{}).get('responseType')!='CardsSelectedResponse'
        or pending.get('responseSpec',{}).get('requiredFields')!={'selectedCards':'ENTITY_ID_ARRAY'}):
        raise ValueError('batch_fixture_unsupported_selection')
    excluded=pending.get('nonSelectableOptions') or []
    chosen=[x for x in options if x not in excluded][:count]
    if len(chosen)!=count:raise ValueError('batch_fixture_selection_unavailable')
    return {'channel':'decision','response':{'type':'CardsSelectedResponse','selectedCards':chosen}}


class BatchFixtureClient:
    def __init__(self,maximum,seconds):
        self.responses=self;self.maximum=maximum;self.deadline=time.monotonic()+seconds
        self.calls=0;self.lock=threading.Lock()
    def create(self,**request):
        with self.lock:
            if self.calls>=self.maximum or time.monotonic()>self.deadline:raise ValueError('batch_fixture_bound')
            self.calls+=1;ordinal=self.calls
        result=value(request)
        return SimpleNamespace(id='batch-fixture-'+str(ordinal),output_text=json.dumps(result),
                               usage=SimpleNamespace(input_tokens=0,output_tokens=0,total_tokens=0))
