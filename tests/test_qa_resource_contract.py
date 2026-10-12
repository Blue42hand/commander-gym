"""Exact QA resource authority contract; no host mutations or games."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from commander_gym import manual_runtime_qa as qa
from commander_gym.manual_runtime_profile import ManualRuntimeError
from test_manual_runtime_qa import paired_profiles

class QAResourceContractTests(unittest.TestCase):
    def test_exact_new_budget_preserves_disk_deadline_and_zero_swap(self):
        self.assertEqual(qa.LIMITS, dict(allocatedBytes=2*1024**3,
            guestMemoryBytes=12*1024**3, supervisorMemoryBytes=16*1024**3,
            swapBytes=0, cpuPercent=400, tasks=512, runtimeSeconds=900))

    def test_only_exact_new_authority_accepted(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            root=Path(folder)
            with patch.object(qa,'ROOT',root):
                p,q,ci,a=paired_profiles(root)
                self.assertFalse(qa.validate_authority(a,p,q,ci,now=1000)['activationEligible'])
                changes=[dict(guestMemoryBytes=2*1024**3,supervisorMemoryBytes=3*1024**3,cpuPercent=100,tasks=128)]
                changes += [{key:value+1} for key,value in qa.LIMITS.items()]
                changes += [{key:True} for key in qa.LIMITS]
                for delta in changes:
                    changed=copy.deepcopy(a);changed['limits'].update(delta)
                    with self.subTest(delta=delta),self.assertRaisesRegex(ManualRuntimeError,'qa_resource_limits'):
                        qa.validate_authority(changed,p,q,ci,now=1000)
