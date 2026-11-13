"""Whole native granted draws and explicit modifier certificates preserve uncertainty."""
from copy import deepcopy
import unittest
from commander_gym.heuristic_facts import CardFacts
from commander_gym.heuristic_policy import MaskedHeuristicSubsystem,draw_neutral_modifiers
from test_heuristic_policy import facts,fact,zone,decision

class GrantedDrawPolicyTests(unittest.TestCase):
    def board(self):
        doc=deepcopy(facts()._document)
        doc['cards']['Draw Granter']=fact(types=['ENCHANTMENT'],grantedFreeOptionalDraws={'native-granted-draw':1})
        f=CardFacts(doc)
        o=decision('YesNoDecision','YesNoResponse',{'choice':'BOOLEAN'},yesAvailable=True,
                   context={'phase':'RESOLUTION','sourceId':'host','sourceName':'Elf','abilityIdentity':{'abilityId':'native-granted-draw'}})
        o['state']['players'][0]['librarySize']=30
        zone(o,'Battlefield','me',host={'name':'Elf','controllerId':'me'})
        return o,f

    def test_known_native_granted_draw_is_accepted_for_its_live_host(self):
        o,f=self.board();before=deepcopy(o)
        result=MaskedHeuristicSubsystem(f).try_choose(o)
        self.assertIsNotNone(result.choice);self.assertTrue(result.choice.response['choice']);self.assertEqual(o,before)

    def test_unknown_grants_foreign_or_missing_hosts_and_empty_library_defer(self):
        for mode in ['unknown-id','foreign-host','missing-host','source-name','library','player-effect','punisher']:
            with self.subTest(mode=mode):
                o,f=self.board()
                if mode=='unknown-id':o['pendingDecision']['context']['abilityIdentity']['abilityId']='unknown'
                elif mode=='foreign-host':o['state']['cards']['host']['controllerId']='enemy'
                elif mode=='missing-host':o['state']['zones']=[]
                elif mode=='source-name':o['pendingDecision']['context']['sourceName']='Unknown'
                elif mode=='library':o['state']['players'][0]['librarySize']=1
                elif mode=='player-effect':o['state']['players'][0]['activeEffects']=['unknown']
                else:
                    doc=deepcopy(f._document);doc['cards']['Elf']['drawSensitive']=True;f=CardFacts(doc)
                self.assertIsNone(MaskedHeuristicSubsystem(f).try_choose(o).choice)

    def test_only_every_explicit_native_modifier_certificate_allows_draw(self):
        for effects,expected in [([],True),([{'drawNeutral':True}],True),([{'drawNeutral':True},{'drawNeutral':True}],True),
                                 ([{'drawNeutral':True},{}],False),([{'drawNeutral':'true'}],False),([{'drawNeutral':1}],False),
                                 ([{'drawNeutral':False}],False),(['unknown'],False),(None,False)]:
            with self.subTest(effects=effects):
                o,f=self.board();o['state']['cards']['host']['activeEffects']=effects
                self.assertEqual(draw_neutral_modifiers(o['state']['cards']['host']),expected)
                self.assertEqual(MaskedHeuristicSubsystem(f).try_choose(o).choice is not None,expected)

    def test_ambiguous_granted_ids_and_invalid_fact_types_defer_or_reject(self):
        o,f=self.board();doc=deepcopy(f._document)
        doc['cards']['Other Granter']=fact(grantedFreeOptionalDraws={'native-granted-draw':2})
        self.assertIsNone(MaskedHeuristicSubsystem(CardFacts(doc)).try_choose(o).choice)
        for value in [None,[],{'x':True},{'x':0},{'x':11},{'':1}]:
            doc=deepcopy(f._document);doc['cards']['Draw Granter']['grantedFreeOptionalDraws']=value
            with self.assertRaises(ValueError):CardFacts(doc)

class SavedNativeDrawPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gzip,json
        from pathlib import Path
        root=Path(__file__).with_name('fixtures')
        cls.facts=CardFacts(json.load(gzip.open(root/'cycle-25-native-facts.json.gz','rt')))
        cls.rows=json.load(gzip.open(root/'cycle-25-native-draw-windows.json.gz','rt'))['rows']

    def test_safe_native_draw_choices_become_local_and_managed_replacement_risk_defers(self):
        seen=[]
        for row in self.rows:
            with self.subTest(sequence=row['sequence']):
                self.assertTrue(row['evidence']['nativeMaskedMatch'])
                self.assertEqual(row['evidence']['nativeBaselineStatus'],'accepted')
                o=row['observation'];before=deepcopy(o)
                choice=MaskedHeuristicSubsystem(self.facts).try_choose(o).choice
                if row['sequence']==7781:
                    self.assertIsNone(choice)  # Laboratory Maniac on the board retains uncertainty.
                else:
                    self.assertIsNotNone(choice);self.assertTrue(choice.response['choice']);seen.append(row['sequence'])
                self.assertEqual(o,before)
        self.assertEqual(seen,[5912,9685,9713,10385,10483,10602,10798])

    def test_legacy_modifier_summaries_and_unrecognized_effects_keep_model_control(self):
        row=next(r for r in self.rows if r['sequence']==10385)
        for mode in ['missing-certificate','extra-effect']:
            o=deepcopy(row['observation'])
            cards=[c for c in o['state']['cards'].values() if c.get('activeEffects')]
            self.assertTrue(cards)
            if mode=='missing-certificate':
                for c in cards:
                    for e in c['activeEffects']:e.pop('drawNeutral',None)
            else:cards[0]['activeEffects'].append({'effectId':'unknown','description':'Targeting text cannot certify this effect'})
            self.assertIsNone(MaskedHeuristicSubsystem(self.facts).try_choose(o).choice)

    def test_saved_bad_decline_is_replaced_by_one_safe_draw(self):
        row=next(r for r in self.rows if r['sequence']==9685)
        self.assertFalse(row['choice']['response']['choice'])
        result=MaskedHeuristicSubsystem(self.facts).try_choose(row['observation'])
        self.assertTrue(result.choice.response['choice'])
