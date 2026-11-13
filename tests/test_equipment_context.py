"""Actual game25 regressions for basic state evidence in bounded inference."""
from copy import deepcopy
import gzip
import json
from pathlib import Path
import unittest
from commander_gym.bounded_policy_fallback import decision_context, _candidate_description
from commander_gym.heuristic_facts import CardFacts

class EquipmentContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows=json.load(gzip.open(Path(__file__).with_name('fixtures')/'cycle-19-equipment.json.gz','rt'))['rows']
        # The existing committed native-facts fixture includes the exact native
        # equip flag; no Oracle-text classification is used here.
        old=json.load(gzip.open(Path(__file__).with_name('fixtures')/'cycle-16-equip.json.gz','rt'))
        cls.facts=CardFacts(old['cardFacts'])

    def test_actual_non_sick_creatures_remain_explicitly_non_sick(self):
        for row in self.rows:
            if row['sequence'] not in {7235,7270,7305,7403}:continue
            with self.subTest(sequence=row['sequence']):
                o=row['observation'];before=deepcopy(o)
                v=decision_context(o,frozen=True,policy_context=None,facts=self.facts)
                for entity in ['e300','e414']:
                    self.assertIs(o['state']['cards'][entity]['hasSummoningSickness'],False)
                    self.assertIs(v['cards'][entity]['hasSummoningSickness'],False)
                    self.assertIs(v['cards'][entity]['isTapped'],False)
                self.assertEqual(o,before)

    def test_actual_equip_choices_show_existing_host_and_target_status(self):
        hosts={7235:'e414',7270:'e300',7305:'e414',7403:'e300'}
        for row in self.rows:
            if row['sequence'] not in hosts:continue
            with self.subTest(sequence=row['sequence']):
                o=row['observation'];c=row['recordedChoice'];a=next(a for a in o['legalActions'] if a['actionId']==c['actionId'])
                wire={'channel':'action','choice':{'semanticId':a['semanticId'],'params':c['params']}}
                view=_candidate_description(wire,o,self.facts)
                self.assertEqual(view['equipmentTransfer']['currentHosts'],[hosts[row['sequence']]])
                self.assertIs(view['equipmentTransfer']['previousHostLosesEquipmentBenefits'],True)
                self.assertIs(view['targetFacts'][0]['hasSummoningSickness'],False)
                self.assertIs(view['targetFacts'][0]['isTapped'],False)

    def test_missing_status_stays_unknown(self):
        o=deepcopy(self.rows[0]['observation']);o['state']['cards']['e300'].pop('hasSummoningSickness')
        v=decision_context(o,frozen=False,policy_context=None,facts=self.facts)
        self.assertNotIn('hasSummoningSickness',v['cards']['e300'])
