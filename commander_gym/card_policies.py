"""Reviewed card mechanics shared by every deck that contains the card.

These handlers implement the card's ordinary benefit. Deck strategy enters only
through inherited scoring hooks; unknown contracts defer to bounded inference.
"""
from .heuristic_policy import ids_in, mana_cost, PolicyNotApplicable


class SharedCardPolicies:
    def _card_pending(self, o, p):
        context = p.get('context', {}); source = context.get('sourceName')
        rt = p.get('responseSpec', {}).get('responseType')
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
        if (p.get('kind') == 'YesNoDecision' and rt == 'YesNoResponse' and source == 'Overgrown Tomb'
            and p.get('prompt') == 'Pay 2 life to have Overgrown Tomb enter untapped?'):
            board = [self._card(o, i) for i in ids_in(o['state'], 'Battlefield', o['agentToAct']) if i != context.get('sourceId')]
            sources = [self._land_colors(c) for c in board if not c.get('isTapped') and self._land_colors(c)]
            life = next((x.get('life', 0) for x in o['state'].get('players', []) if x.get('playerId') == o['agentToAct']), 0)
            unlocks = False
            for i in ids_in(o['state'], 'Hand', o['agentToAct']):
                card = self._card(o, i)
                if self.document['roles'].get(card.get('name')) not in self.document['weights'] and card.get('name') != 'Demonic Tutor':
                    continue
                cost = mana_cost(card.get('manaCost', self.facts.get(card.get('name')).get('manaCost', '')))
                if cost and cost[0] > 0 and self._payable(cost, sources + [{'G', 'B'}]) and not self._payable(cost, sources):
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
        if p.get('kind') in {'SearchLibraryDecision', 'SelectCardsDecision'} and p.get('minSelections') in {0, 1} and p.get('maxSelections') == 1 and source in {'Demonic Tutor', 'Elvish Harbinger', 'Wood Elves', 'Sylvan Messenger', 'Collected Company'}:
            if any(p.get(k) for k in ['selectionLimits', 'conditionalMinimums', 'onePerCardType', 'onePerColor', 'onePerCardName', 'onePerBasicLandType', 'onePerPower', 'maxTotalManaValue', 'minTotalManaValue', 'maxTotalPower']):
                raise PolicyNotApplicable('shared-card-unsupported-search-constraints')
            def search_score(entity):
                card = (p.get('cardInfo') or {}).get(entity, self._card(o, entity))
                return self._search_score(o, p, card)
            candidates = [(search_score(i), i) for i in p.get('options', []) if i not in p.get('nonSelectableOptions', [])]
            # Library order never enters scoring; options are native permitted choices.
            if candidates:
                target = max(candidates, key=lambda x: (x[0], x[1]))[1]
                return self._decision(p, rt, 'shared-card-search-for-engine', selectedCards=[target])
            if p.get('minSelections') == 0:
                return self._decision(p, rt, 'shared-card-search-no-eligible-card', selectedCards=[])
        return None
