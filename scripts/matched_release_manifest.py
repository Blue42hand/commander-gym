"""Inventory public matched CI binaries; no host configuration or private catalogs."""
import argparse
import hashlib
import json
from pathlib import Path
import re


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--engine-sha',required=True);parser.add_argument('--gym-sha',required=True)
    args=parser.parse_args()
    if any(re.fullmatch('[0-9a-f]{40}',sha) is None for sha in (args.engine_sha,args.gym_sha)):
        raise ValueError('exact source pins required')
    inspection=json.loads((args.root/'manual-luna-release.json').read_bytes())
    if inspection['nativeSha']!=args.engine_sha or inspection['gymSha']!=args.gym_sha:
        raise ValueError('matched inspection differs')
    files={}
    for path in sorted(args.root.rglob('*')):
        if path.is_symlink():raise ValueError('artifact symlink')
        if path.is_file():
            with path.open('rb') as stream:sha=hashlib.file_digest(stream,'sha256').hexdigest()
            files[path.relative_to(args.root).as_posix()]={'sha256':sha,'bytes':path.stat().st_size}
    if not {'server.jar','adapter.jar','frontend/index.html'}.issubset(files):raise ValueError('incomplete matched release')
    manifest={'schemaVersion':1,'engineSha':args.engine_sha,'gymSha':args.gym_sha,'files':files,
              'deploymentAuthorized':False,'privateHostBundleRequired':True}
    path=args.root/'matched-release.json'
    with path.open('x') as stream:json.dump(manifest,stream,sort_keys=True,separators=(',',':'))


if __name__=='__main__':main()
