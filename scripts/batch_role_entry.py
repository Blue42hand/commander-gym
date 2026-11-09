"""Minimal sealed-interpreter entry; configured paths are root-owned."""
import json
from pathlib import Path
import sys
config=json.loads(Path('/config/role.json').read_text())
sys.path[:0]=['/app',config['python_packages']]
from commander_gym.batch_roles import main
main()
