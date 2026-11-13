"""Known primary-colour gap must not be overridden by an unknown strategic role."""
import gzip,json,unittest
from copy import deepcopy
from pathlib import Path
from commander_gym.heuristic_facts import CardFacts
from commander_gym.engine_deck_policy import EngineDeckPolicy,deck_profile
from commander_gym.default_policies import ProfiledDeckPolicy

class OpeningManaTenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).parent/'fixtures'
        cls.row=json.load(gzip.open(root/'cycle-10-regressions.json.gz','rt'))['41']
        cls.facts=CardFacts(json.load(gzip.open(root/'cycle-09-public-facts.json.gz','rt')))
    def state(self):return deepcopy(self.row['observation'])
    def kind(self,o,result):
        return next(a['kind'] for a in o['legalActions'] if a['actionId']==result.choice.action_id) if result.choice else None
    def test_recorded_two_plains_hand_gets_primary_colour_mulligan(self):
        for policy in [ProfiledDeckPolicy(self.facts,deck_profile('sythis')),EngineDeckPolicy(self.facts,'sythis')]:
            o=self.state();before=deepcopy(o);r=policy.try_choose(o)
            self.assertEqual(self.kind(o,r),'TakeMulligan');self.assertEqual(o,before)
            self.assertEqual(r.choice.metadata['heuristic']['reason'],'default-opening-missing-primary-mana')
    def test_unknown_definitions_possible_fixing_and_utility_lands_remain_uncertain(self):
        for variant in ['unknown','fixer','utility-land','later-mulligan']:
            o=self.state();facts=self.facts;m=o['state']['mulligan'];cs=m['cards']
            if variant=='unknown':
                doc=facts.document();doc['cards'].pop('Lignify');facts=CardFacts(doc)
            elif variant=='fixer':next(c for c in cs.values() if c['name']=='Doubling Season')['name']='Arcane Signet'
            elif variant=='utility-land':next(c for c in cs.values() if c['name']=='Plains')['name']='Myriad Landscape'
            else:m['mulliganCount']=1;m['cardsToPutOnBottom']=1
            r=EngineDeckPolicy(facts,'sythis').try_choose(o);self.assertIsNone(r.choice,variant)
    def test_profile_primary_colour_rule_is_shared_and_can_be_disabled(self):
        o=self.state();profile=deck_profile('sythis');profile['opening_colors']=['U']
        r=ProfiledDeckPolicy(self.facts,profile).try_choose(o);self.assertEqual(self.kind(o,r),'TakeMulligan')
        profile['opening_colors']=[];r=ProfiledDeckPolicy(self.facts,profile).try_choose(o);self.assertEqual(self.kind(o,r),'KeepHand')
    def test_present_primary_colour_does_not_trigger_new_mulligan_rule(self):
        o=self.state();next(c for c in o['state']['mulligan']['cards'].values() if c['name']=='Plains')['name']='Forest'
        r=EngineDeckPolicy(self.facts,'sythis').try_choose(o)
        self.assertNotEqual(r.metadata.get('heuristic',{}).get('reason'),'default-opening-missing-primary-mana')

    def test_recorded_commander_zone_and_suppression_are_explicit_in_model_summary(self):
        from commander_gym.bounded_policy_fallback import decision_context
        root=Path(__file__).parent/'fixtures';rows=json.load(gzip.open(root/'cycle-10-regressions.json.gz','rt'))
        o=deepcopy(rows['4787']['observation']);before=deepcopy(o)
        view=decision_context(o,frozen=True,policy_context={},facts=self.facts)
        commander=next(c for c in view['actingCommander'] if c['entity']=='e0')
        self.assertEqual(commander['zone'],'Battlefield');self.assertTrue(commander['printedAbilitiesRemoved'])
        self.assertEqual(o,before)

    def test_native_modal_destroy_restores_commander_before_optional_protection(self):
        root=Path(__file__).parent/'fixtures';rows=json.load(gzip.open(root/'cycle-10-regressions.json.gz','rt'))
        from commander_gym.lathril_policy import LathrilDeckSubsystem
        for seq in ['6166','6201']:
            o=deepcopy(rows[seq]['observation']);before=deepcopy(o)
            c=LathrilDeckSubsystem(self.facts).try_choose(o).choice;self.assertIsNotNone(c)
            a=next(a for a in o['legalActions'] if a['actionId']==c.action_id)
            self.assertEqual(a['kind'],'CastSpellMode');self.assertEqual(a['action']['chosenModes'],[1])
            self.assertEqual(c.params,{'targets':['e329']});self.assertEqual(o,before)
    def test_modal_rescue_requires_reviewed_contract_and_destructible_live_jail(self):
        root=Path(__file__).parent/'fixtures';o=json.load(gzip.open(root/'cycle-10-regressions.json.gz','rt'))['6166']['observation']
        from commander_gym.modal_policies import commander_rescue_mode
        action=next(a for a in o['legalActions'] if a.get('kind')=='CastSpellMode' and a['action'].get('chosenModes')==[1])
        for variant in ['unknown-effect','no-native-target','indestructible','not-disabled','extra-cost']:
            state=deepcopy(o);a=deepcopy(action)
            if variant=='unknown-effect':a['description']='Other effect'
            elif variant=='no-native-target':a['validTargets']=[]
            elif variant=='indestructible':state['state']['cards']['e329']['keywords']=['INDESTRUCTIBLE']
            elif variant=='not-disabled':state['state']['cards']['e0']['attachments']=[]
            else:a['additionalCostInfo']={'costType':'Unknown'}
            self.assertIsNone(commander_rescue_mode(state,a,self.facts),variant)

class FriendlyDevelopmentTenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).parent/'fixtures';cls.row=json.load(gzip.open(root/'cycle-10-regressions.json.gz','rt'))['sephara-development']
        cls.facts=CardFacts(json.load(gzip.open(root/'cycle-10-public-facts.json.gz','rt')))
    def state(self):return deepcopy(self.row['observation'])
    def test_actual_affordable_sephara_uses_mana_offer_without_alternative_tap_cost(self):
        o=self.state();before=deepcopy(o);c=EngineDeckPolicy(self.facts,'alela').try_choose(o).choice
        self.assertIsNotNone(c);a=next(a for a in o['legalActions'] if a['actionId']==c.action_id)
        self.assertEqual(o['state']['cards'][a['action']['cardId']]['name'],"Sephara, Sky's Blade")
        self.assertFalse(a['action']['useAlternativeCost']);self.assertEqual(c.params,{})
        self.assertEqual(o,before)
    def test_unreviewed_static_mechanics_still_defer(self):
        doc=self.facts.document();doc['cards']["Sephara, Sky's Blade"]['friendlyStaticPermanent']=False
        self.assertIsNone(EngineDeckPolicy(CardFacts(doc),'alela').try_choose(self.state()).choice)
    def test_general_policy_covers_friendly_static_creature_in_known_development_menu(self):
        from commander_gym.default_policies import DefaultPolicySubsystem
        o=self.state();o['legalActions']=[a for a in o['legalActions'] if a.get('kind')=='PassPriority' or a.get('isManaAbility') or o['state']['cards'].get(a['action'].get('cardId'),{}).get('name')=="Sephara, Sky's Blade"]
        c=DefaultPolicySubsystem(self.facts).try_choose(o).choice;self.assertIsNotNone(c)
        self.assertEqual(o['state']['cards'][next(a for a in o['legalActions'] if a['actionId']==c.action_id)['action']['cardId']]['name'],"Sephara, Sky's Blade")
    def test_complete_choice_makes_native_payment_basis_explicit(self):
        from commander_gym.bounded_policy_fallback import enumerate_choices,_candidate_description
        from commander_gym.policy_action_bounds import bounded_observation
        o=self.state();b,_=bounded_observation(o,self.facts)
        choice=next(c for c in enumerate_choices(b) if c['channel']=='action' and o['state']['cards'].get(next(a for a in o['legalActions'] if a['semanticId']==c['choice']['semanticId'])['action'].get('cardId'),{}).get('name')=="Sephara, Sky's Blade")
        details=_candidate_description(choice,b,self.facts)
        self.assertEqual(details['costBasis'],'mana cost');self.assertEqual(details['manaCost'],'{4}{W}{W}{W}')
        self.assertTrue(details['nativeAffordable']);self.assertEqual(details['parameters'],{})

class SimpleUntapTenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).parent/'fixtures';cls.row=json.load(gzip.open(root/'cycle-10-regressions.json.gz','rt'))['quiet-opponent-untap']
        cls.facts=CardFacts(json.load(gzip.open(root/'cycle-10-public-facts.json.gz','rt')))
    def state(self):return deepcopy(self.row['observation'])
    def test_actual_quiet_opponent_untap_menu_passes_locally(self):
        from commander_gym.lathril_policy import LathrilDeckSubsystem
        o=self.state();before=deepcopy(o);r=LathrilDeckSubsystem(self.facts).try_choose(o)
        self.assertIsNotNone(r.choice)
        self.assertEqual(next(a['kind'] for a in o['legalActions'] if a['actionId']==r.choice.action_id),'PassPriority');self.assertEqual(o,before)
        self.assertTrue(any(v['rule']=='untap-waits-for-development-combat-or-end' for v in r.metadata['policyRestrictions']))
    def test_untap_keeps_meaningful_main_combat_end_and_stack_windows(self):
        from commander_gym.policy_action_bounds import bounded_observation
        for variant in ['main','combat','end','stack','approved']:
            o=self.state();s=o['state'];kwargs={}
            if variant=='main':s['activePlayerId']=o['agentToAct']
            elif variant=='combat':s['currentStep']='DECLARE_ATTACKERS';s['combat']={'attackers':[{'creatureId':'e100','defendingPlayerId':o['agentToAct']}]}
            elif variant=='end':s['currentStep']='END'
            elif variant=='approved':kwargs['reaction_timing_exceptions']={'Wirewood Lodge'}
            else:
                s['cards']['response']={'name':'Unknown response'}
                next(z for z in s['zones'] if z['zoneId']['zoneType']=='Stack')['cardIds']=['response']
            b,_=bounded_observation(o,self.facts,**kwargs)
            self.assertTrue(any(a['action'].get('abilityId')=='Wirewood Lodge:2' for a in b['legalActions']),variant)
    def test_unknown_contracts_and_missing_target_metadata_do_not_manufacture_pass(self):
        from commander_gym.policy_action_bounds import bounded_observation
        o=self.state();doc=self.facts.document();doc['cards']['Wirewood Lodge'].pop('simpleTargetUntapAbilities')
        b,_=bounded_observation(o,CardFacts(doc));self.assertTrue(any(a['action'].get('abilityId')=='Wirewood Lodge:2' for a in b['legalActions']))
        for a in o['legalActions']:
            if a['action'].get('abilityId')=='Wirewood Lodge:2':a['validTargets']=None
        b,_=bounded_observation(o,self.facts);self.assertTrue(any(a['action'].get('abilityId')=='Wirewood Lodge:2' for a in b['legalActions']))
    def test_quiet_untap_excludes_known_untapped_targets_but_preserves_unknown_state(self):
        from commander_gym.policy_action_bounds import bounded_observation
        o=self.state();o['state']['currentStep']='END'
        a=next(a for a in o['legalActions'] if a['action'].get('abilityId')=='Wirewood Lodge:2')
        targets=a['validTargets']
        for t in targets:o['state']['cards'][t]['isTapped']=False
        b,_=bounded_observation(o,self.facts);self.assertFalse(any(a['action'].get('abilityId')=='Wirewood Lodge:2' for a in b['legalActions']))
        o['state']['cards'][targets[0]]['isTapped']=None
        b,_=bounded_observation(o,self.facts);kept=next(a for a in b['legalActions'] if a['action'].get('abilityId')=='Wirewood Lodge:2')
        self.assertEqual(kept['validTargets'],[targets[0]])

    def test_explicitly_empty_opponent_combat_waits_but_unknown_combat_defers(self):
        from commander_gym.policy_action_bounds import bounded_observation
        for combat,kept in [(None,False),({'attackers':[]},False),({'unknown':True},True)]:
            o=self.state();o['state']['currentStep']='DECLARE_ATTACKERS';o['state']['combat']=combat
            b,_=bounded_observation(o,self.facts)
            self.assertEqual(any(a['action'].get('abilityId')=='Wirewood Lodge:2' for a in b['legalActions']),kept)

class FixedNativeModesTenTests(unittest.TestCase):
    def state(self):
        root=Path(__file__).parent/'fixtures';return deepcopy(json.load(gzip.open(root/'cycle-10-regressions.json.gz','rt'))['6201']['observation'])
    def test_native_fixed_modes_become_complete_bounded_choices_without_card_rules(self):
        from commander_gym.bounded_policy_fallback import enumerate_choices
        o=self.state()
        # Manual mana and other mechanics may be present in the original menu;
        # test the complete native mode domain independently of those utilities.
        o['legalActions']=[a for a in o['legalActions'] if a.get('kind')=='CastSpellMode']
        for a in o['legalActions']:o['state']['cards'][a['action']['cardId']]['name']='Unreviewed modal card'
        before=deepcopy(o);choices=enumerate_choices(o);self.assertIsNotNone(choices)
        ids={a['semanticId']:a for a in o['legalActions']}
        self.assertEqual({tuple(ids[c['choice']['semanticId']]['action']['chosenModes']) for c in choices},{(0,),(1,),(2,)})
        target=next(c for c in choices if c['choice']['params']=={'targets':['e329']})
        self.assertEqual(ids[target['choice']['semanticId']]['action']['chosenModes'],[1]);self.assertEqual(o,before)
    def test_unresolved_or_multi_mode_contracts_remain_native(self):
        from commander_gym.bounded_policy_fallback import enumerate_choices
        for variant in ['multiple','unresolved','override-field','prebound-targets']:
            o=self.state();a=deepcopy(next(a for a in o['legalActions'] if a.get('kind')=='CastSpellMode'))
            if variant=='multiple':a['action']['chosenModes']=[0,1]
            elif variant=='unresolved':a['action']['chosenModes']=None
            elif variant=='override-field':a['parameterSpec']['allowedFields']['chosenModes']='INTEGER_ARRAY'
            else:a['action']['modeTargetsOrdered']=[['e329']]
            o['legalActions']=[a];self.assertIsNone(enumerate_choices(o),variant)
