import sqlite3
import tempfile
import unittest
from pathlib import Path

from commander_gym.card_catalog import CardCatalog, CatalogError, create_schema, normalize_oracle_tag


class CardCatalogTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        path = Path(self.directory.name) / 'catalog.sqlite'
        with sqlite3.connect(path) as db:
            db.execute('PRAGMA foreign_keys=ON')
            create_schema(db)
            db.executemany('INSERT INTO snapshots VALUES (?,?,?,?,?,?,?)', [
                ('s1', '2026-10-05T00:00:00Z', '2026-10-04T20:00:00Z', '2026-10-04T21:00:33Z', None,
                 'Scryfall Oracle Cards fixture', 'Scryfall oracle_tags fixture'),
                ('s2', '2026-10-06T00:00:00Z', None, None, None, 'test', 'test'),
            ])
            db.executemany('INSERT INTO cards VALUES (?,?,?,?,?,?,?,?,?,?,?)', [
                ('s1', 'oracle-a', 'printing-a', 'Arcane Signet', 'Artifact', '{T}: Add one mana of any color in your commander’s color identity.', 2, '', 1, None, 'https://scryfall.com/card/a'),
                ('s1', 'oracle-b', 'printing-b', 'Ashnod’s Altar', 'Artifact', 'Sacrifice a creature: Add {C}{C}.', 3, '', 1, '5.20', 'https://scryfall.com/card/b'),
                ('s1', 'oracle-c', 'printing-c', 'Boros Charm', 'Instant', 'Permanents you control gain indestructible.', 2, 'RW', 1, None, 'https://scryfall.com/card/c'),
                ('s2', 'oracle-d', 'printing-d', 'New Card', 'Artifact', '', 1, '', None, None, None),
            ])
            db.executemany('INSERT INTO tags VALUES (?,?,?,?)', [
                ('s1', 'oracle', 'ramp', 'Ramp'),
                ('s1', 'oracle', 'sac', 'Sacrifice Outlet'),
                ('s1', 'art', 'gear', 'Gears'),
            ])
            db.executemany('INSERT INTO card_tags VALUES (?,?,?,?)', [
                ('s1', 'oracle-a', 'oracle', 'ramp'),
                ('s1', 'oracle-b', 'oracle', 'sac'),
                ('s1', 'oracle-b', 'art', 'gear'),
            ])
        self.catalog = CardCatalog(path, 's1')

    def tearDown(self):
        self.directory.cleanup()

    def test_status_and_exact_card_id_with_provenance(self):
        status = self.catalog.catalog_status()
        self.assertEqual(status['card_count'], 3)
        self.assertEqual(status['oracle_tags_updated_at'], '2026-10-04T21:00:33Z')
        self.assertEqual(status['tag_source'], 'Scryfall oracle_tags fixture')
        card = self.catalog.get_card(printing_id='printing-a')
        self.assertEqual(card['oracle_id'], 'oracle-a')
        self.assertEqual(card['printing_id'], 'printing-a')
        self.assertIsNone(card['price_usd'])
        self.assertTrue(card['price_note'])
        self.assertTrue(card['tags_advisory'])
        self.assertIsNone(self.catalog.get_card(oracle_id='missing'))

    def test_structured_search_and_snapshot_pagination(self):
        first = self.catalog.search_cards(type_line='Artifact', commander_legal=True, limit=1)
        self.assertEqual([card['name'] for card in first['cards']], ['Arcane Signet'])
        second = self.catalog.search_cards(type_line='Artifact', commander_legal=True, limit=1,
                                           cursor=first['next_cursor'])
        self.assertEqual([card['name'] for card in second['cards']], ['Ashnod’s Altar'])
        self.assertIsNone(second['next_cursor'])
        self.assertEqual(self.catalog.search_cards(tag_id='ramp')['cards'][0]['oracle_id'], 'oracle-a')
        self.assertEqual([c['name'] for c in self.catalog.search_cards(color_identity='R')['cards']],
                         ['Arcane Signet', 'Ashnod’s Altar'])
        self.assertEqual([c['name'] for c in self.catalog.search_cards(oracle_text='Sacrifice')['cards']],
                         ['Ashnod’s Altar'])
        self.assertEqual(self.catalog.search_cards(name='%')['cards'], [])

    def test_tags_are_bounded_and_advisory(self):
        result = self.catalog.search_tags('a', limit=1)
        self.assertTrue(result['advisory'])
        self.assertTrue(result['next_cursor'])
        self.assertIsNone(self.catalog.search_tags('a', limit=1, cursor=result['next_cursor'])['next_cursor'])
        self.assertEqual(self.catalog.search_tags('Gear', kind='art')['tags'][0]['tag_id'], 'gear')

    def test_validation_and_read_only(self):
        with self.assertRaises(CatalogError):
            self.catalog.search_cards(limit=51)
        with self.assertRaises(CatalogError):
            self.catalog.search_cards(color_identity='WRW')
        with self.assertRaises(CatalogError):
            self.catalog.get_card()
        cursor = self.catalog.search_cards(limit=1)['next_cursor']
        with self.assertRaisesRegex(CatalogError, 'another snapshot'):
            CardCatalog(self.catalog.path, 's2').search_cards(cursor=cursor)
        with self.assertRaises(CatalogError):
            self.catalog.search_tags('a', cursor='bogus')
        with self.catalog._connect() as db, self.assertRaises(sqlite3.OperationalError):
            db.execute('DELETE FROM cards')

    def test_official_oracle_tag_record_projection(self):
        # Field structure checked against a bounded official oracle_tags sample.
        record = {
            'object': 'tag', 'type': 'oracle',
            'id': '00155182-3099-4742-be68-f8b4ea259d78',
            'label': 'tutor-creature-giant', 'slug': 'tutor-creature-giant',
            'aliases': ['tutor-giant'], 'parent_ids': [], 'child_ids': [],
            'taggings': [{'oracle_id': '2445e58b-87ed-4ab2-8209-a5e1f566fba7', 'weight': 'median'}],
        }
        tag, membership = normalize_oracle_tag(record)
        self.assertEqual(tag, {'tag_id': record['id'], 'label': record['label']})
        self.assertEqual(membership, ('2445e58b-87ed-4ab2-8209-a5e1f566fba7',))
        with self.assertRaises(CatalogError):
            normalize_oracle_tag({**record, 'type': 'art'})


if __name__ == '__main__':
    unittest.main()
