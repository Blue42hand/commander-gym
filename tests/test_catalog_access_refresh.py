import asyncio
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from commander_gym.card_catalog import CatalogError, SCHEMA_VERSION, create_schema
from commander_gym.catalog_access import CatalogAccess
from commander_gym.catalog_loader import CatalogLoadError, KINDS
from commander_gym.catalog_refresh import check_refresh, refresh_catalog
from commander_gym.catalog_mcp import _bounded, build_server


ONE = 'a' * 24
TWO = 'b' * 24


class CatalogAccessRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'snapshots').mkdir()
        (self.root / 'staging').mkdir()
        self.sources = {kind: {'url': f'https://data.scryfall.io/{kind}',
                               'compressed_size': 100, 'updated_at': '2026-10-04T21:00:00Z'}
                        for kind in KINDS}
        self._snapshot(ONE, 'Arcane Signet')
        self._current(ONE)

    def tearDown(self):
        self.temp.cleanup()

    def _snapshot(self, snapshot_id, name):
        database = self.root / 'snapshots' / f'{snapshot_id}.sqlite'
        with sqlite3.connect(database) as db:
            db.execute('PRAGMA foreign_keys=ON')
            create_schema(db)
            db.execute('INSERT INTO snapshots VALUES (?,?,?,?,?,?,?)',
                       (snapshot_id, '2026-10-05T00:00:00Z', '2026-10-04T21:00:00Z',
                        '2026-10-04T21:00:00Z', None, 'Scryfall fixture', 'Scryfall tag fixture'))
            db.execute('INSERT INTO cards VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (snapshot_id, f'oracle-{snapshot_id}', f'printing-{snapshot_id}',
                        name, 'Artifact', 'Add mana.', 2, '', 1, None, None, None, None))
            db.execute('INSERT INTO tags VALUES (?,?,?,?)',
                       (snapshot_id, 'oracle', 'ramp', 'ramp'))
            db.execute('INSERT INTO card_tags VALUES (?,?,?,?)',
                       (snapshot_id, f'oracle-{snapshot_id}', 'oracle', 'ramp'))
        (self.root / 'snapshots' / f'{snapshot_id}.json').write_text(json.dumps({
            'snapshot_id': snapshot_id, 'schema_version': SCHEMA_VERSION,
            'sources': self.sources}))

    def _current(self, snapshot_id):
        (self.root / 'current.json').write_text(json.dumps({
            'snapshot_id': snapshot_id, 'database': str(self.root / 'snapshots' / f'{snapshot_id}.sqlite')}))

    def _manifest(self, changed=False):
        return {kind: {'jsonl_download_uri': source['url'] + ('-new' if changed else ''),
                       'compressed_size': source['compressed_size'],
                       'updated_at': source['updated_at']}
                for kind, source in self.sources.items()}

    def test_explicit_snapshot_stays_pinned_when_current_changes(self):
        access = CatalogAccess(self.root)
        self.assertEqual(access.catalog_status()['snapshot_id'], ONE)
        self.assertEqual(access.search_tags(ONE, 'ramp')['tags'][0]['tag_id'], 'ramp')
        self.assertEqual(access.search_cards(ONE, name='Signet')['cards'][0]['oracle_id'], f'oracle-{ONE}')
        self.assertIsNone(access.get_card(ONE, oracle_id=f'oracle-{ONE}')['price_usd'])
        self._snapshot(TWO, 'New Signet')
        self._current(TWO)
        old = access.catalog_status(ONE)
        self.assertFalse(old['is_current'])
        self.assertEqual(old['current_snapshot_id'], TWO)
        self.assertEqual(access.search_cards(ONE, name='Arcane')['cards'][0]['name'], 'Arcane Signet')
        self.assertEqual(access.search_cards(TWO, name='Arcane')['cards'], [])
        self.assertIn('Argentum', old['rules_authority'])

    def test_paths_are_configured_not_tool_arguments(self):
        access = CatalogAccess(self.root)
        with self.assertRaisesRegex(CatalogError, 'invalid snapshot_id'):
            access.catalog_status('../../private')
        with self.assertRaisesRegex(CatalogError, 'invalid snapshot_id'):
            access.search_cards('/tmp/private')
        (self.root / 'snapshots' / f'{TWO}.sqlite').symlink_to(self.root / 'snapshots' / f'{ONE}.sqlite')
        with self.assertRaisesRegex(CatalogError, 'snapshot unavailable'):
            access.catalog_status(TWO)
        self.assertNotIn(str(self.root), json.dumps(access.catalog_status()))

    def test_refresh_skips_unchanged_manifest_without_download(self):
        with (mock.patch('commander_gym.catalog_refresh.fetch_manifest', return_value=self._manifest()),
              mock.patch('commander_gym.catalog_refresh.download_source') as download):
            result = refresh_catalog(self.root)
        self.assertEqual(result, {'current_snapshot_id': ONE, 'refresh_needed': False,
                                  'published': False})
        download.assert_not_called()

    def test_older_schema_needs_rebuild_even_when_sources_are_unchanged(self):
        metadata = self.root / 'snapshots' / f'{ONE}.json'
        old = json.loads(metadata.read_text())
        old['schema_version'] = 2
        metadata.write_text(json.dumps(old))
        with mock.patch('commander_gym.catalog_refresh.fetch_manifest', return_value=self._manifest()):
            result = check_refresh(self.root)
        self.assertTrue(result['refresh_needed'])

    def test_check_only_is_read_only_and_changed_build_failure_keeps_current(self):
        before = (self.root / 'current.json').read_bytes()
        with mock.patch('commander_gym.catalog_refresh.fetch_manifest', return_value=self._manifest(True)):
            result = check_refresh(self.root)
        self.assertTrue(result['refresh_needed'])
        self.assertEqual((self.root / 'current.json').read_bytes(), before)
        with (mock.patch('commander_gym.catalog_refresh.fetch_manifest', return_value=self._manifest(True)),
              mock.patch('commander_gym.catalog_refresh.download_source', return_value=object()) as download,
              mock.patch('commander_gym.catalog_refresh._publish_locked', side_effect=CatalogLoadError('bad source'))):
            with self.assertRaisesRegex(CatalogLoadError, 'bad source'):
                refresh_catalog(self.root)
        self.assertEqual(download.call_count, 3)
        self.assertEqual((self.root / 'current.json').read_bytes(), before)

    def test_corrupt_pointer_fails_closed_before_download(self):
        (self.root / 'current.json').write_text('{broken')
        with (mock.patch('commander_gym.catalog_refresh.fetch_manifest') as fetch,
              mock.patch('commander_gym.catalog_refresh.download_source') as download):
            with self.assertRaises(CatalogLoadError):
                refresh_catalog(self.root)
        fetch.assert_not_called()
        download.assert_not_called()

    def test_mcp_exposes_only_bounded_read_tools(self):
        from mcp import Client

        async def run():
            async with Client(build_server(self.root)) as client:
                listed = await client.list_tools()
                self.assertEqual({tool.name for tool in listed.tools},
                                 {'catalog_status', 'search_tags', 'search_cards', 'get_card',
                                  'list_engine_coverage', 'get_engine_coverage',
                                  'coverage_status', 'get_deck_coverage',
                                  'get_deckbuilding_methodology'})
                for tool in listed.tools:
                    self.assertTrue(tool.annotations.read_only_hint)
                    self.assertFalse(tool.annotations.destructive_hint)
                    self.assertNotIn('catalog_root', tool.input_schema['properties'])
                status = (await client.call_tool('catalog_status', {})).structured_content
                self.assertEqual(status['snapshot_id'], ONE)
                search = (await client.call_tool('search_cards',
                                                 {'snapshot_id': ONE, 'name': 'Signet'})).structured_content
                self.assertEqual(search['cards'][0]['name'], 'Arcane Signet')
                ranked = (await client.call_tool('search_cards',
                                                 {'snapshot_id': ONE, 'sort_by': 'edhrec_rank'})).structured_content
                self.assertEqual(ranked['cards'][0]['name'], 'Arcane Signet')
                self.assertIsNone(ranked['cards'][0]['edhrec_rank'])
                self.assertEqual(ranked['sort_by'], 'edhrec_rank')
                card = (await client.call_tool('get_card',
                                               {'snapshot_id': ONE, 'oracle_id': f'oracle-{ONE}'})).structured_content['result']
                self.assertIsNone(card['price_usd'])
                self.assertTrue(card['tags_advisory'])

                for tool, arguments, message in (
                    ('catalog_status', {'snapshot_id': TWO}, 'snapshot unavailable'),
                    ('search_tags', {'snapshot_id': ONE, 'query': 'ramp',
                                     'cursor': 'bad'}, 'invalid cursor'),
                    ('search_cards', {'snapshot_id': ONE, 'limit': 51},
                     'limit must be an integer from 1 to 50'),
                    ('get_card', {'snapshot_id': ONE, 'oracle_id': 'a',
                                  'printing_id': 'b'}, 'exactly one'),
                ):
                    with self.subTest(tool=tool):
                        failed = await client.call_tool(tool, arguments)
                        self.assertTrue(failed.is_error)
                        self.assertIn(message, ' '.join(part.text for part in failed.content))
                        self.assertNotIn(str(self.root), ' '.join(part.text for part in failed.content))

                with mock.patch('commander_gym.catalog_mcp.MAX_RESULT_BYTES', 10):
                    failed = await client.call_tool('catalog_status', {})
                self.assertTrue(failed.is_error)
                self.assertIn('narrow the query', ' '.join(part.text for part in failed.content))

                with mock.patch('commander_gym.catalog_access.CardCatalog.catalog_status',
                                side_effect=OSError('sensitive-host-path')):
                    failed = await client.call_tool('catalog_status', {})
                self.assertTrue(failed.is_error)
                self.assertNotIn('sensitive-host-path',
                                 ' '.join(part.text for part in failed.content))

        asyncio.run(run())
        with self.assertRaisesRegex(CatalogError, 'too large'):
            _bounded({'large': 'x' * 500_000})


if __name__ == '__main__':
    unittest.main()
