import asyncio
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from commander_gym.card_catalog import CardCatalog, CatalogError, create_schema
from commander_gym.catalog_access import CatalogAccess
from commander_gym.engine_coverage import build_evidence, load_evidence, publish_evidence
from commander_gym.engine_coverage import _canonical, _hash


SNAPSHOT = 'a' * 24
NEW_SNAPSHOT = 'b' * 24
ENGINE = 'c' * 40
NEW_ENGINE = 'd' * 40


class EngineCoverageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'snapshots').mkdir()
        path = self.root / 'snapshots' / f'{SNAPSHOT}.sqlite'
        with sqlite3.connect(path) as db:
            create_schema(db)
            db.execute('INSERT INTO snapshots VALUES (?,?,?,?,?,?,?)',
                       (SNAPSHOT, '2026-10-07T00:00:00Z', None, None, None, 'fixture', 'fixture'))
            for key, name in [('a', 'Alpha'), ('b', 'Beta'), ('c', 'Front // Back'),
                              ('d', 'Shared'), ('e', 'Other'), ('f', 'Foreign'),
                              ('g', 'alpha')]:
                db.execute('INSERT INTO cards VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                           (SNAPSHOT, key, f'printing-{key}', name, 'Artifact', '', 2, '',
                            1, None, None, 10, None))
            db.executemany('INSERT INTO card_faces VALUES (?,?,?,?,?,?,?,?)', [
                (SNAPSHOT, 'c', 0, None, 2, 'Front', 'Artifact', ''),
                (SNAPSHOT, 'c', 1, 'c', 2, 'Back', 'Artifact', ''),
                (SNAPSHOT, 'e', 0, None, 2, 'Shared', 'Artifact', ''),
                # A foreign meld face must never establish this card's support.
                (SNAPSHOT, 'f', 0, 'some-other-oracle', 2, 'Alpha', 'Artifact', ''),
            ])
        (self.root / 'current.json').write_text(json.dumps({'snapshot_id': SNAPSHOT}))
        self.catalog = CardCatalog(path, SNAPSHOT)
        self.access = CatalogAccess(self.root)
        self.names = self.root / 'registry.txt'
        self.report = self.root / 'coverage.json'
        self.artifact = self.build(['Alpha', 'Front', 'Shared', 'Unmapped'])
        self.coverage_id = publish_evidence(self.root, self.artifact)

    def tearDown(self):
        self.temp.cleanup()

    def receipt(self, sha=ENGINE, deployed=False):
        result = {'engine_sha': sha, 'source_url':
                  'https://github.com/Blue42hand/commander-gym/actions/runs/123',
                  'conclusion': 'success', 'verified_at': '2026-10-07T00:00:00Z'}
        if deployed:
            result['deployed_artifact_sha256'] = 'e' * 64
        else:
            result['registry_export_sha256'] = hashlib.sha256(self.names.read_bytes()).hexdigest()
            result['coverage_report_sha256'] = hashlib.sha256(self.report.read_bytes()).hexdigest()
        return result

    def build(self, names, sha=ENGINE, deployment=None):
        self.names.write_text('\n'.join(names) + '\n')
        self.report.write_text(json.dumps({'schema': 1, 'registryCardNames': len(names),
            'argentum': {'repository': 'https://github.com/Blue42hand/argentum-engine.git',
                         'commit': sha}, 'decks': []}))
        return build_evidence(self.catalog, self.names, self.report, self.receipt(sha), deployment)

    def lookup(self, oracle_id, coverage_id=None):
        return self.access.get_engine_coverage(SNAPSHOT, oracle_id, coverage_id or self.coverage_id)

    def test_exact_identity_ambiguity_and_no_gameplay_claim(self):
        for key in ['a', 'c']:
            result = self.lookup(key)
            self.assertEqual(result['registry_presence'], 'present')
            self.assertEqual(result['gameplay_correctness'], 'unknown')
            self.assertEqual(result['evidence']['engine_sha'], ENGINE)
            self.assertEqual(result['evidence']['revision_kind'], 'qualified_revision')
        self.assertEqual(self.lookup('c')['registry_names'], ['Front'])
        for key in ['d', 'e']:
            self.assertEqual(self.lookup(key)['registry_presence'], 'unknown')
            self.assertEqual(self.lookup(key)['reason'], 'ambiguous_exact_name')
        for key in ['b', 'f', 'g']:
            self.assertEqual(self.lookup(key)['registry_presence'], 'absent')
        self.assertEqual(self.lookup('printing-a')['registry_presence'], 'unknown')
        self.assertEqual(self.lookup('printing-a')['reason'], 'oracle_not_in_snapshot')

    def test_no_selection_or_missing_oracle_is_unknown(self):
        result = self.access.get_engine_coverage(SNAPSHOT, 'a')
        self.assertEqual(result['registry_presence'], 'unknown')
        self.assertEqual(result['reason'], 'coverage_not_selected')
        missing = self.access.get_engine_coverage(SNAPSHOT, 'missing', self.coverage_id)
        self.assertEqual(missing['registry_presence'], 'unknown')
        self.assertEqual(missing['reason'], 'oracle_not_in_snapshot')

    def test_opt_in_search_and_cursor_pins_revision_and_filters(self):
        self.assertEqual(len(self.access.search_cards(SNAPSHOT)['cards']), 7)
        page = self.access.search_cards(SNAPSHOT, registered_in=self.coverage_id, limit=1)
        self.assertEqual([c['oracle_id'] for c in page['cards']], ['a'])
        next_page = self.access.search_cards(SNAPSHOT, registered_in=self.coverage_id, limit=1,
                                             cursor=page['next_cursor'])
        self.assertEqual([c['oracle_id'] for c in next_page['cards']], ['c'])
        self.assertIsNone(next_page['next_cursor'])
        other = publish_evidence(self.root, self.build(['Alpha', 'Beta', 'Front'], NEW_ENGINE))
        with self.assertRaisesRegex(CatalogError, 'another query'):
            self.access.search_cards(SNAPSHOT, registered_in=other, cursor=page['next_cursor'])
        with self.assertRaisesRegex(CatalogError, 'another query'):
            self.access.search_cards(SNAPSHOT, cursor=page['next_cursor'])
        ranked = self.access.search_cards(SNAPSHOT, registered_in=self.coverage_id,
                                         sort_by='edhrec_rank', limit=1)
        self.assertEqual(len(self.access.search_cards(SNAPSHOT, registered_in=self.coverage_id,
                         sort_by='edhrec_rank', limit=1, cursor=ranked['next_cursor'])['cards']), 1)
        with self.assertRaisesRegex(CatalogError, 'pinned coverage_id'):
            self.access.search_cards(SNAPSHOT, _registry_filter=('anything', ('a',)))

    def test_qualified_new_revision_does_not_replace_deployed_evidence(self):
        deployed = self.build(['Alpha'], deployment=self.receipt(deployed=True))
        deployed_id = publish_evidence(self.root, deployed)
        newer = publish_evidence(self.root, self.build(['Beta'], NEW_ENGINE))
        available = self.access.list_engine_coverage(SNAPSHOT)['engine_coverage']
        kinds = {e['coverage_id']: e['revision_kind'] for e in available}
        self.assertEqual(kinds[deployed_id], 'verified_deployed_engine')
        self.assertEqual(kinds[newer], 'qualified_revision')
        self.assertEqual(self.lookup('a', deployed_id)['registry_presence'], 'present')
        self.assertEqual(self.lookup('a', newer)['registry_presence'], 'absent')
        with self.assertRaisesRegex(CatalogError, 'same engine SHA'):
            self.build(['Alpha'], NEW_ENGINE, self.receipt(deployed=True))

    def test_tamper_snapshot_and_catalog_identity_mismatch_fail_closed(self):
        path = self.root / 'engine-coverage' / f'{self.coverage_id}.json'
        original = path.read_bytes()
        modified = json.loads(original)
        modified['evidence']['cards']['a']['registry_presence'] = 'absent'
        path.write_text(json.dumps(modified))
        with self.assertRaisesRegex(CatalogError, 'digest mismatch'):
            self.lookup('a')
        path.write_bytes(original)
        with self.assertRaisesRegex(CatalogError, 'another snapshot'):
            load_evidence(self.root, self.coverage_id, CardCatalog(self.catalog.path, NEW_SNAPSHOT))
        with sqlite3.connect(self.catalog.path) as db:
            db.execute("UPDATE cards SET name='Changed' WHERE oracle_id='a'")
        with self.assertRaisesRegex(CatalogError, 'identity mismatch'):
            self.lookup('a')

    def test_export_and_receipts_are_required_and_bounded(self):
        for names in [['Beta', 'Alpha'], ['Alpha', 'Alpha'], [' Alpha'], []]:
            with self.subTest(names=names), self.assertRaises(CatalogError):
                self.build(names)
        self.build(['Alpha'])
        report = json.loads(self.report.read_text())
        report['registryCardNames'] = 2
        self.report.write_text(json.dumps(report))
        with self.assertRaises(CatalogError):
            build_evidence(self.catalog, self.names, self.report, self.receipt())
        self.build(['Alpha'])
        for receipt in [{}, {**self.receipt(), 'conclusion': 'failure'},
                        {**self.receipt(), 'source_url': 'file:///private'}]:
            with self.assertRaises(CatalogError):
                build_evidence(self.catalog, self.names, self.report, receipt)

    def test_publication_is_immutable_and_caller_cannot_supply_paths(self):
        original = (self.root / 'engine-coverage' / f'{self.coverage_id}.json').read_bytes()
        self.assertEqual(publish_evidence(self.root, self.artifact), self.coverage_id)
        self.assertEqual((self.root / 'engine-coverage' / f'{self.coverage_id}.json').read_bytes(), original)
        with self.assertRaisesRegex(CatalogError, 'invalid coverage_id'):
            self.lookup('a', '../../private')
        path = self.root / 'engine-coverage' / f'{self.coverage_id}.json'
        path.unlink()
        path.symlink_to(self.report)
        with self.assertRaisesRegex(CatalogError, 'unavailable'):
            self.lookup('a')

    def test_self_hashed_invalid_semantics_cannot_override_identity_or_correctness(self):
        for change in ['extra_field', 'extra_manifest_field', 'extra_receipt_field',
                       'wrong_receipt_hash', 'wrong_role']:
            artifact = json.loads(json.dumps(self.artifact))
            payload = artifact['evidence']
            if change == 'extra_field':
                payload['cards']['a']['gameplay_correctness'] = 'verified'
            elif change == 'extra_manifest_field':
                payload['unexpected_content'] = 'must not be exposed'
            elif change == 'extra_receipt_field':
                payload['qualification']['unexpected_content'] = 'must not be exposed'
            elif change == 'wrong_receipt_hash':
                payload['qualification']['registry_export_sha256'] = 'f' * 64
            else:
                payload['revision_kind'] = 'verified_deployed_engine'
            artifact['coverage_id'] = _hash(_canonical(payload))
            # Simulate an artifact created outside this publisher.
            (self.root / 'engine-coverage' / f"{artifact['coverage_id']}.json").write_bytes(_canonical(artifact))
            with self.subTest(change=change), self.assertRaises(CatalogError):
                self.lookup('a', artifact['coverage_id'])
            with self.assertRaises(CatalogError):
                publish_evidence(self.root, artifact)

    def test_missing_oracle_entry_remains_unknown_and_filter_excludes_it(self):
        artifact = json.loads(json.dumps(self.artifact))
        del artifact['evidence']['cards']['a']
        artifact['coverage_id'] = _hash(_canonical(artifact['evidence']))
        coverage_id = publish_evidence(self.root, artifact)
        result = self.lookup('a', coverage_id)
        self.assertEqual(result['registry_presence'], 'unknown')
        self.assertEqual(result['reason'], 'oracle_evidence_missing')
        filtered = self.access.search_cards(SNAPSHOT, registered_in=coverage_id)
        self.assertNotIn('a', [c['oracle_id'] for c in filtered['cards']])

    def test_real_mcp_adapter_readonly_lookup_filter_and_schema(self):
        from mcp import Client
        from commander_gym.catalog_mcp import build_server

        async def run():
            async with Client(build_server(self.root)) as client:
                listed = {t.name: t for t in (await client.list_tools()).tools}
                for name in ['list_engine_coverage', 'get_engine_coverage', 'search_cards']:
                    self.assertTrue(listed[name].annotations.read_only_hint)
                    self.assertFalse(listed[name].annotations.destructive_hint)
                self.assertEqual(set(listed['get_engine_coverage'].input_schema['properties']),
                                 {'snapshot_id', 'oracle_id', 'coverage_id'})
                result = await client.call_tool('get_engine_coverage',
                    {'snapshot_id': SNAPSHOT, 'oracle_id': 'a', 'coverage_id': self.coverage_id})
                self.assertFalse(result.is_error)
                self.assertEqual(result.structured_content['registry_presence'], 'present')
                result = await client.call_tool('search_cards',
                    {'snapshot_id': SNAPSHOT, 'registered_in': self.coverage_id})
                self.assertFalse(result.is_error)
                self.assertEqual(len(result.structured_content['cards']), 2)
                rejected = await client.call_tool('get_engine_coverage',
                    {'snapshot_id': SNAPSHOT, 'oracle_id': 'a', 'coverage_id': '../private'})
                self.assertTrue(rejected.is_error)
        asyncio.run(run())


if __name__ == '__main__':
    unittest.main()
