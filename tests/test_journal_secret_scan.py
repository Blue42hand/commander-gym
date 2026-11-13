"""Credential rejection must remain complete on large and non-dict mappings."""
from collections import UserDict
from types import MappingProxyType
import unittest
from commander_gym.game_journal import _safe,JournalError

class JournalSecretScanTests(unittest.TestCase):
    def test_all_nested_transport_names_and_secret_values_are_rejected(self):
        for key in ['api.KEY','h.e.A.d_E-r-S','other_credentials','AUTHORIZATION']:
            for mapping in [dict,UserDict,MappingProxyType]:
                with self.subTest(key=key,mapping=mapping):
                    with self.assertRaises(JournalError):_safe({'cards':[mapping({key:'sensitive'})]})
        with self.assertRaises(JournalError):_safe({'cards':[{'oracleText':'prefix sk-proj-'+('x'*24)}]})

    def test_late_malicious_field_is_not_skipped_in_large_checkpoints(self):
        fields={f'entity-{i}':{'power':1,'toughness':2} for i in range(6000)}
        fields['last']={'nested':{'model-api-key':'sensitive'}}
        with self.assertRaises(JournalError):_safe(fields)

    def test_ordinary_state_fields_and_mappings_are_preserved(self):
        value={'cards':[UserDict({'name':'Island','power':None}),MappingProxyType({'color':'U'})],
               'counts':(0,1,False),'oracleText':'A harmless card description sk-short.'}
        before=repr(value);_safe(value);self.assertEqual(repr(value),before)
