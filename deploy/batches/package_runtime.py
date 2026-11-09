#!/usr/bin/env python3
"""Copy the pinned server, replacing only its embedded Gym adapter for batch use.
The shared server and engine classes are never changed. Input adapter must have
matching engine/Gym manifest pins. Produces a private immutable runtime receipt.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import zipfile


def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adapter',required=True,type=Path);parser.add_argument('--gym-sha',required=True)
    args=parser.parse_args()
    if os.getuid()!=0:parser.error('Root required for immutable runtime preparation')
    if len(args.gym_sha)!=40 or any(c not in '0123456789abcdef' for c in args.gym_sha):parser.error('Exact Gym commit required')
    profile=json.loads(Path('/etc/argentum-luna/service-profile.json').read_text())
    server=Path(profile['artifacts']['server']['root'])/'server.jar'
    with zipfile.ZipFile(args.adapter) as adapter:
        manifest=adapter.read('META-INF/MANIFEST.MF').decode().replace('\r\n ','').replace('\n ','')
        if ('Argentum-Revision: '+profile['engineSha']) not in manifest or ('Commander-Gym-Revision: '+args.gym_sha) not in manifest:
            parser.error('Adapter manifest does not match pinned engine and Gym commits')
    root=Path('/opt/commander-gym-batch-runtime')/args.gym_sha
    if root.exists():parser.error('Runtime already exists; immutable package will not be overwritten')
    root.mkdir(mode=0o755,parents=True)
    nested='BOOT-INF/lib/commander-gym-argentum-adapter.jar'
    with zipfile.ZipFile(server) as source,zipfile.ZipFile(root/'server.jar','w') as target:
        if source.namelist().count(nested)!=1:parser.error('Expected exactly one embedded adapter')
        for info in source.infolist():
            target.writestr(info,args.adapter.read_bytes() if info.filename==nested else source.read(info.filename))
    # Prove every other archive member is byte-identical; report exact artifact hashes.
    with zipfile.ZipFile(server) as source,zipfile.ZipFile(root/'server.jar') as target:
        assert source.namelist()==target.namelist()
        assert all(source.read(n)==target.read(n) for n in source.namelist() if n!=nested)
    receipt={'engineSha':profile['engineSha'],'gymSha':args.gym_sha,'serverSha256':sha(root/'server.jar'),
             'adapterSha256':sha(args.adapter),'baseServerSha256':sha(server),'unchangedOtherArchiveMembers':True}
    (root/'receipt.json').write_text(json.dumps(receipt,indent=2));(root/'server.jar').chmod(0o444);(root/'receipt.json').chmod(0o444)
    print('Prepared isolated server with matching adapter; all other archive members unchanged.')

if __name__=='__main__':main()
