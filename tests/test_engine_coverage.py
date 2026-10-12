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
from commander_gym.engine_coverage import _canonical, _hash, _identities


SNAPSHOT = 'a' * 24
NEW_SNAPSHOT = 'b' * 24
ENGINE = 'c' * 40
NEW_ENGINE = 'd' * 40


def add_printing(db, snapshot, key, name, layout, printing_id=None):
    db.execute('INSERT INTO printings (snapshot_id, printing_id, oracle_id, face_index, name, '
               'type_line, oracle_text, lang, digital, released_at, set_code, collector_number, '
               'faces_json, authoring_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
               (snapshot, printing_id or f'printing-{key}', key, 0, name, 'Artifact', '', 'en', 0,
                '2026-10-05', 'fixture', '1', '[]', json.dumps({'layout': layout})))


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
            add_printing(db, SNAPSHOT, 'c', 'Front // Back', 'transform')
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
        self.assertEqual(self.lookup('d')['registry_presence'], 'present')
        self.assertEqual(self.lookup('e')['registry_presence'], 'unknown')
        self.assertEqual(self.lookup('e')['reason'], 'face_alias_only')
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
        third = self.access.search_cards(SNAPSHOT, registered_in=self.coverage_id, limit=1,
                                        cursor=next_page['next_cursor'])
        self.assertEqual([c['oracle_id'] for c in third['cards']], ['d'])
        self.assertIsNone(third['next_cursor'])
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

    def test_status_unavailable_legacy_and_explicit_pin_comparison(self):
        status = self.access.coverage_status(SNAPSHOT)
        self.assertEqual(status['availability'], 'available')
        self.assertIsNone(status['selected_evidence'])
        status = self.access.coverage_status(SNAPSHOT, self.coverage_id, ENGINE, NEW_ENGINE)
        self.assertTrue(status['engine_pin_match'])
        self.assertIsNone(status['gym_pin_match'])
        self.assertEqual(status['freshness'], 'unknown')
        self.assertEqual(self.access.coverage_status(SNAPSHOT, self.coverage_id,
                         NEW_ENGINE)['freshness'], 'stale_for_requested_context')
        with self.assertRaises(CatalogError):
            self.access.coverage_status(SNAPSHOT, expected_engine_sha='main')
        (self.root / 'engine-coverage' / f'{self.coverage_id}.json').unlink()
        self.assertEqual(self.access.coverage_status(SNAPSHOT)['availability'], 'unavailable')
        with self.assertRaises(CatalogError):
            self.access.coverage_status(SNAPSHOT, self.coverage_id)

    def test_gym_provenance_v2_and_invalid_identity_fail_closed(self):
        self.build(['Alpha'])
        report = json.loads(self.report.read_text())
        report['commanderGym'] = {'repository': 'https://github.com/Blue42hand/commander-gym.git',
                                  'commit': NEW_ENGINE}
        self.report.write_text(json.dumps(report))
        artifact = build_evidence(self.catalog, self.names, self.report, self.receipt())
        coverage_id = publish_evidence(self.root, artifact)
        loaded = load_evidence(self.root, coverage_id, self.catalog)
        self.assertEqual(loaded['schema_version'], 3)
        self.assertEqual(loaded['commander_gym']['commit'], NEW_ENGINE)
        status = self.access.coverage_status(SNAPSHOT, coverage_id, ENGINE, NEW_ENGINE)
        self.assertEqual(status['freshness'], 'matches_requested_pins')
        self.assertEqual(status['current_deployed_support'], 'unknown')
        self.assertEqual(self.access.coverage_status(SNAPSHOT, coverage_id, ENGINE,
                         ENGINE)['freshness'], 'stale_for_requested_context')
        (self.root / 'current.json').write_text(json.dumps({'snapshot_id': NEW_SNAPSHOT}))
        self.assertEqual(self.access.coverage_status(SNAPSHOT, coverage_id, ENGINE,
                         NEW_ENGINE)['freshness'], 'stale_for_requested_context')
        for gym in ({'repository': 'private', 'commit': NEW_ENGINE},
                    {'repository': report['commanderGym']['repository'], 'commit': 'main'},
                    {**report['commanderGym'], 'trace': 'private'}):
            report['commanderGym'] = gym
            self.report.write_text(json.dumps(report))
            with self.assertRaises(CatalogError):
                build_evidence(self.catalog, self.names, self.report, self.receipt())

    def test_deck_slots_uncertainty_duplicates_legality_and_no_private_echo(self):
        result = self.access.get_deck_coverage(SNAPSHOT, ['a', 'a', 'b', 'd', 'missing'],
                                               self.coverage_id)
        self.assertEqual(result['registry_slots'], {'present': 3, 'absent': 1, 'unknown': 1})
        self.assertEqual(result['registered_slot_percent'], 60)
        self.assertEqual(result['scryfall_commander_legality_slots'],
                         {'legal': 4, 'not_legal': 0, 'unknown': 1})
        self.assertNotIn('oracle_ids', result)
        self.assertEqual(result['tested_card_behavior'], 'unknown')
        self.assertEqual(self.access.get_deck_coverage(SNAPSHOT, ['a'])['registry_slots']['unknown'], 1)
        for slots in ([], ['a'] * 1001, ['a' * 129], [True], 'a'):
            with self.assertRaises(CatalogError):
                self.access.get_deck_coverage(SNAPSHOT, slots, self.coverage_id)
        with self.assertRaises(CatalogError):
            self.access.get_deck_coverage(SNAPSHOT, ['a'], '../private')
        deployed = publish_evidence(self.root, self.build(['Alpha'], deployment=self.receipt(deployed=True)))
        card = self.lookup('a', deployed)
        self.assertTrue(card['scryfall_commander_legal'])
        self.assertEqual(card['deployed_registry_presence_at_receipt'], 'present')
        self.assertEqual(card['current_deployed_support'], 'unknown')

    def test_real_mcp_adapter_readonly_lookup_filter_and_schema(self):
        from mcp import Client
        from commander_gym.catalog_mcp import build_server

        async def run():
            async with Client(build_server(self.root)) as client:
                listed = {t.name: t for t in (await client.list_tools()).tools}
                for name in ['list_engine_coverage', 'get_engine_coverage', 'search_cards',
                             'coverage_status', 'get_deck_coverage']:
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
                self.assertEqual(len(result.structured_content['cards']), 3)
                status = await client.call_tool('coverage_status', {'snapshot_id': SNAPSHOT})
                self.assertFalse(status.is_error)
                self.assertEqual(status.structured_content['availability'], 'available')
                deck = await client.call_tool('get_deck_coverage',
                    {'snapshot_id': SNAPSHOT, 'oracle_ids': ['a', 'b'], 'coverage_id': self.coverage_id})
                self.assertFalse(deck.is_error)
                self.assertEqual(deck.structured_content['registry_slots']['present'], 1)
                rejected = await client.call_tool('get_engine_coverage',
                    {'snapshot_id': SNAPSHOT, 'oracle_id': 'a', 'coverage_id': '../private'})
                self.assertTrue(rejected.is_error)
        asyncio.run(run())


