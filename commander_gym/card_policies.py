"""Reviewed card mechanics shared by every deck that contains the card.

These handlers implement the card's ordinary benefit. Deck strategy enters only
through inherited scoring hooks; unknown contracts defer to bounded inference.
"""
from .heuristic_policy import ids_in, mana_cost, affordable, PolicyNotApplicable


from .resource_policies import ResourcePolicies


class SharedCardPolicies(ResourcePolicies):
    def _card_actions(self,o):
        if o.get('nativePaymentError') is not None:return None
        s=o.get('state',{});me=o['agentToAct'];offers=[a for a in o.get('legalActions',[]) if affordable(a)]
        held_equipment=set()
        quiet=not ids_in(s,'Stack');main=s.get('activePlayerId')==me and s.get('currentStep') in {'PRECOMBAT_MAIN','POSTCOMBAT_MAIN'}
        for a in offers:
            raw=a.get('action',{});ability=raw.get('abilityId');source=self._card(o,raw.get('sourceId'))
            if main and quiet and self._card(o,raw.get('cardId')).get('name') not in self.self_target_exceptions:
                planned=getattr(self,'_modal_plans',{}).get(raw.get('cardId'))
                if planned and planned.get('purpose')=='restore-disabled-commander':
                    return self._action(a,'shared-modal-complete-commander-restoration-plan',{})
                from .modal_policies import commander_rescue_mode
                rescue=commander_rescue_mode(o,a,self.facts)
                if rescue is not None:return self._action(a,'shared-modal-restore-disabled-commander',rescue)
            simple=(a.get('targetRequirements') is None and a.get('targetCount')==a.get('minTargets')==1 and not a.get('additionalCostInfo'))
            if ability=='Timberwatch Elf:1' and quiet and s.get('currentStep') in {'DECLARE_ATTACKERS','DECLARE_BLOCKERS'} and simple:
                combat=s.get('combat') or {};participants={v.get('creatureId') for kind in ['attackers','blockers'] for v in combat.get(kind,[])}
                targets=[i for i in a.get('validTargets',[]) if i in participants and self._card(o,i).get('controllerId')==me]
                if targets:
                    targets.sort(key=lambda i:(not self._card(o,i).get('isCommander'),-(self._card(o,i).get('power') or 0),i))
                    return self._action(a,'shared-card-pump-combat-participant',{'targets':[targets[0]]})
            if not main or not quiet:continue
            if ability=='Lightning Greaves:1' and simple and a.get('manaCostString')=='{0}':
                targets=[t for t in a.get('validTargets',[]) if self._card(o,t).get('controllerId')==me and self._card(o,t).get('isCommander')
                         and not {'HASTE','SHROUD'}<=set(self._card(o,t).get('keywords',[])) and raw.get('sourceId') not in self._card(o,t).get('attachments',[])]
                if targets:return self._action(a,'shared-card-protect-commander-free-equip',{'targets':[targets[0]]})
                if any(c.get('isCommander') and raw.get('sourceId') in c.get('attachments',[]) and {'HASTE','SHROUD'}<=set(c.get('keywords',[])) for c in self._own(o)):
                    held_equipment.add(a['semanticId'])
            if any(b.get('kind')=='PlayLand' or b.get('kind')=='CastSpell' and self.document['roles'].get(self._card(o,b.get('action',{}).get('cardId')).get('name')) in {'commander','draw_engine','mana_elf','mana_engine','ramp','reducer'} for b in offers):continue
            if ability=='Skullclamp:2' and simple and a.get('manaCostString')=='{1}':
                player=next((v for v in s.get('players',[]) if v['playerId']==me),{})
                if player.get('librarySize',99)<=5:continue
                targets=[]
                for t in a.get('validTargets',[]):
                    c=self._card(o,t)
                    if (c.get('controllerId')==me and (c.get('toughness') or 0)==1 and not c.get('isCommander')
                        and 'INDESTRUCTIBLE' not in c.get('keywords',[]) and raw.get('sourceId') not in c.get('attachments',[])
                        and (c.get('isToken') or c.get('name')=='Wirewood Herald')):targets.append(t)
                if hasattr(self,'_elves') and len(self._elves(o,True))>=11:targets=[]
                if targets:
                    targets.sort(key=lambda t:(self._card(o,t).get('name')!='Wirewood Herald',t))
                    return self._action(a,'shared-card-clamp-expendable-body',{'targets':[targets[0]]})
            if a.get('kind')=='CastSpell' and self._card(o,raw.get('cardId')).get('name')=='Skullclamp' and self._plain_cast(a) and not a.get('requiresTargets'):
                return self._action(a,'shared-card-develop-draw-equipment')
        # These reviewed utilities have no useful quiet action now. Holding them
        # is a covered decision; unrepresented competing mechanics still defer.
        if quiet:
            useful=[a for a in offers if a.get('kind')!='PassPriority' and not a.get('isManaAbility')]
            if useful and all(a.get('action',{}).get('abilityId') in {'Skullclamp:2','Timberwatch Elf:1'} or a['semanticId'] in held_equipment for a in useful):
                passed=self._pass(o)
                if passed:return self._action(passed,'shared-card-hold-utility-for-payoff')
        return None

    def _card_pending(self, o, p):
        resources=self._resource_pending(o,p)
        if resources is not None:return resources
        from .modal_policies import modal_pending
        modal=modal_pending(self,o,p)
        if modal is not None:return modal
        from .policy_action_bounds import positive_counter_decision
        positive=positive_counter_decision(p,self.facts)
        if positive is not None and o.get('nativePaymentError') is None and p.get('context',{}).get('sourceName') not in self.self_target_exceptions:
            targets=p['legalTargets'].get(positive)
            if isinstance(targets,list) and len(targets)==1 and self._card(o,targets[0]).get('controllerId')==o['agentToAct'] and 'CREATURE' in self._card(o,targets[0]).get('cardTypes',[]):
                return self._decision(p,p['responseSpec']['responseType'],'shared-positive-counter-single-own-target',selectedTargets={positive:targets})
        from .policy_action_bounds import copied_neutralization_targets
        copy=copied_neutralization_targets(o,p,self.facts)
        if copy and p.get('context',{}).get('sourceName') not in self.self_target_exceptions and len(copy[1])==1:
            return self._decision(p,p['responseSpec']['responseType'],'shared-card-copy-neutralize-distinct-live-threat',selectedTargets={copy[0]:copy[1]})
        from .policy_action_bounds import reviewed_exile_decision,fixed_stat_reduction
        reduction=fixed_stat_reduction(p,self.facts)
        if reviewed_exile_decision(p) or reduction:
            slot=str(p['targetRequirements'][0]['index']);targets=p['legalTargets'].get(slot)
            if isinstance(targets,list) and len(targets)==1:
                c=self._card(o,targets[0])
                if not reduction or (c.get('controllerId') not in (None,o['agentToAct']) and type(c.get('toughness')) is int and c['toughness']+reduction['toughness']<=0 and not c.get('activeEffects')):
                    return self._decision(p,p['responseSpec']['responseType'],'shared-card-clear-removal-target',selectedTargets={slot:targets})
        context = p.get('context', {}); source = context.get('sourceName')
        rt = p.get('responseSpec', {}).get('responseType')
        optional_benefits={
            'Beastmaster Ascension':('Beastmaster Ascension:1','You may put a quest counter on Beastmaster Ascension'),
            'Wirewood Herald':('Wirewood Herald:1','You may search your library for an Elf card, reveal it, put it into your hand, then shuffle'),
        }
        if (source in optional_benefits and rt=='YesNoResponse' and context.get('phase')=='RESOLUTION'
            and (context.get('abilityIdentity') or {}).get('abilityId')==optional_benefits[source][0]
            and p.get('prompt')==optional_benefits[source][1] and not any(p.get(k) for k in ['peerOptions','yesDetail','noDetail'])):
            return self._decision(p,rt,'shared-card-accept-free-development',choice=p.get('yesAvailable') is True)
        if (source=='Land Tax' and rt=='YesNoResponse' and context.get('phase')=='RESOLUTION'
            and (context.get('abilityIdentity') or {}).get('abilityId')=='Land Tax:1'
            and not any(p.get(k) for k in ['peerOptions','yesDetail','noDetail'])):
            return self._decision(p,rt,'shared-card-accept-basic-land-search',choice=p.get('yesAvailable') is True)
        if (source == 'Reclamation Sage' and rt == 'YesNoResponse'
            and context.get('phase') == 'RESOLUTION'
            and (context.get('abilityIdentity') or {}).get('abilityId') == 'Reclamation Sage:1'
            and p.get('prompt') == 'You may destroy target artifact or enchantment'
            and not any(p.get(k) for k in ['peerOptions','yesDetail','noDetail'])):
            targets = context.get('targetIds')
            if isinstance(targets,list) and len(targets)==1:
                controller = self._card(o,targets[0]).get('controllerId')
                if controller is not None:
                    return self._decision(p,rt,'shared-card-decline-self-removal',choice=p.get('yesAvailable') is True and controller != o['agentToAct'])
        if (source == 'Felidar Retreat' and rt == 'OptionChosenResponse'
            and context.get('phase') == 'TRIGGER' and p.get('options') == [
                'Create a 2/2 white Cat Beast creature token',
                'Put a +1/+1 counter on each creature you control. Those creatures gain vigilance until end of turn']):
            creatures = [c for c in self._own(o) if 'CREATURE' in c.get('cardTypes',[]) or 'Creature' in c.get('typeLine','')]
            # Establish a board first, then strengthen a developed team.
            return self._decision(p, rt, 'shared-card-landfall-board-development', optionIndex=1 if len(creatures)>=3 else 0)
        free_tokens = {
            'Wirewood Hivemaster': 'You may create a 1/1 green Insect creature token',
            'Lys Alana Huntmaster': 'You may create a 1/1 green Elf Warrior creature token',
        }
        if (p.get('kind') == 'YesNoDecision' and rt == 'YesNoResponse' and source in free_tokens
            and (context.get('abilityIdentity') or {}).get('abilityId') == source + ':1'
            and context.get('phase') == 'RESOLUTION'
            and p.get('prompt', '').rstrip('.').casefold() == free_tokens[source].casefold()
            and not any(p.get(k) for k in ['peerOptions', 'yesDetail', 'noDetail'])):
            return self._decision(p, rt, 'shared-card-accept-free-token', choice=p.get('yesAvailable') is True)
        if (p.get('kind') == 'YesNoDecision' and rt == 'YesNoResponse' and (source == 'Overgrown Tomb' or self.facts.get(source).get('untappedPayLife') == 2)
            and p.get('prompt') == f'Pay 2 life to have {source} enter untapped?'):
            board = [self._card(o, i) for i in ids_in(o['state'], 'Battlefield', o['agentToAct']) if i != context.get('sourceId')]
            sources = [self._land_colors(c) for c in board if not c.get('isTapped') and self._land_colors(c)]
            life = next((x.get('life', 0) for x in o['state'].get('players', []) if x.get('playerId') == o['agentToAct']), 0)
            unlocks = False
            for i in ids_in(o['state'], 'Hand', o['agentToAct']):
                card = self._card(o, i)
                if self.document['roles'].get(card.get('name')) not in self.document['weights'] and card.get('name') != 'Demonic Tutor':
                    continue
                cost = mana_cost(card.get('manaCost', self.facts.get(card.get('name')).get('manaCost', '')))
                if cost and cost[0] > 0 and self._payable(cost, sources + [self._land_colors({'name':source},o)]) and not self._payable(cost, sources):
                    unlocks = True
            return self._decision(p, rt, 'shared-card-pay-life-for-development', choice=p.get('yesAvailable') is True and life > 4 and unlocks)
        if p.get('kind') == 'YesNoDecision' and source == 'Leaf-Crowned Visionary' and (context.get('abilityIdentity') or {}).get('abilityId') == 'Leaf-Crowned Visionary:1':
            me = next((x for x in o['state'].get('players', []) if x.get('playerId') == o['agentToAct']), {})
            return self._decision(p, rt, 'shared-card-pay-for-cards', choice=p.get('yesAvailable') is True and me.get('librarySize', 0) > 3)
        if rt == 'ManaSourcesSelectedResponse' and source == 'Leaf-Crowned Visionary' and p.get('requiredCost') == '{G}':
            available = {a['entityId'] for a in p.get('availableSources', []) if not a.get('requiresSacrifice') and not a.get('requiresTappingAnotherPermanent')}
            suggestion = p.get('autoPaySuggestion', [])
            if suggestion and len(suggestion) == len(set(suggestion)) and set(suggestion) <= available:
                return self._decision(p, rt, 'shared-card-pay-native-suggestion', autoPay=False, declined=False, selectedSources=suggestion, waterbendPermanents=[])
            if p.get('canAutoPayNow') is True:
                return self._decision(p, rt, 'shared-card-native-autopay', autoPay=True, declined=False, selectedSources=[], waterbendPermanents=[])
            if p.get('canDecline') is True:
                return self._decision(p, rt, 'shared-card-decline-unfunded-draw', autoPay=False, declined=True, selectedSources=[], waterbendPermanents=[])
        if (p.get('kind') == 'SelectCardsDecision' and rt == 'CardsSelectedResponse'
            and ((source == 'Growing Rites of Itlimoc'
                  and p.get('prompt') == 'You may reveal a creature card and put it into your hand')
                 or (source == "Herald's Horn" and p.get('selectedLabel') == 'Reveal and put into your hand'
                     and p.get('remainderLabel') == 'Leave on top of your library'))
            and p.get('minSelections') == 0 and p.get('maxSelections') == 1
            and not any(p.get(k) for k in ['selectionLimits', 'conditionalMinimums', 'onePerCardType',
                'onePerColor', 'onePerCardName', 'onePerBasicLandType', 'onePerPower',
                'maxTotalManaValue', 'minTotalManaValue', 'maxTotalPower'])):
            candidates = [i for i in p.get('options', []) if i not in p.get('nonSelectableOptions', [])]
            info = p.get('cardInfo') or {}
            if any(i not in info or 'Creature' not in info[i].get('typeLine', '') for i in candidates):
                raise PolicyNotApplicable('shared-card-incomplete-revealed-creatures')
            def reveal_value(entity):
                card = info[entity]; role = self.document['roles'].get(card.get('name'))
                return self._development_score(o, card, role) if role in self.document['weights'] else self._opening_value(card)
            chosen = sorted(candidates, key=lambda i: (-reveal_value(i), i))[:1]
            return self._decision(p, rt, 'shared-card-reveal-development-creature', selectedCards=chosen)
        if p.get('kind') in {'SearchLibraryDecision', 'SelectCardsDecision'} and p.get('minSelections') in {0, 1} and p.get('maxSelections') == 1 and source in {'Demonic Tutor', 'Elvish Harbinger', 'Wood Elves', 'Sylvan Messenger', 'Collected Company', 'Sterling Grove', "Green Sun's Zenith", "Chord of Calling"}:
            if any(p.get(k) for k in ['selectionLimits', 'conditionalMinimums', 'onePerCardType', 'onePerColor', 'onePerCardName', 'onePerBasicLandType', 'onePerPower', 'maxTotalManaValue', 'minTotalManaValue', 'maxTotalPower']):
                raise PolicyNotApplicable('shared-card-unsupported-search-constraints')
            def search_score(entity):
                card = (p.get('cardInfo') or {}).get(entity, self._card(o, entity))
                if source == 'Sterling Grove':
                    from .policy_action_bounds import tutor_payoff_score
                    return tutor_payoff_score(card.get('name'),self.facts, {n for n,role in self.document['roles'].items() if role=='draw_engine'})
                return self._search_score(o, p, card)
            candidates = [(search_score(i), i) for i in p.get('options', []) if i not in p.get('nonSelectableOptions', [])]
            # Library order never enters scoring; options are native permitted choices.
            if candidates:
                target = max(candidates, key=lambda x: (x[0], x[1]))[1]
                return self._decision(p, rt, 'shared-card-search-for-engine', selectedCards=[target])
            if p.get('minSelections') == 0:
                return self._decision(p, rt, 'shared-card-search-no-eligible-card', selectedCards=[])
        return None
