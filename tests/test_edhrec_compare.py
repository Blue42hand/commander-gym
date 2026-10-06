import asyncio
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from commander_gym.card_catalog import CardCatalog, CatalogError, create_schema
from commander_gym.catalog_mcp import build_server
from commander_gym.edhrec_compare import (ContextReader, SourceHTTPError,
                                          compare_contexts, parse_context)


SNAPSHOT = 'a' * 24
COMMANDER = 'oracle-commander'


def body(theme, cards):
    return json.dumps({'commander_slug': 'test-commander', 'theme_slug': theme,
                       'source_url': 'https://edhrec.com/commanders/test-commander' +
                                     ('/' + theme if theme else ''),
                       'retrieved_at': '2026-10-06T02:00:00Z', 'cards': cards}).encode()


def row(name, count, denominator, **extra):
    return {'name': name, 'inclusion_count': count,
            'potential_decks': denominator, **extra}


class EdhrecComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'snapshots').mkdir()
        self.database = self.root / 'snapshots' / f'{SNAPSHOT}.sqlite'
        with sqlite3.connect(self.database) as db:
            create_schema(db)
            db.execute('INSERT INTO snapshots VALUES (?,?,?,?,?,?,?)',
                       (SNAPSHOT, '2026-10-06T00:00:00Z', None, None, None, 'fixture', 'fixture'))
            for oid, name in ((COMMANDER, 'Test Commander'), ('oracle-one', 'Card One'),
                              ('oracle-two', 'Card Two'), ('oracle-three', 'Card Three')):
                db.execute('INSERT INTO cards VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                           (SNAPSHOT, oid, 'print-' + oid, name, 'Creature', '', 1,
                            '', 1, None, None, None, None))
        (self.root / 'current.json').write_text(json.dumps({'snapshot_id': SNAPSHOT}))
        self.payloads = {
            'tokens': body('tokens', [row('Card One', 40, 100, oracle_id='oracle-one',
                                         lift_ratio=1.25, synergy_percent=12.5,
                                         average_quantity=1),
                                      row('Card Two', 0, 50), row('Unknown', 4, 10)]),
            'artifacts': body('artifacts', [row('Card One', 10, 50),
                                             row('Card Two', None, 40),
                                             row('Card Three', 5, 25)]),
            None: body(None, [row('Card One', 60, 200)]),
        }

    def tearDown(self):
        self.temp.cleanup()

    def reader(self):
        return ContextReader(lambda commander, theme: self.payloads[theme],
                             clock=lambda: 0.0, sleep=lambda _: None)

    def resolve(self, name, oid):
        return CardCatalog(self.database, SNAPSHOT).resolve_exact_name(name, oid)

    def test_two_themes_preserve_denominators_null_zero_and_units(self):
        result = compare_contexts(self.reader(), 'test-commander', 'tokens',
                                  'artifacts', self.resolve)
        self.assertEqual(result['unresolved_count'], 1)
        self.assertEqual(result['coverage'], {'both_observed': 2, 'only_a_observed': 0,
                                              'only_b_observed': 1, 'comparable_rates': 1})
        one = next(card for card in result['cards'] if card['name'] == 'Card One')
        self.assertEqual([side['potential_decks'] for side in one['contexts']], [100, 50])
        self.assertEqual([side['inclusion_percent'] for side in one['contexts']], [40, 20])
        self.assertEqual(one['difference_percentage_points'], 20)
        self.assertEqual(one['contexts'][0]['lift_ratio'], 1.25)
        self.assertEqual(one['contexts'][0]['synergy_percent'], 12.5)
        self.assertEqual(one['contexts'][0]['average_quantity'], 1)
        two = next(card for card in result['cards'] if card['name'] == 'Card Two')
        self.assertEqual(two['contexts'][0]['inclusion_percent'], 0)
        self.assertIsNone(two['contexts'][1]['inclusion_percent'])
        self.assertIsNone(two['difference_percentage_points'])
        three = next(card for card in result['cards'] if card['name'] == 'Card Three')
        self.assertIsNone(three['contexts'][0])
        self.assertIn('overlap', result['interpretation'])

    def test_theme_vs_overall_is_not_complement_and_is_bounded(self):
        result = compare_contexts(self.reader(), 'test-commander', 'tokens', None,
                                  self.resolve, limit=1)
        self.assertEqual(result['cards'][0]['difference_percentage_points'], 10)
        self.assertTrue(result['truncated'])
        self.assertIsNone(result['contexts'][1]['theme_slug'])

    def test_exact_oracle_joins_reject_mismatch_and_ambiguity(self):
        catalog = CardCatalog(self.database, SNAPSHOT)
        self.assertEqual(catalog.resolve_exact_name('Card One'), 'oracle-one')
        self.assertIsNone(catalog.resolve_exact_name('card one'))
        self.assertIsNone(catalog.resolve_exact_name('Card One', 'oracle-wrong'))
        with sqlite3.connect(self.database) as db:
            db.execute('UPDATE cards SET name=? WHERE oracle_id=?', ('Card One', 'oracle-two'))
        self.assertIsNone(catalog.resolve_exact_name('Card One'))

    def test_parser_rejects_mismatch_unsafe_source_and_bad_counts(self):
        for payload in (body('tokens', [row('Card One', 11, 10)]),
                        body('tokens', [row('Card One', True, 10)]),
                        body('tokens', [row('Card One', 1, 10, synergy_percent=float('inf'))])):
            with self.assertRaises(CatalogError):
                parse_context(payload, 'test-commander', 'tokens')
        with self.assertRaises(CatalogError):
            parse_context(self.payloads['tokens'], 'other-commander', 'tokens')
        unsafe = json.loads(self.payloads['tokens'])
        unsafe['source_url'] = 'https://example.com/private'
        with self.assertRaises(CatalogError):
            parse_context(json.dumps(unsafe).encode(), 'test-commander', 'tokens')
        with self.assertRaises(CatalogError):
            parse_context(b'x' * 2_000_001, 'test-commander', 'tokens')

    def test_reader_spaces_requests_and_stops_on_403_or_429(self):
        now = [10.0]
        events = []
        def sleep(delay):
            now[0] += delay
        def fetch(_commander, theme):
            events.append(now[0])
            return self.payloads[theme]
        reader = ContextReader(fetch, clock=lambda: now[0], sleep=sleep)
        reader.read('test-commander', 'tokens')
        reader.read('test-commander', 'artifacts')
        self.assertEqual(events, [10.0, 12.0])
        for status in (403, 429):
            calls = []
            def restricted(*_):
                calls.append(1)
                raise SourceHTTPError(status)
            reader = ContextReader(restricted, clock=lambda: now[0], sleep=sleep)
            with self.assertRaisesRegex(CatalogError, 'stopped'):
                reader.read('test-commander', 'tokens')
            with self.assertRaisesRegex(CatalogError, 'stopped'):
                reader.read('test-commander', 'artifacts')
            self.assertEqual(len(calls), 1)

    def test_optional_mcp_tool_is_read_only_and_default_server_unchanged(self):
        from mcp import Client
        async def run():
            async with Client(build_server(self.root)) as client:
                self.assertNotIn('compare_commander_themes',
                                 {tool.name for tool in (await client.list_tools()).tools})
            async with Client(build_server(self.root, self.reader())) as client:
                tools = {tool.name: tool for tool in (await client.list_tools()).tools}
                self.assertTrue(tools['compare_commander_themes'].annotations.read_only_hint)
                data = (await client.call_tool('compare_commander_themes', {
                    'snapshot_id': SNAPSHOT, 'commander_oracle_id': COMMANDER,
                    'commander_slug': 'test-commander', 'theme_a': 'tokens',
                    'theme_b': 'artifacts', 'limit': 2})).structured_content
                self.assertEqual(data['snapshot_id'], SNAPSHOT)
                self.assertEqual(data['cards'][0]['difference_percentage_points'], 20)
        asyncio.run(run())


if __name__ == '__main__':
    unittest.main()
