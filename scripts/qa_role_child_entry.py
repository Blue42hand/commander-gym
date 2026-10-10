"""Byte-sealed child bootstrap; only root's anonymous descriptor supplies paths."""
import json
import os
import stat
import sys
import fcntl

info = os.fstat(3)
if not (fcntl.fcntl(3,fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY
        and stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 0
        and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= 4*1024*1024):
    raise SystemExit('qa_role_descriptor')
raw = os.read(3, 4*1024*1024+1)
message = json.loads(raw)
# Root validated all source/dependency bytes and supplies no ambient paths.
paths = message['importPaths']
if not isinstance(paths,list) or not paths or not all(isinstance(p,str) and p.startswith('/opt/commander-gym-qa/artifacts/') for p in paths):
    raise SystemExit('qa_role_import_paths')
sys.path[:0] = paths
from commander_gym.manual_runtime_qa_roles import run_child
os.close(3)
run_child(message)
