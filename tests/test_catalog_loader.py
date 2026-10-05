import gzip
import hashlib
import io
import fcntl
import os
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from commander_gym.card_catalog import CardCatalog
from commander_gym.catalog_loader import (
    CatalogLoadError, Source, _source_url, download_and_publish, download_source, publish_from_files,
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
            with path.open('wb') as output:
                with gzip.GzipFile(fileobj=output, mode='wb', filename='', mtime=0) as stream:
                    for record in records:
                        stream.write(json.dumps(record).encode() + b'\n')
            data = path.read_bytes()
            slug = kind.replace('_', '-')
            result[kind] = Source(kind, f'https://data.scryfall.io/{slug}/{slug}-20261004210033.jsonl.gz',
                                  '2026-10-04T21:00:33Z', len(data), path,
                                  hashlib.sha256(data).hexdigest(), '2026-10-05T00:00:00Z')
        return result

    def test_fixture_sources_are_stable_across_wall_clock_seconds(self):
        with mock.patch('gzip.time.time', return_value=1_000_000):
            first = self.sources()
        with mock.patch('gzip.time.time', return_value=2_000_000):
            second = self.sources()
        self.assertEqual(
            {kind: source.sha256 for kind, source in first.items()},
            {kind: source.sha256 for kind, source in second.items()},
        )

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
        self.assertEqual(query.search_cards(oracle_text='Draw a card')['cards'][0]['oracle_id'], OID)
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
        self.assertFalse((target / '.catalog-import.lock').exists())
        link = self.root / 'catalog-link'
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(CatalogLoadError, 'symlink'):
            publish_from_files(link, self.sources())

    def test_reversible_card_preserves_both_real_oracle_identities(self):
        reversible = card(OLD, '2025-01-01', None)
        reversible.pop('oracle_id')
        reversible.pop('cmc')
        reversible['layout'] = 'reversible_card'
        reversible['name'] = 'Front // Back'
        reversible['card_faces'] = [
            {'name': 'Front', 'type_line': 'Creature', 'oracle_text': 'Front text.',
             'oracle_id': OID, 'cmc': 2},
            {'name': 'Back', 'type_line': 'Artifact', 'oracle_text': 'Back text.',
             'oracle_id': OTHER, 'cmc': 3},
        ]
        self.records['default_cards'] = [reversible]
        metadata = publish_from_files(self.root / 'reversible', self.sources())
        query = CardCatalog(self.root / 'reversible/snapshots' / f"{metadata['snapshot_id']}.sqlite",
                            metadata['snapshot_id'])
        result = query.get_card(printing_id=OLD)
        self.assertEqual(query.catalog_status()['printing_count'], 1)
        self.assertEqual(result['printing_id'], OLD)
        self.assertEqual([face['oracle_id'] for face in result['oracle_faces']], [OID, OTHER])
        self.assertEqual([face['mana_value'] for face in result['oracle_faces']], [2, 3])
        self.assertEqual(query.get_card(oracle_id=OTHER)['name'], 'Back')
        self.assertEqual({card['oracle_id'] for card in query.search_cards(name='Back')['cards']}, {OTHER})
        self.assertEqual({card['oracle_id'] for card in query.search_cards(type_line='Artifact')['cards']}, {OTHER})
        self.assertEqual({card['oracle_id'] for card in query.search_cards(oracle_text='Back text.')['cards']}, {OTHER})
        self.assertEqual({card['oracle_id'] for card in query.search_cards(name='Front')['cards']}, {OID})
        self.assertEqual({card['oracle_id'] for card in query.search_cards(type_line='Creature')['cards']}, {OID})
        self.assertEqual({card['oracle_id'] for card in query.search_cards(oracle_text='Front text.')['cards']}, {OID})

    def test_reversible_same_oracle_identity_keeps_both_faces(self):
        # Scryfall's Propaganda // Propaganda reversible card has the same
        # face oracle_id on both sides (official card 3e3f0bcd-0796-494d-bf51-94b33c1671e9).
        reversible = card(OLD, '2025-01-01', '1.00')
        reversible.pop('oracle_id')
        reversible.pop('cmc')
        reversible['layout'] = 'reversible_card'
        reversible['name'] = 'Propaganda // Propaganda'
        reversible['card_faces'] = [
            {'name': 'Propaganda', 'type_line': 'Enchantment', 'oracle_text': 'Attack tax.',
             'oracle_id': OID, 'cmc': 3},
            {'name': 'Propaganda', 'type_line': 'Enchantment', 'oracle_text': 'Attack tax.',
             'oracle_id': OID, 'cmc': 3},
        ]
        self.records['default_cards'] = [reversible]
        metadata = publish_from_files(self.root / 'same-face-id', self.sources())
        query = CardCatalog(self.root / 'same-face-id/snapshots' / f"{metadata['snapshot_id']}.sqlite",
                            metadata['snapshot_id'])
        result = query.get_card(printing_id=OLD)
        self.assertEqual(result['oracle_id'], OID)
        self.assertEqual(query.catalog_status()['card_count'], 1)
        self.assertEqual(query.catalog_status()['printing_count'], 1)
        self.assertEqual([face['face_oracle_id'] for face in result['faces']], [OID, OID])
        self.assertEqual(len(result['requested_printing']['faces']), 2)

    def test_reversible_adventure_name_does_not_imply_face_count(self):
        # Official reversible Bloomvine Regent printing has three name parts,
        # but two card_faces with one shared Oracle identity.
        printing_id = '081f2de5-251a-41c9-a62f-11487f54d355'
        oracle_id = 'da1e019c-2ffb-412d-90d7-f2e5e5c44c4b'
        reversible = card(printing_id, '2025-01-01', None)
        reversible.pop('oracle_id')
        reversible.pop('cmc')
        reversible['layout'] = 'reversible_card'
        reversible['name'] = 'Bloomvine Regent // Claim Territory // Bloomvine Regent'
        reversible['card_faces'] = [
            {'name': 'Bloomvine Regent', 'type_line': 'Creature — Dragon', 'oracle_text': 'Flying.',
             'oracle_id': oracle_id, 'cmc': 5},
            {'name': 'Claim Territory', 'type_line': 'Sorcery — Omen', 'oracle_text': 'Flying.',
             'oracle_id': oracle_id, 'cmc': 5},
        ]
        self.records['default_cards'] = [reversible]
        self.records['rulings'][0]['oracle_id'] = oracle_id
        self.records['oracle_tags'][0]['taggings'][0]['oracle_id'] = oracle_id
        metadata = publish_from_files(self.root / 'reversible-adventure', self.sources())
        query = CardCatalog(self.root / 'reversible-adventure/snapshots' / f"{metadata['snapshot_id']}.sqlite",
                            metadata['snapshot_id'])
        result = query.get_card(printing_id=printing_id)
        self.assertEqual(result['oracle_id'], oracle_id)
        self.assertEqual([face['name'] for face in result['faces']],
                         ['Bloomvine Regent', 'Claim Territory'])
        self.assertEqual(query.search_cards(name='Claim Territory')['cards'][0]['oracle_id'], oracle_id)
        self.assertEqual(query.search_cards(type_line='Sorcery — Omen')['cards'][0]['oracle_id'], oracle_id)

    def test_large_valid_cmc_and_non_commander_record_are_retained(self):
        self.records['default_cards'] = [self.records['default_cards'][0]]
        self.records['default_cards'][0]['cmc'] = 1_000_000
        self.records['default_cards'][0]['legalities']['commander'] = 'not_legal'
        metadata = publish_from_files(self.root / 'large', self.sources())
        query = CardCatalog(self.root / 'large/snapshots' / f"{metadata['snapshot_id']}.sqlite",
                            metadata['snapshot_id'])
        result = query.get_card(printing_id=OLD)['requested_printing']
        self.assertEqual(result['oracle_id'], OID)
        self.assertEqual(result['name'], 'Example')
        self.assertEqual(query.search_cards(mana_value_min=1_000_000)['cards'][0]['oracle_id'], OID)
        self.assertEqual(query.search_cards(commander_legal=False)['cards'][0]['oracle_id'], OID)

    def test_import_lock_blocks_concurrent_publication(self):
        root = self.root / 'locked'
        root.mkdir()
        lock_fd = os.open(root / '.catalog-import.lock', os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(CatalogLoadError, 'already in progress'):
                publish_from_files(root, self.sources())
            with mock.patch('commander_gym.catalog_loader.fetch_manifest') as manifest:
                with self.assertRaisesRegex(CatalogLoadError, 'already in progress'):
                    download_and_publish(root)
                manifest.assert_not_called()
            self.assertFalse((root / 'current.json').exists())
        finally:
            os.close(lock_fd)

    def test_link_collision_never_unlinks_another_publishers_files(self):
        root = self.root / 'collision'
        real_link = os.link
        winner = []

        def collide(source, target, *args, **kwargs):
            target = Path(target)
            if target.suffix == '.sqlite':
                target.write_bytes(b'winner database')
                other = target.with_suffix('.json')
                other.write_bytes(b'winner metadata')
                winner.extend([target, other])
                raise FileExistsError('another publisher won')
            return real_link(source, target, *args, **kwargs)

        with mock.patch('commander_gym.catalog_loader.os.link', side_effect=collide):
            with self.assertRaises(FileExistsError):
                publish_from_files(root, self.sources())
        self.assertEqual([path.read_bytes() for path in winner],
                         [b'winner database', b'winner metadata'])

    def test_failed_first_build_can_retry_with_precreated_directories(self):
        root = self.root / 'precreated'
        (root / 'staging').mkdir(parents=True)
        (root / 'snapshots').mkdir()
        self.records['rulings'][0]['oracle_id'] = OTHER
        with self.assertRaisesRegex(CatalogLoadError, 'unknown oracle_id'):
            publish_from_files(root, self.sources())
        self.records['rulings'][0]['oracle_id'] = OID
        published = publish_from_files(root, self.sources())
        self.assertEqual(json.loads((root / 'current.json').read_text())['snapshot_id'],
                         published['snapshot_id'])
        symlink_root = self.root / 'symlink-snapshots'
        symlink_root.mkdir()
        (symlink_root / 'snapshots').symlink_to(root / 'snapshots', target_is_directory=True)
        with self.assertRaisesRegex(CatalogLoadError, 'symlink'):
            publish_from_files(symlink_root, self.sources())


if __name__ == '__main__':
    unittest.main()
