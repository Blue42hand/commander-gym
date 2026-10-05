import gzip
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from commander_gym.card_catalog import CardCatalog
from commander_gym.catalog_loader import (
    CatalogLoadError, Source, _source_url, download_source, publish_from_files,
)


OID = '2445e58b-87ed-4ab2-8209-a5e1f566fba7'
OLD = '0000419b-0bba-4488-8f7a-6194544ce91e'
NEW = '0000579f-7b35-4ed3-b44c-db2a538066fe'
TAG = '00155182-3099-4742-be68-f8b4ea259d78'
OTHER = '44623693-51d6-49ad-8cd7-140505caf02f'


def card(printing_id, released, price, *, faces=None):
    return {'object': 'card', 'id': printing_id, 'oracle_id': OID,
            'name': 'Split Example' if faces else 'Example', 'lang': 'en', 'digital': False,
            'released_at': released, 'set': 'tst', 'collector_number': '1',
            'type_line': None if faces else 'Artifact', 'oracle_text': None if faces else 'Add mana.',
            'card_faces': faces, 'color_identity': [], 'cmc': 2.0,
            'legalities': {'commander': 'legal'}, 'prices': {'usd': price},
            'scryfall_uri': 'https://scryfall.com/card/tst/1'}


class CatalogLoaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.faces = [{'name': 'Front', 'type_line': 'Artifact', 'oracle_text': 'Add mana.'},
                      {'name': 'Back', 'type_line': 'Creature', 'oracle_text': 'Draw a card.'}]
        self.records = {
            'default_cards': [card(OLD, '2020-01-01', '2.00'),
                              card(NEW, '2025-01-01', None, faces=self.faces)],
            'rulings': [{'object': 'ruling', 'oracle_id': OID, 'source': 'wotc',
                         'published_at': '2025-02-07', 'comment': 'Example ruling.'}],
            'oracle_tags': [{'object': 'tag', 'id': TAG, 'label': 'ramp', 'type': 'oracle',
                             'taggings': [{'oracle_id': OID, 'weight': 'median'}]}],
        }

    def tearDown(self):
        self.temp.cleanup()

    def sources(self):
        result = {}
        for kind, records in self.records.items():
            path = self.root / f'{kind}.jsonl.gz'
            with gzip.open(path, 'wb') as stream:
                for record in records:
                    stream.write(json.dumps(record).encode() + b'\n')
            data = path.read_bytes()
            slug = kind.replace('_', '-')
            result[kind] = Source(kind, f'https://data.scryfall.io/{slug}/{slug}-20261004210033.jsonl.gz',
                                  '2026-10-04T21:00:33Z', len(data), path,
                                  hashlib.sha256(data).hexdigest(), '2026-10-05T00:00:00Z')
        return result

    def test_publish_keeps_printing_face_ruling_and_null_price_evidence(self):
        metadata = publish_from_files(self.root / 'catalog', self.sources())
        active = json.loads((self.root / 'catalog/current.json').read_text())
        self.assertEqual(active['snapshot_id'], metadata['snapshot_id'])
        query = CardCatalog(Path(active['database']), active['snapshot_id'])
        status = query.catalog_status()
        self.assertEqual(status['printing_count'], 2)
        self.assertEqual(status['ruling_count'], 1)
        self.assertEqual({source['kind'] for source in status['datasets']},
                         {'default_cards', 'rulings', 'oracle_tags'})
        result = query.get_card(oracle_id=OID)
        self.assertEqual(result['printing_id'], NEW)
        self.assertIsNone(result['price_usd'])
        self.assertEqual(result['printing_count'], 2)
        self.assertEqual([face['name'] for face in result['faces']], ['Front', 'Back'])
        self.assertEqual(result['rulings'][0]['comment'], 'Example ruling.')
        self.assertEqual(result['tags'][0]['tag_id'], TAG)
        self.assertEqual(len(result['provenance']['datasets']), 3)
        old = query.get_card(printing_id=OLD)
        self.assertEqual(old['requested_printing']['price_usd'], '2.00')
        self.assertEqual(old['requested_printing']['faces'], [])
        self.assertIsNone(old['price_usd'])  # Canonical price is not silently borrowed.
        self.assertEqual(metadata['counts'], {'default_cards': 2, 'rulings': 1, 'oracle_tags': 1})
        with self.assertRaises(CatalogLoadError):
            publish_from_files(self.root / 'catalog', self.sources())

    def test_bad_files_and_partial_download_do_not_replace_current(self):
        publish_from_files(self.root / 'catalog', self.sources())
        previous = (self.root / 'catalog/current.json').read_bytes()
        sources = self.sources()
        source = sources['rulings']
        source.path.write_bytes(source.path.read_bytes()[:-8])
        with self.assertRaisesRegex(CatalogLoadError, 'compressed size mismatch'):
            publish_from_files(self.root / 'catalog', sources)
        self.assertEqual((self.root / 'catalog/current.json').read_bytes(), previous)
        sources = self.sources()
        bad = sources['rulings']
        bad.path.write_bytes(b'not gzip')
        sources['rulings'] = Source(bad.kind, bad.url, bad.updated_at, 8, bad.path,
                                    hashlib.sha256(b'not gzip').hexdigest(), bad.downloaded_at)
        with self.assertRaisesRegex(CatalogLoadError, 'invalid gzip'):
            publish_from_files(self.root / 'catalog', sources)
        self.assertEqual((self.root / 'catalog/current.json').read_bytes(), previous)

    def test_duplicate_ids_and_cross_dataset_references_fail_closed(self):
        self.records['default_cards'].append(card(OLD, '2022-01-01', '3.00'))
        with self.assertRaises(CatalogLoadError):
            publish_from_files(self.root / 'bad1', self.sources())
        self.records['default_cards'].pop()
        self.records['rulings'][0]['oracle_id'] = OTHER
        with self.assertRaisesRegex(CatalogLoadError, 'unknown oracle_id'):
            publish_from_files(self.root / 'bad2', self.sources())
        self.records['rulings'][0]['oracle_id'] = OID
        self.records['oracle_tags'][0]['taggings'][0]['oracle_id'] = OTHER
        with self.assertRaisesRegex(CatalogLoadError, 'unknown oracle_id'):
            publish_from_files(self.root / 'bad3', self.sources())
        self.records['oracle_tags'][0]['taggings'][0]['oracle_id'] = OID
        self.records['oracle_tags'].append(dict(self.records['oracle_tags'][0]))
        with self.assertRaises(CatalogLoadError):
            publish_from_files(self.root / 'bad4', self.sources())

    def test_new_snapshot_preserves_previous_database_and_manifest(self):
        first = publish_from_files(self.root / 'catalog', self.sources())
        previous_db = self.root / 'catalog/snapshots' / f"{first['snapshot_id']}.sqlite"
        previous_meta = self.root / 'catalog/snapshots' / f"{first['snapshot_id']}.json"
        self.records['default_cards'][0]['prices']['usd'] = '3.00'
        second = publish_from_files(self.root / 'catalog', self.sources())
        self.assertNotEqual(first['snapshot_id'], second['snapshot_id'])
        self.assertTrue(previous_db.is_file())
        self.assertTrue(previous_meta.is_file())
        self.assertEqual(json.loads((self.root / 'catalog/current.json').read_text())['snapshot_id'],
                         second['snapshot_id'])
        self.assertEqual(CardCatalog(previous_db, first['snapshot_id']).get_card(printing_id=OLD)
                         ['requested_printing']['price_usd'], '2.00')

    def test_malformed_record_and_source_url_rejected(self):
        self.records['default_cards'][0]['prices'] = {'usd': 'NaN'}
        with self.assertRaisesRegex(CatalogLoadError, 'invalid USD price'):
            publish_from_files(self.root / 'bad', self.sources())
        with self.assertRaises(CatalogLoadError):
            _source_url('default_cards', 'https://evil.example/default-cards/default-cards-20261004210033.jsonl.gz')
        with self.assertRaises(CatalogLoadError):
            _source_url('default_cards', 'http://data.scryfall.io/default-cards/default-cards-20261004210033.jsonl.gz')

    def test_partial_download_and_redirect_fail_closed(self):
        url = 'https://data.scryfall.io/rulings/rulings-20261004210033.jsonl.gz'
        entry = {'jsonl_download_uri': url, 'compressed_size': 20, 'updated_at': '2026-10-04T21:00:33Z'}

        class Response(io.BytesIO):
            status = 200

            def geturl(self):
                return url

        with mock.patch('commander_gym.catalog_loader._OPENER.open', return_value=Response(b'partial')):
            with self.assertRaisesRegex(CatalogLoadError, 'partial download'):
                download_source('rulings', entry, self.root)
        (self.root / 'rulings.jsonl.gz').unlink()

        class Redirected(Response):
            def geturl(self):
                return 'https://evil.example/data'

        with mock.patch('commander_gym.catalog_loader._OPENER.open', return_value=Redirected(b'whole body')):
            with self.assertRaisesRegex(CatalogLoadError, 'redirect'):
                download_source('rulings', entry, self.root)

    def test_refuses_non_catalog_output_directory(self):
        target = self.root / 'existing-data'
        target.mkdir()
        (target / 'important.txt').write_text('keep')
        with self.assertRaisesRegex(CatalogLoadError, 'non-catalog'):
            publish_from_files(target, self.sources())
        self.assertEqual((target / 'important.txt').read_text(), 'keep')
        link = self.root / 'catalog-link'
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(CatalogLoadError, 'symlink'):
            publish_from_files(link, self.sources())


if __name__ == '__main__':
    unittest.main()
