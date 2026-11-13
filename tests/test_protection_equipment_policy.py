"""General equipment bounds, with real game25 observations and typed facts."""
from copy import deepcopy
import gzip,json,unittest
from pathlib import Path
from commander_gym.heuristic_facts import CardFacts
from commander_gym.policy_action_bounds import bounded_observation

class ProtectionEquipmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).with_name('fixtures')
        cls.rows={x['sequence']:x for x in json.load(gzip.open(root/'cycle-19-equipment.json.gz','rt'))['rows']}
        cls.doc=json.load(gzip.open(root/'cycle-19-protection-facts.json.gz','rt'))

    def case(self,seq=7270):
        o=deepcopy(self.rows[seq]['observation']);doc=deepcopy(self.doc)
        # Actual native e990 export, qualified by whole-shape JVM tests.
        return o,CardFacts(doc)

    def test_actual_commander_to_non_sick_token_transfers_are_excluded(self):
        for seq in [7270,7403]:
            o,f=self.case(seq);before=deepcopy(o);v,r=bounded_observation(o,f)
            a=next((a for a in v['legalActions'] if a['actionId']==self.rows[seq]['recordedChoice']['actionId']),None)
            self.assertTrue(a is None or 'e414' not in a['validTargets'])
            self.assertTrue(any(x['rule']=='preserve-engine-protection-over-non-sick-token' for x in r));self.assertEqual(o,before)

    def test_actual_transfers_to_commander_remain_available(self):
        for seq in [7235,7305]:
            o,f=self.case(seq);v,_=bounded_observation(o,f)
            a=next(a for a in v['legalActions'] if a['actionId']==self.rows[seq]['recordedChoice']['actionId'])
            self.assertIn('e300',a['validTargets'])

    def test_useful_haste_unknown_shapes_and_deck_overrides_retain_model_choice(self):
        for mode in ['large-token','tapped-host','postcombat','sick','unknown-sickness','modified-token','text-token','counter-token','response','opponent-turn','modified-host','unknown-contract','deck-timing','deck-target','pending','payment']:
            with self.subTest(mode=mode):
                o,f=self.case();kwargs={}
                if mode=='large-token':o['state']['cards']['e414']['power']=20
                elif mode=='tapped-host':o['state']['cards']['e300']['isTapped']=True
                elif mode=='postcombat':o['state']['currentStep']='POSTCOMBAT_MAIN'
                elif mode=='sick':o['state']['cards']['e414']['hasSummoningSickness']=True
                elif mode=='unknown-sickness':o['state']['cards']['e414'].pop('hasSummoningSickness')
                elif mode=='modified-token':o['state']['cards']['e414']['activeEffects']=['unknown']
                elif mode=='text-token':o['state']['cards']['e414']['oracleText']='Whenever this attacks, draw a card.'
                elif mode=='counter-token':o['state']['cards']['e414']['counters']={'+1/+1':3}
                elif mode=='response':o['state']['currentStep']='DECLARE_BLOCKERS'
                elif mode=='opponent-turn':o['state']['activePlayerId']='policy-talrand'
                elif mode=='modified-host':o['state']['cards']['e300']['activeEffects']=['unknown']
                elif mode=='unknown-contract':doc=deepcopy(f._document);doc['cards']['Swiftfoot Boots'].pop('protectionEquipmentKeywords');f=CardFacts(doc)
                elif mode=='deck-timing':kwargs['reaction_timing_exceptions']={'Swiftfoot Boots'}
                elif mode=='deck-target':kwargs['allow_self_targets']={'Swiftfoot Boots'}
                elif mode=='pending':o['pendingDecision']={'type':'Unreviewed'}
                elif mode=='payment':o['nativePaymentError']='retry'
                v,_=bounded_observation(o,f,**kwargs)
                a=next(a for a in v['legalActions'] if a['actionId']==self.rows[7270]['recordedChoice']['actionId'])
                self.assertIn('e414',a['validTargets'])

    def test_malformed_native_contracts_rejected(self):
        for value in [None,{},[],['HASTE'],['UNBLOCKABLE'],['HEXPROOF','HEXPROOF'],[['HEXPROOF']]]:
            with self.subTest(value=value):
                doc=deepcopy(self.doc);doc['cards']['Swiftfoot Boots']['protectionEquipmentKeywords']=value
                with self.assertRaises(ValueError):CardFacts(doc)

    def test_shroud_only_and_haste_shroud_contracts_are_supported(self):
        for value in [['SHROUD'],['HASTE','SHROUD']]:
            doc=deepcopy(self.doc);doc['cards']['Swiftfoot Boots']['protectionEquipmentKeywords']=value
            self.assertEqual(CardFacts(doc).get('Swiftfoot Boots')['protectionEquipmentKeywords'],value)