class CanonicalIdentityTests(unittest.TestCase):
    def setUp(self):
        self.base = EngineCoverageTests()
        self.base.setUp()
        self.fixture = json.loads((Path(__file__).parent / 'fixtures/coverage_issue248.json').read_text())

    def tearDown(self):
        self.base.tearDown()

    def add_card(self, key, name, layout=None, faces=(), legal=1):
        with sqlite3.connect(self.base.catalog.path) as db:
            db.execute('INSERT INTO cards VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (SNAPSHOT, key, f'printing-{key}', name, 'Artifact', '', 2, '',
                        legal, None, None, 10, None))
            add_printing(db, SNAPSHOT, key, name, layout)
            db.executemany('INSERT INTO card_faces VALUES (?,?,?,?,?,?,?,?)',
                           [(SNAPSHOT, key, index, oracle, 2, face, 'Artifact', '')
                            for index, oracle, face in faces])

    def build(self, names):
        return publish_evidence(self.base.root, self.base.build(sorted(set(names))))

    def lookup(self, key, cid):
        return self.base.access.get_engine_coverage(SNAPSHOT, key, cid)

    def test_all_13_pinned_oracle_identities_and_alias_parents(self):
        for card in self.fixture['cards']:
            self.add_card(card['oracle_id'], card['name'], card['layout'],
                          [(f['index'], f['oracle_id'], f['name']) for f in card['faces']],
                          int(card['commander_legal']))
        names = [c['name'] for c in self.fixture['targets']]
        cid = self.build(names)
        for target in self.fixture['targets']:
            with self.subTest(target=target['name']):
                single = self.lookup(target['oracle_id'], cid)
                self.assertEqual(single['registry_presence'], 'present')
                self.assertEqual(single['match_kind'], 'canonical_name')
                aggregate = self.base.access.get_deck_coverage(SNAPSHOT, [target['oracle_id']], cid)
                self.assertEqual(aggregate['registry_slots'], {'present': 1, 'absent': 0, 'unknown': 0})
                self.assertEqual(single['gameplay_correctness'], 'unknown')
                self.assertEqual(single['current_deployed_support'], 'unknown')
        targets = {c['oracle_id'] for c in self.fixture['targets']}
        for card in self.fixture['cards']:
            if card['oracle_id'] not in targets:
                with self.subTest(alias=card['name']):
                    single = self.lookup(card['oracle_id'], cid)
                    self.assertEqual(single['registry_presence'], 'unknown')
                    self.assertEqual(single['reason'], 'face_alias_only')
                    self.assertTrue(single['candidates'])
        ids = {c['name']: c['oracle_id'] for c in self.fixture['targets']}
        slots = [ids['Swamp']] * 10 + [ids['Sign in Blood']]
        aggregate = self.base.access.get_deck_coverage(SNAPSHOT, slots, cid)
        self.assertEqual(aggregate['registry_slots'], {'present': 11, 'absent': 0, 'unknown': 0})
        self.assertEqual(aggregate['total_slots'], 11)

    def test_prepare_and_art_series_front_registration_does_not_credit_whole(self):
        for layout in ('prepare', 'art_series'):
            self.add_card(layout, f'{layout} front // Alpha', layout,
                          [(0, None, f'{layout} front'), (1, None, 'Alpha')])
            cid = self.build([f'{layout} front', 'Alpha'])
            self.assertEqual(self.lookup(layout, cid)['reason'], 'face_alias_only')
            self.assertEqual(self.lookup('a', cid)['registry_presence'], 'present')

    def test_true_canonical_collision_remains_actionable_unknown(self):
        self.add_card('duplicate', 'Alpha', 'normal')
        cid = self.build(['Alpha'])
        for key in ('a', 'duplicate'):
            result = self.lookup(key, cid)
            self.assertEqual(result['reason'], 'ambiguous_canonical_name')
            self.assertEqual({c['oracle_id'] for c in result['candidates']}, {'a', 'duplicate'})
        self.assertEqual(self.base.access.get_deck_coverage(SNAPSHOT, ['a', 'duplicate'], cid)
                         ['registry_slots']['unknown'], 2)

    def test_explicit_multiface_front_back_canonical_and_foreign_roles(self):
        self.assertEqual(self.lookup('c', self.build(['Front']))['reason'], 'explicit_front_face')
        self.assertEqual(self.lookup('c', self.build(['Back']))['reason'], 'face_alias_only')
        self.assertEqual(self.lookup('c', self.build(['Front // Back']))['reason'], 'exact_canonical_name')
        self.assertEqual(self.lookup('f', self.build(['Alpha']))['registry_presence'], 'absent')
        self.add_card('modal', 'Door // Window', 'modal_dfc', [(0, None, 'Door'), (1, 'modal', 'Window')])
        self.assertEqual(self.lookup('modal', self.build(['Door']))['reason'], 'explicit_front_face')
        self.add_card('unknown-layout', 'Unknown Front // Unknown Back', None,
                      [(0, None, 'Unknown Front'), (1, None, 'Unknown Back')])
        self.assertEqual(self.lookup('unknown-layout', self.build(['Unknown Front']))['reason'], 'face_alias_only')
        with sqlite3.connect(self.base.catalog.path) as db:
            db.execute("UPDATE printings SET authoring_json=? WHERE oracle_id='c'",
                       (json.dumps({'layout': 99}),))
        self.assertEqual(self.lookup('c', self.build(['Front']))['reason'], 'face_alias_only')
        self.assertEqual(self.lookup('c', self.build(['Front // Back']))['reason'], 'exact_canonical_name')

    def test_only_matching_representative_printing_establishes_layout(self):
        with sqlite3.connect(self.base.catalog.path) as db:
            add_printing(db, SNAPSHOT, 'c', 'Front // Back', 'art_series', printing_id='other-printing-c')
        self.assertEqual(self.lookup('c', self.build(['Front']))['reason'], 'explicit_front_face')
        with sqlite3.connect(self.base.catalog.path) as db:
            db.execute("DELETE FROM printings WHERE printing_id='printing-c'")
        self.assertEqual(self.lookup('c', self.build(['Front']))['reason'], 'face_alias_only')

    def test_extra_foreign_face_cannot_establish_front_normalization(self):
        with sqlite3.connect(self.base.catalog.path) as db:
            db.execute('INSERT INTO card_faces VALUES (?,?,?,?,?,?,?,?)',
                       (SNAPSHOT, 'c', 2, 'foreign-oracle', 2, 'Foreign Extra', 'Artifact', ''))
        self.assertEqual(self.lookup('c', self.build(['Front']))['reason'], 'face_alias_only')
        self.assertEqual(self.lookup('c', self.build(['Front // Back']))['reason'], 'exact_canonical_name')

    def test_front_collision_and_canonical_precedence(self):
        self.add_card('other-front', 'Front // Other Back', 'modal_dfc',
                      [(0, None, 'Front'), (1, None, 'Other Back')])
        cid = self.build(['Front'])
        self.assertEqual(self.lookup('c', cid)['reason'], 'ambiguous_front_face')
        self.assertEqual(self.lookup('other-front', cid)['reason'], 'ambiguous_front_face')
        self.add_card('whole-front', 'Front', 'normal')
        cid = self.build(['Front', 'Front // Back'])
        self.assertEqual(self.lookup('whole-front', cid)['reason'], 'exact_canonical_name')
        self.assertEqual(self.lookup('c', cid)['reason'], 'exact_canonical_name')
        self.assertEqual(self.lookup('other-front', cid)['reason'], 'front_face_collision')

    def test_exact_case_punctuation_and_no_legality_filter(self):
        name = "K'rrik, Son of Yawgmoth"
        self.add_card('punctuation', name, 'normal', legal=0)
        self.assertEqual(self.lookup('punctuation', self.build([name]))['registry_presence'], 'present')
        self.assertEqual(self.lookup('punctuation', self.build(["K’rrik, Son of Yawgmoth"]))['registry_presence'], 'absent')
        cid = self.build(['alpha'])
        self.assertEqual(self.lookup('a', cid)['registry_presence'], 'absent')
        self.assertEqual(self.lookup('g', cid)['registry_presence'], 'present')

    def test_layout_hash_export_bytes_and_rehashed_false_claims_fail_closed(self):
        original = (self.base.root / 'engine-coverage' / f'{self.base.coverage_id}.json').read_bytes()
        self.base.names.write_bytes(b'Alpha\r\nFront\r\nShared\r\nUnmapped\r\n')
        artifact = build_evidence(self.base.catalog, self.base.names, self.base.report, self.base.receipt())
        cid = publish_evidence(self.base.root, artifact)
        loaded = load_evidence(self.base.root, cid, self.base.catalog)
        self.assertIn('\r\n', loaded['registry_export'])
        self.assertEqual(loaded['registry_export_sha256'], hashlib.sha256(self.base.names.read_bytes()).hexdigest())
        self.assertEqual((self.base.root / 'engine-coverage' / f'{self.base.coverage_id}.json').read_bytes(), original)
        false_claim = json.loads(json.dumps(artifact))
        false_claim['evidence']['cards']['e'].update(registry_presence='present',
            reason='exact_canonical_name', match_kind='canonical_name', candidates=[])
        false_claim['coverage_id'] = _hash(_canonical(false_claim['evidence']))
        false_id = publish_evidence(self.base.root, false_claim)
        with self.assertRaisesRegex(CatalogError, 'identity mapping mismatch'):
            load_evidence(self.base.root, false_id, self.base.catalog)
        for bad in (['Alpha', {}], ['Alpha', 'Alpha']):
            malformed = json.loads(json.dumps(artifact))
            malformed['evidence']['cards']['a']['registry_names'] = bad
            malformed['coverage_id'] = _hash(_canonical(malformed['evidence']))
            with self.assertRaises(CatalogError):
                publish_evidence(self.base.root, malformed)
        with sqlite3.connect(self.base.catalog.path) as db:
            db.execute("UPDATE printings SET authoring_json=? WHERE oracle_id='c'",
                       (json.dumps({'layout': 'art_series'}),))
        with self.assertRaisesRegex(CatalogError, 'identity mismatch'):
            load_evidence(self.base.root, cid, self.base.catalog)

    def test_legacy_schema1_2_frozen_fixtures_remain_unchanged(self):
        for version in (1, 2):
            data = (Path(__file__).parent / f'fixtures/legacy_registry_v{version}.json').read_bytes()
            artifact = json.loads(data)
            cid = publish_evidence(self.base.root, artifact)
            path = self.base.root / 'engine-coverage' / f'{cid}.json'
            before = path.read_bytes()
            loaded = load_evidence(self.base.root, cid, self.base.catalog)
            self.assertEqual(loaded['schema_version'], version)
            self.assertEqual(self.lookup('d', cid)['reason'], 'ambiguous_exact_name')
            self.assertEqual(self.lookup('e', cid)['reason'], 'ambiguous_exact_name')
            self.assertEqual(self.lookup('c', cid)['registry_presence'], 'present')
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(artifact['coverage_id'], _hash(_canonical(artifact['evidence'])))



if __name__ == '__main__':
    unittest.main()
