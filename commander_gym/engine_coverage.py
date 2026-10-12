"""Offline, content-addressed CardRegistry evidence; never gameplay certification.

Registry exports come from Argentum's existing commanderGymDeckCoverage task.
Qualification and deployment receipts are operator-reviewed public evidence,
not network checks performed by this module. No host access or engine build.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

from .card_catalog import CardCatalog, CatalogError


MAX_EVIDENCE_BYTES = 16_000_000
MAX_REGISTRY_NAMES = 100_000
HEX_SHA = re.compile(r"[0-9a-f]{40}\Z")
EVIDENCE_ID = re.compile(r"[0-9a-f]{64}\Z")
REPOSITORY = "https://github.com/Blue42hand/argentum-engine.git"
NOTE = ("Registry presence is evidence of registration in the pinned engine, "
        "not evidence of gameplay correctness or full card behavior. "
        "Ordinary paper-deck discovery is unrestricted unless explicitly filtered.")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf-8')


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(path: Path, maximum: int = MAX_EVIDENCE_BYTES) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise CatalogError('coverage evidence unavailable')
    try:
        with path.open('rb') as stream:
            data = stream.read(maximum + 1)
    except OSError as exc:
        raise CatalogError('coverage evidence unavailable') from exc
    if len(data) > maximum:
        raise CatalogError('coverage evidence too large')
    return data


def _json(data: bytes) -> dict:
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError) as exc:
        raise CatalogError('invalid coverage JSON') from exc
    if not isinstance(value, dict):
        raise CatalogError('coverage JSON must be an object')
    return value


def _identities(catalog: CardCatalog) -> dict[str, list[str]]:
    """Exact catalog names, including only faces belonging to this Oracle ID."""
    with catalog._connect() as db:
        names = {r['oracle_id']: {r['name']} for r in db.execute(
            'SELECT oracle_id, name FROM cards WHERE snapshot_id=? ORDER BY oracle_id',
            (catalog.snapshot_id,))}
        for r in db.execute('SELECT oracle_id, name FROM card_faces WHERE snapshot_id=? '
                            'AND (face_oracle_id IS NULL OR face_oracle_id=oracle_id)',
                            (catalog.snapshot_id,)):
            names[r['oracle_id']].add(r['name'])
    return {key: sorted(value) for key, value in sorted(names.items())}



MAPPING_V2 = 'canonical_card_or_explicit_front_face_v2'


def _identity_roles(catalog: CardCatalog) -> dict:
    """Keep whole names, explicit layout and face ownership distinct."""
    with catalog._connect() as db:
        has_layout = any(r['name'] == 'authoring_json' for r in
                         db.execute("PRAGMA table_info('printings')"))
        identities = {}
        sql = ('SELECT c.oracle_id, c.name, p.authoring_json FROM cards c LEFT JOIN printings p '
               'ON p.snapshot_id=c.snapshot_id AND p.oracle_id=c.oracle_id AND p.printing_id=c.printing_id '
               'WHERE c.snapshot_id=? ORDER BY c.oracle_id') if has_layout else (
               'SELECT oracle_id, name, NULL AS authoring_json FROM cards WHERE snapshot_id=? ORDER BY oracle_id')
        for row in db.execute(sql, (catalog.snapshot_id,)):
            layout, layout_hash = None, None
            raw = row['authoring_json']
            if raw is not None:
                layout_hash = _hash(raw.encode('utf-8'))
                try:
                    details = json.loads(raw)
                    value = details.get('layout') if isinstance(details, dict) else None
                    layout = value if isinstance(value, str) else None
                except (ValueError, TypeError):
                    layout = None
            identities[row['oracle_id']] = {'name': row['name'], 'layout': layout,
                                             'layout_source_sha256': layout_hash, 'faces': []}
        for row in db.execute('SELECT oracle_id, face_index, face_oracle_id, name FROM card_faces '
                              'WHERE snapshot_id=? ORDER BY oracle_id, face_index', (catalog.snapshot_id,)):
            identities[row['oracle_id']]['faces'].append({
                'index': row['face_index'], 'oracle_id': row['face_oracle_id'], 'name': row['name']})
    return identities


def _registry_names(raw: bytes) -> list[str]:
    try:
        names = raw.decode('utf-8').splitlines()
    except UnicodeError as exc:
        raise CatalogError('registry names must be UTF-8') from exc
    if (not names or len(names) > MAX_REGISTRY_NAMES or names != sorted(set(names)) or
            any(not name or name != name.strip() or len(name) > 512 for name in names)):
        raise CatalogError('registry export must be complete, sorted and unique')
    return names


def _role_entries(identities: dict, names: list[str]) -> tuple[dict, int]:
    """Canonical exact ownership precedes explicitly evidenced front normalization."""
    canonical, fronts, aliases = {}, {}, {}
    for key, identity in identities.items():
        canonical.setdefault(identity['name'], []).append(key)
        own = [f for f in identity['faces'] if f['oracle_id'] in (None, key)]
        for face in own:
            aliases.setdefault(face['name'], []).append(key)
        if (identity['layout'] in ('transform', 'modal_dfc') and len(identity['faces']) == 2 and len(own) == 2 and
                [f['index'] for f in own] == [0, 1] and
                identity['name'] == ' // '.join(f['name'] for f in own)):
            fronts.setdefault(own[0]['name'], []).append(key)
    registered = set(names)

    def candidates(ids, kind):
        return [{'oracle_id': k, 'canonical_name': identities[k]['name'], 'match_kind': kind}
                for k in sorted(set(ids))]

    entries = {}
    for key, identity in identities.items():
        name = identity['name']
        own_names = {f['name'] for f in identity['faces'] if f['oracle_id'] in (None, key)}
        matches = sorted(({name} | own_names) & registered)
        presence, reason, kind, details = 'absent', 'not_in_complete_export', 'none', []
        if name in registered:
            matches = [name]
            if len(canonical[name]) == 1:
                presence, reason, kind = 'present', 'exact_canonical_name', 'canonical_name'
            else:
                presence, reason, kind = 'unknown', 'ambiguous_canonical_name', 'canonical_name'
                details = candidates(canonical[name], kind)
        else:
            eligible = [n for n in matches if key in fronts.get(n, [])]
            if eligible:
                front = eligible[0]
                matches = [front]
                if canonical.get(front):
                    presence, reason, kind = 'unknown', 'front_face_collision', 'front_face'
                    details = candidates(canonical[front], 'canonical_name') + candidates(fronts[front], kind)
                elif len(fronts[front]) > 1:
                    presence, reason, kind = 'unknown', 'ambiguous_front_face', 'front_face'
                    details = candidates(fronts[front], kind)
                else:
                    presence, reason, kind = 'present', 'explicit_front_face', 'front_face'
            elif matches:
                presence, reason, kind = 'unknown', 'face_alias_only', 'face_alias'
                details = candidates([k for n in matches for k in canonical.get(n, [])], 'canonical_name')
                details += candidates([k for n in matches for k in aliases.get(n, [])], 'face_alias')
        entries[key] = {'registry_presence': presence, 'reason': reason, 'registry_names': matches,
                        'match_kind': kind, 'candidates': details}
    return entries, sum(n not in canonical and n not in aliases for n in names)


def _validate_v3(payload: dict) -> None:
    fields = {'schema_version', 'snapshot_id', 'catalog_identity_sha256', 'catalog_sources',
              'engine_repository', 'engine_sha', 'revision_kind', 'qualification',
              'deployment_verification', 'registry_export_sha256', 'registry_name_count',
              'coverage_report_sha256', 'mapping', 'unmapped_registry_name_count', 'cards',
              'commander_gym', 'registry_export'}
    if type(payload.get('schema_version')) is not int or set(payload) != fields or payload.get('mapping') != MAPPING_V2:
        raise CatalogError('invalid coverage schema or identity mapping')
    raw = payload.get('registry_export')
    if not isinstance(raw, str):
        raise CatalogError('coverage registry export unavailable')
    try:
        encoded = raw.encode('utf-8')
    except UnicodeError as exc:
        raise CatalogError('invalid coverage registry UTF-8') from exc
    names = _registry_names(encoded)
    registered = set(names)
    if _hash(encoded) != payload['registry_export_sha256'] or len(names) != payload['registry_name_count']:
        raise CatalogError('coverage registry export hash or count mismatch')
    entries = payload.get('cards')
    if not isinstance(entries, dict):
        raise CatalogError('invalid coverage card evidence')
    combinations = {
        ('present', 'exact_canonical_name', 'canonical_name'),
        ('present', 'explicit_front_face', 'front_face'),
        ('unknown', 'ambiguous_canonical_name', 'canonical_name'),
        ('unknown', 'ambiguous_front_face', 'front_face'),
        ('unknown', 'front_face_collision', 'front_face'),
        ('unknown', 'face_alias_only', 'face_alias'),
        ('absent', 'not_in_complete_export', 'none')}
    for key, entry in entries.items():
        if (not isinstance(key, str) or not 1 <= len(key) <= 128 or not isinstance(entry, dict) or
                set(entry) != {'registry_presence', 'reason', 'registry_names', 'match_kind', 'candidates'} or
                any(not isinstance(entry.get(k), str) for k in ('registry_presence', 'reason', 'match_kind')) or
                (entry['registry_presence'], entry['reason'], entry['match_kind']) not in combinations or
                not isinstance(entry['registry_names'], list) or
                any(not isinstance(n, str) for n in entry['registry_names']) or
                entry['registry_names'] != sorted(set(entry['registry_names'])) or
                any(n not in registered for n in entry['registry_names']) or
                not isinstance(entry['candidates'], list)):
            raise CatalogError('invalid coverage card evidence')
        if ((entry['registry_presence'] == 'absent') != (not entry['registry_names']) or
                (entry['registry_presence'] != 'unknown' and entry['candidates']) or
                (entry['registry_presence'] == 'unknown' and not entry['candidates'])):
            raise CatalogError('invalid coverage candidate evidence')
        for candidate in entry['candidates']:
            if (not isinstance(candidate, dict) or set(candidate) != {'oracle_id', 'canonical_name', 'match_kind'} or
                    not isinstance(candidate['oracle_id'], str) or not 1 <= len(candidate['oracle_id']) <= 128 or
                    not isinstance(candidate['canonical_name'], str) or not 1 <= len(candidate['canonical_name']) <= 512 or
                    not isinstance(candidate['match_kind'], str) or candidate['match_kind'] not in ('canonical_name', 'front_face', 'face_alias')):
                raise CatalogError('invalid coverage candidate evidence')
    # Reuse unchanged legacy pin/receipt validation; new entry semantics are checked above
    # and regenerated from the selected catalog on read.
    proxy = {k: v for k, v in payload.items() if k != 'registry_export'}
    proxy['schema_version'] = 2 if proxy['commander_gym'] is not None else 1
    if proxy['commander_gym'] is None:
        del proxy['commander_gym']
    proxy['mapping'] = 'exact_catalog_card_or_own_face_name_v1'
    proxy['cards'] = {k: {'registry_presence': e['registry_presence'],
        'reason': {'present': 'exact_name_match', 'absent': 'not_in_complete_export',
                   'unknown': 'ambiguous_exact_name'}[e['registry_presence']],
        'registry_names': e['registry_names']} for k, e in entries.items()}
    _validate_payload(proxy)


def _receipt(receipt: Any, engine_sha: str, *, deployed: bool = False) -> dict:
    if not isinstance(receipt, dict) or receipt.get('engine_sha') != engine_sha:
        raise CatalogError('receipt must pin the same engine SHA')
    fields = {'engine_sha', 'source_url', 'verified_at', 'conclusion'}
    fields.update({'deployed_artifact_sha256'} if deployed else
                  {'registry_export_sha256', 'coverage_report_sha256'})
    if set(receipt) != fields:
        raise CatalogError('receipt contains missing or unsupported fields')
    url = receipt.get('source_url')
    if not isinstance(url, str) or not re.fullmatch(
            r'https://github\.com/Blue42hand/(?:argentum-engine|commander-gym)/'
            r'(?:actions/runs/[0-9]+|commit/[0-9a-f]{40})', url):
        raise CatalogError('receipt requires an exact public CI run or commit URL')
    if not deployed and '/actions/runs/' not in url:
        raise CatalogError('qualification receipt requires an exact public CI run URL')
    try:
        timestamp = datetime.fromisoformat(receipt['verified_at'].replace('Z', '+00:00'))
        if timestamp.tzinfo is None:
            raise ValueError()
    except (KeyError, AttributeError, TypeError, ValueError) as exc:
        raise CatalogError('receipt requires a timezone-qualified verified_at') from exc
    if receipt.get('conclusion') != 'success':
        raise CatalogError('receipt must record successful verification')
    result = {key: receipt[key] for key in
              ('engine_sha', 'source_url', 'verified_at', 'conclusion')}
    if deployed:
        digest = receipt.get('deployed_artifact_sha256')
        if not isinstance(digest, str) or not EVIDENCE_ID.fullmatch(digest):
            raise CatalogError('deployment receipt requires deployed artifact SHA-256')
        result['deployed_artifact_sha256'] = digest
    else:
        for key in ('registry_export_sha256', 'coverage_report_sha256'):
            digest = receipt.get(key)
            if not isinstance(digest, str) or not EVIDENCE_ID.fullmatch(digest):
                raise CatalogError('qualification receipt requires export and report SHA-256')
            result[key] = digest
    return result


def build_evidence(catalog: CardCatalog, registry_path: Path, report_path: Path,
                   qualification_receipt: dict, deployment_receipt: dict | None = None) -> dict:
    """Join a complete runtime name export to one exact catalog identity set.

    Names are matched byte-for-byte: no punctuation, case, fuzzy, or implicit
    front-face normalization. Canonical and explicit front identities are separate.
    Missing qualification/deployment evidence cannot silently become verified.
    """
    registry_bytes = _read(registry_path)
    report_bytes = _read(report_path)
    report = _json(report_bytes)
    engine = report.get('argentum')
    if (not isinstance(engine, dict) or engine.get('repository') != REPOSITORY or
            not isinstance(engine.get('commit'), str) or not HEX_SHA.fullmatch(engine['commit'])):
        raise CatalogError('report requires the exact Argentum repository and engine SHA')
    sha = engine['commit']
    qualification = _receipt(qualification_receipt, sha)
    if (qualification['registry_export_sha256'] != _hash(registry_bytes) or
            qualification['coverage_report_sha256'] != _hash(report_bytes)):
        raise CatalogError('qualification receipt does not match export and report bytes')
    deployment = None if deployment_receipt is None else _receipt(deployment_receipt, sha, deployed=True)
    names = _registry_names(registry_bytes)
    if report.get('schema') != 1 or type(report.get('registryCardNames')) is not int or report['registryCardNames'] != len(names):
        raise CatalogError('registry export must match report count')
    identities = _identity_roles(catalog)
    entries, unmapped = _role_entries(identities, names)
    status = catalog.catalog_status()
    payload = {
        'schema_version': 3, 'snapshot_id': catalog.snapshot_id,
        'catalog_identity_sha256': _hash(_canonical(identities)),
        'catalog_sources': status['datasets'],
        'engine_repository': REPOSITORY, 'engine_sha': sha,
        'revision_kind': 'verified_deployed_engine' if deployment else 'qualified_revision',
        'qualification': qualification, 'deployment_verification': deployment,
        'registry_export_sha256': _hash(registry_bytes), 'registry_name_count': len(names),
        'coverage_report_sha256': _hash(report_bytes),
        'mapping': MAPPING_V2,
        'registry_export': registry_bytes.decode('utf-8'),
        'commander_gym': report.get('commanderGym'),
        'unmapped_registry_name_count': unmapped,
        'cards': entries,
    }
    encoded = _canonical(payload)
    artifact = {'coverage_id': _hash(encoded), 'evidence': payload}
    _validate_payload(payload)
    if len(_canonical(artifact)) > MAX_EVIDENCE_BYTES:
        raise CatalogError('coverage evidence too large')
    return artifact


def publish_evidence(root: Path, artifact: dict) -> str:
    """Exclusive content-addressed publication; never replaces existing evidence."""
    coverage_id = artifact['coverage_id']
    if not EVIDENCE_ID.fullmatch(coverage_id) or _hash(_canonical(artifact['evidence'])) != coverage_id:
        raise CatalogError('coverage digest mismatch')
    _validate_payload(artifact['evidence'])
    directory = root / 'engine-coverage'
    if directory.is_symlink():
        raise CatalogError('coverage directory unavailable')
    directory.mkdir(exist_ok=True)
    encoded = _canonical(artifact)
    if len(encoded) > MAX_EVIDENCE_BYTES:
        raise CatalogError('coverage evidence too large')
    path = directory / f'{coverage_id}.json'
    # Hard-link a fully flushed staging file so readers never see partial bytes.
    import tempfile
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as stream:
        staging = Path(stream.name)
        try:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
            try:
                os.link(staging, path)
            except FileExistsError:
                if _read(path) != encoded:
                    raise CatalogError('immutable coverage artifact already exists with different bytes')
        finally:
            staging.unlink(missing_ok=True)
    return coverage_id


def load_evidence(root: Path, coverage_id: str, catalog: CardCatalog) -> dict:
    if not isinstance(coverage_id, str) or not EVIDENCE_ID.fullmatch(coverage_id):
        raise CatalogError('invalid coverage_id')
    directory = root / 'engine-coverage'
    if directory.is_symlink():
        raise CatalogError('coverage directory unavailable')
    artifact = _json(_read(directory / f'{coverage_id}.json'))
    payload = artifact.get('evidence')
    if (artifact.get('coverage_id') != coverage_id or not isinstance(payload, dict) or
            _hash(_canonical(payload)) != coverage_id):
        raise CatalogError('coverage digest mismatch')
    if payload.get('schema_version') not in (1, 2, 3) or payload.get('snapshot_id') != catalog.snapshot_id:
        raise CatalogError('coverage belongs to another snapshot or schema')
    identities = _identity_roles(catalog) if payload['schema_version'] == 3 else _identities(catalog)
    if payload.get('catalog_identity_sha256') != _hash(_canonical(identities)):
        raise CatalogError('coverage catalog identity mismatch')
    if payload.get('catalog_sources') != catalog.catalog_status()['datasets']:
        raise CatalogError('coverage catalog source mismatch')
    _validate_payload(payload)
    if payload['schema_version'] == 3:
        expected, unmapped = _role_entries(identities, _registry_names(payload['registry_export'].encode('utf-8')))
        if (unmapped != payload['unmapped_registry_name_count'] or
                any(expected.get(key) != entry for key, entry in payload['cards'].items())):
            raise CatalogError('coverage identity mapping mismatch')
    return payload


def _validate_payload(payload: dict) -> None:
    """Validate semantic pins at both the publication and read boundaries."""
    if payload.get('schema_version') == 3:
        return _validate_v3(payload)
    fields = {'schema_version', 'snapshot_id', 'catalog_identity_sha256', 'catalog_sources',
              'engine_repository', 'engine_sha', 'revision_kind', 'qualification',
              'deployment_verification', 'registry_export_sha256', 'registry_name_count',
              'coverage_report_sha256', 'mapping', 'unmapped_registry_name_count', 'cards'}
    if payload.get('schema_version') == 2:
        fields.add('commander_gym')
        gym = payload.get('commander_gym')
        if (not isinstance(gym, dict) or set(gym) != {'repository', 'commit'} or
                gym.get('repository') != 'https://github.com/Blue42hand/commander-gym.git' or
                not isinstance(gym.get('commit'), str) or not HEX_SHA.fullmatch(gym['commit'])):
            raise CatalogError('invalid coverage Commander Gym identity')
    if set(payload) != fields:
        raise CatalogError('coverage contains missing or unsupported fields')
    if (type(payload['registry_name_count']) is not int or
            not 1 <= payload['registry_name_count'] <= MAX_REGISTRY_NAMES or
            type(payload['unmapped_registry_name_count']) is not int or
            not 0 <= payload['unmapped_registry_name_count'] <= payload['registry_name_count']):
        raise CatalogError('invalid coverage registry counts')
    if payload.get('schema_version') not in (1, 2) or payload.get('mapping') != 'exact_catalog_card_or_own_face_name_v1':
        raise CatalogError('invalid coverage schema or identity mapping')
    sha = payload.get('engine_sha')
    if not isinstance(sha, str) or not HEX_SHA.fullmatch(sha) or payload.get('engine_repository') != REPOSITORY:
        raise CatalogError('invalid coverage engine identity')
    qualification = _receipt(payload.get('qualification'), sha)
    for key in ('registry_export_sha256', 'coverage_report_sha256'):
        if qualification[key] != payload.get(key):
            raise CatalogError('coverage qualification digest mismatch')
    deployment = payload.get('deployment_verification')
    if deployment is not None:
        _receipt(deployment, sha, deployed=True)
    expected_kind = 'qualified_revision' if deployment is None else 'verified_deployed_engine'
    if payload.get('revision_kind') != expected_kind:
        raise CatalogError('coverage verification kind mismatch')
    entries = payload.get('cards')
    if not isinstance(entries, dict) or len(entries) > MAX_REGISTRY_NAMES:
        raise CatalogError('invalid coverage card evidence')
    for entry in entries.values():
        if (not isinstance(entry, dict) or set(entry) != {'registry_presence', 'reason', 'registry_names'} or
                entry.get('registry_presence') not in ('present', 'absent', 'unknown') or
                entry.get('reason') != {'present': 'exact_name_match', 'absent': 'not_in_complete_export',
                                       'unknown': 'ambiguous_exact_name'}.get(entry.get('registry_presence')) or
                not isinstance(entry.get('registry_names'), list) or
                any(not isinstance(name, str) or not 1 <= len(name) <= 512 for name in entry['registry_names']) or
                (entry['registry_presence'] == 'absent' and bool(entry['registry_names'])) or
                (entry['registry_presence'] != 'absent' and not entry['registry_names'])):
            raise CatalogError('invalid coverage card evidence')


def metadata(coverage_id: str, payload: dict) -> dict:
    return {'coverage_id': coverage_id, **{key: value for key, value in payload.items() if key not in ('cards', 'registry_export')},
            'gameplay_correctness': 'unknown', 'tested_card_behavior': 'unknown',
            'upstream_presence': 'unknown', 'coverage_note': NOTE}


def lookup(catalog: CardCatalog, oracle_id: str, coverage_id: str | None, payload: dict | None) -> dict:
    # This is an Oracle-only API. Printing IDs and names cannot substitute.
    card = catalog.get_card(oracle_id=oracle_id)
    result = {'snapshot_id': catalog.snapshot_id, 'oracle_id': oracle_id,
              'coverage_id': coverage_id, 'registry_presence': 'unknown',
              'reason': 'coverage_not_selected', 'registry_names': [],
              'gameplay_correctness': 'unknown', 'coverage_note': NOTE}
    if card is None:
        result['reason'] = 'oracle_not_in_snapshot'
    elif payload is not None:
        result.update(payload['cards'].get(oracle_id, {'reason': 'oracle_evidence_missing'}))
        result['evidence'] = metadata(coverage_id, payload)
    result['scryfall_commander_legal'] = None if card is None else card['commander_legal']
    result['tested_card_behavior'] = 'unknown'
    result['upstream_presence'] = 'unknown'
    result['deployed_registry_presence_at_receipt'] = (
        result['registry_presence'] if payload is not None and
        payload['deployment_verification'] is not None else 'unknown')
    result['current_deployed_support'] = 'unknown'
    return result


def main() -> None:
    from .catalog_access import CatalogAccess
    parser = argparse.ArgumentParser(description='Publish offline pinned Argentum registry evidence')
    parser.add_argument('--catalog-root', type=Path, required=True)
    parser.add_argument('--snapshot-id', required=True)
    parser.add_argument('--registry-names', type=Path, required=True)
    parser.add_argument('--coverage-report', type=Path, required=True)
    parser.add_argument('--qualification-receipt', type=Path, required=True)
    parser.add_argument('--deployment-receipt', type=Path)
    args = parser.parse_args()
    access = CatalogAccess(args.catalog_root)
    artifact = build_evidence(access._catalog(args.snapshot_id), args.registry_names,
                              args.coverage_report, _json(_read(args.qualification_receipt)),
                              _json(_read(args.deployment_receipt)) if args.deployment_receipt else None)
    print(json.dumps({'coverage_id': publish_evidence(access.root, artifact)}))


if __name__ == '__main__':
    main()
