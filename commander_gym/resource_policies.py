"""Shared development values for revealed cards; never inspect hidden library order."""
from collections import Counter
from itertools import combinations
from math import comb
from .heuristic_policy import ids_in,mana_cost

CONSTRAINTS = ('selectionLimits','conditionalMinimums','onePerCardType','onePerColor','onePerCardName',
               'onePerBasicLandType','onePerPower','maxTotalManaValue','minTotalManaValue','maxTotalPower')


class ResourcePolicies:
    def _resource_value(self,o,card):
        own=self._own(o);me=o['agentToAct'];hand=[self._card(o,i) for i in ids_in(o['state'],'Hand',me)]
        def land(c):return 'LAND' in c.get('cardTypes',[]) or 'Land' in c.get('typeLine','')
        lands=sum(land(c) for c in own);held=sum(land(c) for c in hand)
        if land(card) and held>=2 and lands>=2:return -20
        if land(card):return 160 if lands<4 and held<2 else 110 if lands<6 and held==0 else 35 if lands+held<6 else -20
        f=self.facts.get(card.get('name'));role=self.document['roles'].get(card.get('name'))
        score=self._opening_value(card)
        if role in {'commander','draw_engine'}:score+=35
        if role in {'finisher','closing','recovery','sweeper'}:score-=35
        if role in self.document.get('mana_roles',[]) and lands>=5:score-=40
        if role=='interaction':score-=20*max(0,sum(self.document['roles'].get(c.get('name'))=='interaction' for c in hand)-2)
        cost=mana_cost(card.get('manaCost',f.get('manaCost','')))
        if cost:score-=max(0,cost[0]-lands-1)*12
        return score

    def _resource_pending(self,o,p):
        rt=p.get('responseSpec',{}).get('responseType');ctx=p.get('context',{});source=ctx.get('sourceName')
        key=(o['agentToAct'],o['state'].get('turnNumber'),ctx.get('sourceId'))
        if (source=='Ponder' and rt=='OrderedResponse' and p.get('kind')=='ReorderLibraryDecision'
            and isinstance(p.get('cards'),list) and len(p['cards'])==3 and all(i in (p.get('cardInfo') or {}) for i in p['cards'])):
            info=p['cardInfo'];ordered=sorted(p['cards'],key=lambda i:(-self._resource_value(o,info[i]),i))
            self._ponder_review=(key,self._resource_value(o,info[ordered[0]])<20)
            return self._decision(p,rt,'default-order-for-development',orderedObjects=ordered)
        if (source=='Ponder' and rt=='YesNoResponse' and p.get('prompt')=='You may shuffle your library'
            and getattr(self,'_ponder_review',None) and self._ponder_review[0]==key):
            _,shuffle=self._ponder_review
            return self._decision(p,rt,'default-keep-reviewed-ponder-top',choice=p.get('yesAvailable') is True and shuffle)
        if (p.get('kind') not in {'SearchLibraryDecision','SelectCardsDecision'} or rt!='CardsSelectedResponse'
            or p.get('ordered') or any(p.get(k) for k in CONSTRAINTS) or p.get('nonSelectableOptions')):return None
        options=p.get('options');info={**o['state'].get('cards',{}),**(p.get('cardInfo') or {})}
        if not isinstance(options,list) or not all(i in info and info[i].get('name') for i in options):return None
        low=p.get('minSelections');high=p.get('maxSelections')
        if not isinstance(low,int) or not isinstance(high,int):return None
        if (p.get('selectedLabel')=='Put on bottom' and p.get('remainderLabel')=='Put on top'
            and low==0 and 1<=high<=5 and p.get('prompt')==f'Scry {high}: choose cards to put on the bottom of your library'):
            bottom=[i for i in options if self._resource_value(o,info[i])<25]
            return self._decision(p,rt,'default-scry-for-curve',selectedCards=bottom[:high])
        discards={'Flow of Knowledge':"Choose two cards to discard",'Thirst for Discovery':'Choose 2 cards to discard, or 1 basic land card','Thirst for Knowledge':'Choose 2 cards to discard, or 1 artifact card'}
        if source in discards and p.get('prompt')==discards[source] and low==high==2:
            # Respect the advertised count even if the English prompt mentions
            # an alternative not represented by native cardinality constraints.
            if comb(len(options),2)>512:return None
            board_lands=sum('LAND' in c.get('cardTypes',[]) for c in self._own(o))
            hand_lands={i for i in ids_in(o['state'],'Hand',o['agentToAct']) if 'Land' in self._card(o,i).get('typeLine','') or 'LAND' in self._card(o,i).get('cardTypes',[])}
            needed=min(len(hand_lands),max(0,5-board_lands))
            def retained_value(discard):
                return sum(self._resource_value(o,info[i]) for i in options if i not in discard)-500*max(0,needed-len(hand_lands-set(discard)))
            chosen=list(max(combinations(sorted(options),2),key=retained_value))
            return self._decision(p,rt,'default-discard-for-development',selectedCards=chosen)
        if source=='Land Tax' and low==0 and high==3 and all('Basic Land' in info[i].get('typeLine','') for i in options):
            need=Counter()
            for i in ids_in(o['state'],'Hand',o['agentToAct']):
                cost=mana_cost(self._card(o,i).get('manaCost',''))
                if cost:need.update(cost[1])
            supply=Counter(c for card in self._own(o) for c in self._land_colors(card,o))
            chosen=[]
            for _ in range(min(3,len(options))):
                remaining=[i for i in options if i not in chosen]
                best=max(remaining,key=lambda i:(sum((20 if supply[c]==0 else 0)+need[c]-supply[c] for c in self._land_colors(info[i],o)),i))
                chosen.append(best);supply.update(self._land_colors(info[best],o))
            return self._decision(p,rt,'shared-card-search-basic-color-needs',selectedCards=chosen)
        if source in {'Sleight of Hand','Anticipate','Impulse','Memory Deluge'} and low==high and 1<=low<=2:
            chosen=sorted(options,key=lambda i:(-self._resource_value(o,info[i]),i))[:low]
            return self._decision(p,rt,'default-select-revealed-resources',selectedCards=chosen)
        if source in {'Mystical Tutor','Wirewood Herald'} and low in {0,1} and high==1:
            chosen=sorted(options,key=lambda i:(-self._resource_value(o,info[i]),i) if source=='Mystical Tutor' else (-self._search_score(o,p,info[i]),-self._resource_value(o,info[i]),i))[:1]
            return self._decision(p,rt,'default-search-for-development',selectedCards=chosen)
        return None
