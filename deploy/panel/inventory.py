#!/usr/bin/env python3
"""Read-only byte and metadata inventory; write output outside the capture root."""
import hashlib
import json
import os
import sys
import time
from pathlib import Path
root=Path(sys.argv[1]).resolve(strict=True)
started=time.monotonic()
records={}
for directory, folders, files in os.walk(root,followlinks=False):
    folders[:]=sorted(f for f in folders if not (Path(directory)/f).is_symlink())
    for name in sorted(files):
        path=Path(directory)/name
        if path.parent==root and name.startswith('.'):
            continue  # continuously refreshed host health, not capture artifacts
        if path.is_symlink():
            raise ValueError('refuse symlink')
        info=path.stat();digest=hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
        records[str(path.relative_to(root))]={'bytes':info.st_size,'mtime_ns':info.st_mtime_ns,
            'mode':info.st_mode,'uid':info.st_uid,'gid':info.st_gid,'sha256':digest.hexdigest()}
print(json.dumps({'records':records,'seconds':round(time.monotonic()-started,3)},sort_keys=True))
