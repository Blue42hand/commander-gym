"""Verify downloaded matched CI bytes offline; never extract, execute, or approve."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import zipfile

from .manual_runtime_profile import ManualRuntimeError, decode, require
from .release_cadence import pinned_json

MAX_BYTES = 1024**3
MAX_FILES = 10000


def stamp(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def safe_name(name):
    require(type(name) is str and name and '\\' not in name and '\x00' not in name
            and not PurePosixPath(name).is_absolute()
            and all(part not in ('', '.', '..') for part in name.split('/')), 'artifact_path')
    return name


def verify_archive(path, candidate):
    require(candidate['schemaVersion'] == 1 and candidate['state'] == 'source-evidence-collected'
            and candidate['promotionEnabled'] is False, 'artifact_source_evidence')
    artifact = candidate['matchedArtifact']
    path = Path(path)
    require(not path.is_symlink(), 'artifact_symlink')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_size == artifact['bytes']
                and 0 < before.st_size <= MAX_BYTES, 'artifact_archive_size')
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        require(actual == artifact['sha256'], 'artifact_archive_digest')
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            infos = archive.infolist()
            require(0 < len(infos) <= MAX_FILES, 'artifact_file_count')
            files, names, total = {}, set(), 0
            for info in infos:
                name = safe_name(info.filename.rstrip('/') if info.is_dir() else info.filename)
                require(name not in names, 'artifact_duplicate_path')
                names.add(name)
                mode = info.external_attr >> 16
                require(stat.S_IFMT(mode) in (0, stat.S_IFREG, stat.S_IFDIR)
                        and not info.flag_bits & 1, 'artifact_special_file')
                if info.is_dir():
                    require(info.file_size == 0, 'artifact_directory_size')
                    continue
                require(stat.S_IFMT(mode) in (0, stat.S_IFREG), 'artifact_special_file')
                total += info.file_size
                require(total <= MAX_BYTES, 'artifact_expanded_size')
                files[name] = info
            require('matched-release.json' in files
                    and files['matched-release.json'].file_size <= 4 * 1024 * 1024,
                    'artifact_manifest_missing_or_size')
            manifest = decode(archive.read('matched-release.json'))
            require(set(manifest) == {'schemaVersion', 'engineSha', 'gymSha', 'files',
                                     'deploymentAuthorized', 'privateHostBundleRequired'}
                    and type(manifest['schemaVersion']) is int and manifest['schemaVersion'] == 1
                    and manifest['deploymentAuthorized'] is False
                    and manifest['privateHostBundleRequired'] is True
                    and manifest['engineSha'] == candidate['sources']['engine']['sha']
                    and manifest['gymSha'] == candidate['sources']['gym']['sha'], 'artifact_manifest_identity')
            inventory = manifest['files']
            require(type(inventory) is dict and set(inventory) == set(files) - {'matched-release.json'}
                    and {'server.jar', 'adapter.jar', 'frontend/index.html', 'manual-luna-release.json',
                         'canonical-v2-fixture.json'} <= set(inventory), 'artifact_inventory')
            for name, record in inventory.items():
                safe_name(name)
                require(set(record) == {'sha256', 'bytes'} and type(record['bytes']) is int
                        and files[name].file_size == record['bytes'], 'artifact_file_size')
                with archive.open(name) as source:
                    require(hashlib.file_digest(source, 'sha256').hexdigest() == record['sha256'],
                            'artifact_file_digest')
            require(files['manual-luna-release.json'].file_size <= 1024 * 1024, 'artifact_receipt_size')
            receipt = decode(archive.read('manual-luna-release.json'))
            require(receipt['nativeSha'] == manifest['engineSha']
                    and receipt['gymSha'] == manifest['gymSha']
                    and receipt['serverSha256'] == inventory['server.jar']['sha256']
                    and receipt['adapterSha256'] == inventory['adapter.jar']['sha256']
                    and receipt['deploymentAuthorized'] is False
                    and type(receipt['providerCallsMade']) is int and receipt['providerCallsMade'] == 0
                    and receipt['qaCallbackGatePackaged'] is True
                    and receipt['qaRedisVariantPackaged'] is True, 'artifact_receipt_identity')
        require(stamp(before) == stamp(os.fstat(stream.fileno()))
                and stamp(before) == stamp(path.lstat()), 'artifact_changed_during_verification')
    return {'schemaVersion': 1, 'state': 'matched-artifact-bytes-verified', 'promotionEnabled': False,
            'archiveSha256': actual, 'engineSha': manifest['engineSha'], 'gymSha': manifest['gymSha'],
            'artifactId': artifact['id'], 'runId': artifact['runId'], 'runAttempt': artifact['runAttempt'],
            'fileCount': len(inventory), 'expandedBytes': total,
            'remainingGates': ['independent-source-review', 'native-private-catalog-validation',
                               'fresh-runtime-qualification', 'exact-owner-approval']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', required=True)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--candidate-sha256', required=True)
    args = parser.parse_args(argv)
    try:
        result = verify_archive(args.archive, pinned_json(args.candidate, args.candidate_sha256))
    except ManualRuntimeError as error:
        result = {'state': 'held-artifact-evidence', 'reason': str(error), 'promotionEnabled': False}
    except (KeyError, TypeError, ValueError, OSError, zipfile.BadZipFile, RuntimeError):
        result = {'state': 'held-artifact-evidence', 'reason': 'artifact_input_invalid', 'promotionEnabled': False}
    print(json.dumps(result, sort_keys=True))
    return 0 if result['state'] == 'matched-artifact-bytes-verified' else 1


if __name__ == '__main__':
    raise SystemExit(main())
