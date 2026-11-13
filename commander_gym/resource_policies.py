"""Shared development values for revealed cards; never inspect hidden library order."""
from collections import Counter
from itertools import combinations
from math import comb
from .heuristic_policy import ids_in,mana_cost,affordable

CONSTRAINTS = ('selectionLimits','conditionalMinimums','onePerCardType','onePerColor','onePerCardName',
               'onePerBasicLandType','onePerPower','maxTotalManaValue','minTotalManaValue','maxTotalPower')


class ResourcePolicies:
    def _floating_payable(self,o,cost_text):
        cost=mana_cost(cost_text)
        if cost is None:return False
        pool=next((p.get('manaPool',{}) for p in o['state'].get('players',[]) if p['playerId']==o['agentToAct']),{})
        units=[]
        for name,color in [('white','W'),('blue','U'),('black','B'),('red','R'),('green','G'),('colorless','C')]:
            amount=pool.get(name,0)
            if type(amount) is not int or amount<0:return False
            units.extend([{color}]*amount)
        return self._payable(cost,units)

    def _resource_value(self,o,card):
        own=self._own(o);me=o['agentToAct'];hand=[self._card(o,i) for i in ids_in(o['state'],'Hand',me)]
        def land(c):return 'LAND' in c.get('cardTypes',[]) or 'Land' in c.get('typeLine','')
        lands=sum(land(c) for c in own);held=sum(land(c) for c in hand)
        if land(card) and held>=2 and lands>=2:return -20
        if land(card):return 160 if lands<4 and held<2 else 110 if lands<6 and held==0 else 35 if lands+held<6 else -20
        if card.get('isCommander') or card.get('name')==self.document.get('commander'):return 220
        f=self.facts.get(card.get('name'));role=self.document['roles'].get(card.get('name'))
        score=self._opening_value(card)
        if role in {'commander','draw_engine'}:score+=35
        if role in {'finisher','closing','recovery','sweeper'}:score-=35
        if role in self.document.get('mana_roles',[]) and lands>=5:score-=40
        if role=='interaction':score-=20*max(0,sum(self.document['roles'].get(c.get('name'))=='interaction' for c in hand)-2)
        cost=mana_cost(card.get('manaCost',f.get('manaCost','')))
        if cost:score-=max(0,cost[0]-lands-1)*12
        return score

    def _offered_mana_payment(self,o,p):
        """Plan a small ordinary cost from native source offers; native assigns colors."""
        if p.get('context',{}).get('phase')!='CASTING':return None
        rows=p.get('availableSources');cost=mana_cost(p.get('requiredCost',''))
        if cost is None or not isinstance(rows,list) or not 1<=len(rows)<=8:return None
        player=next((v for v in o['state'].get('players',[]) if v['playerId']==o['agentToAct']),{})
        pool=player.get('manaPool',{})
        if pool.get('restrictedMana'):return None
        units=[]
        for color,name in [('W','white'),('U','blue'),('B','black'),('R','red'),('G','green'),('C','colorless')]:
            n=pool.get(name,0)
            if type(n) is not int or not 0<=n<=32:return None
            units.extend([{color}]*n)
        colors={'WHITE':'W','BLUE':'U','BLACK':'B','RED':'R','GREEN':'G'};sources=[]
        for row in rows:
            entity=row.get('entityId');amount=row.get('manaAmount');available=row.get('producesColors')
            if (not isinstance(entity,str) or not entity or type(amount) is not int or not 1<=amount<=8
                or not isinstance(available,list) or any(c not in colors for c in available)
                or type(row.get('producesColorless')) is not bool or type(row.get('requiresSacrifice')) is not bool
                or row.get('requiresTappingAnotherPermanent') is not False):return None
            choices={colors[c] for c in available}|({'C'} if row['producesColorless'] else set())
            # A batch choosing one color cannot be split into arbitrary colors.
            if not choices or amount>1 and len(choices)>1:return None
            sources.append((entity,choices,amount,row['requiresSacrifice']))
        if len({x[0] for x in sources})!=len(sources):return None
        plans=[]
        for size in range(1,len(sources)+1):
            for selected in combinations(sources,size):
                supplied=units+[colors for _,colors,n,_ in selected for _ in range(n)]
                if self._payable(cost,supplied):
                    ids=sorted(x[0] for x in selected)
                    plans.append(((sum(x[3] for x in selected),size,len(supplied)-cost[0],ids),ids))
        if not plans:return None
        selected_ids=min(plans)[1]
        # Native selected-sacrifice planning deliberately cannot prove a hidden
        # battlefield. Activate an offered source explicitly instead; never
        # inspect the hidden identity or weaken that native proof boundary.
        hidden_board=any(self._card(o,i).get('isFaceDown') is True for i in ids_in(o['state'],'Battlefield'))
        selected=[row for row in sources if row[0] in selected_ids]
        if hidden_board and any(row[3] for row in selected):
            namespace=tuple(v.get('playerId') for v in o['state'].get('players',[]))
            if not namespace or any(not isinstance(v,str) for v in namespace):return None
            for entity,choices,amount,_ in selected:
                source_actions=[a for a in o.get('legalActions',[]) if a.get('kind')=='ActivateAbility'
                    and a.get('isManaAbility') is True and affordable(a) and a.get('action',{}).get('sourceId')==entity]
                if len(source_actions)!=1:continue
                others=units+[cs for e,cs,n,_ in selected if e!=entity for _ in range(n)]
                for color in sorted(choices):
                    if not self._payable(cost,others+[{color}]*amount):continue
                    native_color={v:k for k,v in colors.items()}.get(color)
                    for offer in source_actions:
                        raw=offer.get('action',{})
                        if (offer.get('kind')!='ActivateAbility' or offer.get('isManaAbility') is not True
                            or not affordable(offer) or raw.get('sourceId')!=entity
                            or offer.get('requiresTargets') is not False or offer.get('hasXCost') is not False
                            or offer.get('additionalCostInfo') is not None):continue
                        if offer.get('requiresManaColorChoice') is True:
                            if native_color is None:continue
                            offered_colors=offer.get('availableManaColors')
                            if offered_colors is not None and native_color not in offered_colors:continue
                            self._manual_mana_color=(namespace,o['agentToAct'],o['state'].get('turnNumber'),
                                o['state'].get('currentStep'),entity,native_color,dict(pool))
                        elif len(choices)!=1:continue
                        return self._action(offer,'default-manual-offered-mana-payment')
            return None
        return self._decision(p,'ManaSourcesSelectedResponse','default-complete-offered-mana-payment',
            autoPay=False,declined=False,selectedSources=selected_ids,waterbendPermanents=[])

    def _resource_pending(self,o,p):
        rt=p.get('responseSpec',{}).get('responseType');ctx=p.get('context',{});source=ctx.get('sourceName')
        intent=getattr(self,'_manual_mana_color',None)
        self._manual_mana_color=None
        if intent is not None:
            namespace,actor,turn,step,entity,color,pool=intent
            current_pool=next((v.get('manaPool',{}) for v in o['state'].get('players',[]) if v['playerId']==o['agentToAct']),{})
            if (rt=='ColorChosenResponse' and p.get('kind')=='ChooseColorDecision'
                and p.get('playerId')==actor==o['agentToAct'] and tuple(v.get('playerId') for v in o['state'].get('players',[]))==namespace
                and o['state'].get('turnNumber')==turn and o['state'].get('currentStep')==step
                and p.get('prompt')=='Choose a color of mana to add' and p.get('maxColors')==1
                and ctx.get('phase')=='RESOLUTION' and ctx.get('sourceId')==entity
                and color in (p.get('availableColors') or []) and current_pool==pool):
                return self._decision(p,rt,'default-complete-manual-mana-color',color=color)
        # The model may select a manual mana action whose color is asked in a
        # separate callback. Keep the immediate casting cost across that boundary.
        observed=getattr(self,'_callback_mana_color_need',None)
        self._callback_mana_color_need=None
        namespace=tuple(v.get('playerId') for v in o['state'].get('players',[]))
        current_pool=next((v.get('manaPool',{}) for v in o['state'].get('players',[]) if v['playerId']==o['agentToAct']),{})
        key=(namespace,o['agentToAct'],o['state'].get('turnNumber'),o['state'].get('currentStep'))
        if observed is not None:
            previous_key,sources,color,pool=observed
            if (key==previous_key and rt=='ColorChosenResponse' and p.get('kind')=='ChooseColorDecision'
                and p.get('playerId')==o['agentToAct'] and ctx.get('phase')=='RESOLUTION'
                and ctx.get('sourceId') in sources and p.get('prompt')=='Choose a color of mana to add'
                and p.get('maxColors')==1 and current_pool==pool and color in (p.get('availableColors') or [])):
                return self._decision(p,rt,'default-preserve-casting-payment-color',color=color)
        if (rt=='ManaSourcesSelectedResponse' and ctx.get('phase')=='CASTING'
            and namespace and all(isinstance(v,str) and v for v in namespace)
            and not current_pool.get('restrictedMana')):
            cost=mana_cost(p.get('requiredCost',''));units=[]
            valid=cost is not None
            for name,color in [('white','W'),('blue','U'),('black','B'),('red','R'),('green','G'),('colorless','C')]:
                n=current_pool.get(name)
                if type(n) is not int or not 0<=n<=32:valid=False;break
                units.extend([{color}]*n)
            if valid and not self._payable(cost,units):
                colors=[name for name,color in [('WHITE','W'),('BLUE','U'),('BLACK','B'),('RED','R'),('GREEN','G')]
                        if self._payable(cost,units+[{color}])]
                sources={a.get('action',{}).get('sourceId') for a in o.get('legalActions',[])
                         if a.get('kind')=='ActivateAbility' and a.get('isManaAbility') is True and affordable(a)
                         and a.get('requiresManaColorChoice') is True and isinstance(a.get('action',{}).get('sourceId'),str)}
                if len(colors)==1 and sources:
                    self._observed_mana_color_need=(key,sources,colors[0],dict(current_pool))
        if rt=='ManaSourcesSelectedResponse' and (p.get('canAutoPayNow') is True or self._floating_payable(o,p.get('requiredCost',''))):
            return self._decision(p,rt,'default-fund-native-mana-request',autoPay=p.get('canAutoPayNow') is not False,declined=False,selectedSources=[],waterbendPermanents=[])
        if rt=='ManaSourcesSelectedResponse':
            planned=self._offered_mana_payment(o,p)
            if planned is not None:return planned
        if (rt=='ManaSourcesSelectedResponse' and p.get('canDecline') is True and p.get('availableSources')==[]
            and p.get('waterbendPermanents')==[] and not p.get('autoPaySuggestion')
            and not any(affordable(a) and a.get('isManaAbility') for a in o.get('legalActions',[]))):
            cost=mana_cost(p.get('requiredCost',''));player=next((v for v in o['state'].get('players',[]) if v['playerId']==o['agentToAct']),{})
            pool=player.get('manaPool',{});amounts=[pool.get(c,0) for c in ['white','blue','black','red','green','colorless']]
            if cost is not None and not pool.get('restrictedMana') and all(type(n) is int and n>=0 for n in amounts) and not self._floating_payable(o,p.get('requiredCost','')):
                if ctx.get('phase')=='COMBAT' and source=='Attack tax':self._unfunded_attack=(o['agentToAct'],o['state'].get('turnNumber'),o['state'].get('currentStep'))
                return self._decision(p,rt,'default-decline-proven-unfunded-payment',autoPay=False,declined=True,selectedSources=[],waterbendPermanents=[])
        prompt=p.get('prompt','');prefix='Pay ';suffix=' to prevent your spell from being countered?'
        if (rt=='YesNoResponse' and p.get('yesAvailable') is True and prompt.startswith(prefix) and prompt.endswith(suffix)
            and ctx.get('phase')=='RESOLUTION' and not any(p.get(k) for k in ['peerOptions','yesDetail','noDetail'])
            and self._floating_payable(o,prompt[len(prefix):-len(suffix)])):
            return self._decision(p,rt,'default-use-floating-mana-to-save-spell',choice=True)
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
        discards={'Flow of Knowledge':"Choose two cards to discard",'Thirst for Discovery':'Choose 2 cards to discard, or 1 basic land card','Thirst for Knowledge':'Choose 2 cards to discard, or 1 artifact card'}
        known_discard=source in discards and p.get('prompt')==discards[source]
        if (p.get('kind') not in {'SearchLibraryDecision','SelectCardsDecision'} or rt!='CardsSelectedResponse'
            or p.get('ordered') or any(p.get(k) for k in CONSTRAINTS if k!='conditionalMinimums' or not known_discard) or p.get('nonSelectableOptions')):return None
        options=p.get('options');info=dict(o['state'].get('cards',{}))
        for entity,detail in (p.get('cardInfo') or {}).items():info[entity]={**info.get(entity,{}),**detail}
        if not isinstance(options,list) or not all(i in info and info[i].get('name') for i in options):return None
        low=p.get('minSelections');high=p.get('maxSelections')
        if not isinstance(low,int) or not isinstance(high,int):return None
        if (p.get('selectedLabel')=='Put on bottom' and p.get('remainderLabel')=='Put on top'
            and low==0 and 1<=high<=5 and p.get('prompt')==f'Scry {high}: choose cards to put on the bottom of your library'):
            bottom=[i for i in options if self._resource_value(o,info[i])<25]
            return self._decision(p,rt,'default-scry-for-curve',selectedCards=bottom[:high])
        if known_discard and 1<=low<=high<=2 and (low==high or p.get('conditionalMinimums')):
            from .selection_contracts import conditional_selection_valid
            if sum(comb(len(options),n) for n in range(low,high+1))>512:return None
            candidates=[c for n in range(low,high+1) for c in combinations(sorted(options),n) if conditional_selection_valid(c,p)]
            if not candidates:return None
            board_lands=sum('LAND' in c.get('cardTypes',[]) for c in self._own(o))
            hand_lands={i for i in ids_in(o['state'],'Hand',o['agentToAct']) if 'Land' in self._card(o,i).get('typeLine','') or 'LAND' in self._card(o,i).get('cardTypes',[])}
            # Discounted discards preserve card advantage while keeping the next
            # land drop toward ordinary four-mana development available.
            curve_target=4 if p.get('conditionalMinimums') else 5
            needed=min(len(hand_lands),max(0,curve_target-board_lands))
            # Even a low-priority card has some retention value; a discount
            # must not encourage an unnecessary extra discard of a negative score.
            values={i:max(1,self._resource_value(o,info[i])) for i in options}
            def retained_value(discard):
                return -sum(values[i] for i in discard)-500*max(0,needed-len(hand_lands-set(discard)))
            chosen=list(max(candidates,key=retained_value))
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
