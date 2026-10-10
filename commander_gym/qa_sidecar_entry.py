"""Fixed root entry for the disposable QA sidecar unit."""
import sys

if __package__ in (None, ''):
    sys.path.insert(0, '/opt/commander-gym-qa/implementation')
from commander_gym.manual_runtime_qa_roles import root_main

if __name__ == '__main__':
    root_main('sidecar')
