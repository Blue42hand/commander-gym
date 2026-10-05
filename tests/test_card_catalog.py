import sqlite3

import pytest

from commander_gym.card_catalog import CardCatalog, CatalogError, create_schema


@pytest.fixture
def catalog(tmp_path):
    path = tmp_path / 'catalog.sqlite'
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
    return CardCatalog(path, 's1')


def test_status_and_exact_card_id_with_provenance(catalog):
    status = catalog.catalog_status()
    assert status['card_count'] == 3
    assert status['oracle_tags_updated_at'] == '2026-10-04T21:00:33Z'
    assert status['tag_source'] == 'Scryfall oracle_tags fixture'
    card = catalog.get_card(printing_id='printing-a')
    assert card['oracle_id'] == 'oracle-a'
    assert card['printing_id'] == 'printing-a'
    assert card['price_usd'] is None
    assert card['price_note']
    assert card['tags_advisory']
    assert catalog.get_card(oracle_id='missing') is None


def test_structured_search_and_snapshot_pagination(catalog):
    first = catalog.search_cards(type_line='Artifact', commander_legal=True, limit=1)
    assert [card['name'] for card in first['cards']] == ['Arcane Signet']
    second = catalog.search_cards(type_line='Artifact', commander_legal=True, limit=1,
                                  cursor=first['next_cursor'])
    assert [card['name'] for card in second['cards']] == ['Ashnod’s Altar']
    assert second['next_cursor'] is None
    assert catalog.search_cards(tag_id='ramp')['cards'][0]['oracle_id'] == 'oracle-a'
    assert [c['name'] for c in catalog.search_cards(color_identity='R')['cards']] == ['Arcane Signet', 'Ashnod’s Altar']
    assert [c['name'] for c in catalog.search_cards(oracle_text='Sacrifice')['cards']] == ['Ashnod’s Altar']
    assert catalog.search_cards(name='%')['cards'] == []


def test_tags_are_bounded_and_advisory(catalog):
    result = catalog.search_tags('a', limit=1)
    assert result['advisory'] is True
    assert result['next_cursor']
    assert catalog.search_tags('a', limit=1, cursor=result['next_cursor'])['next_cursor'] is None
    assert catalog.search_tags('Gear', kind='art')['tags'][0]['tag_id'] == 'gear'


def test_validation_and_read_only(catalog):
    with pytest.raises(CatalogError):
        catalog.search_cards(limit=51)
    with pytest.raises(CatalogError):
        catalog.search_cards(color_identity='WRW')
    with pytest.raises(CatalogError):
        catalog.get_card()
    cursor = catalog.search_cards(limit=1)['next_cursor']
    with pytest.raises(CatalogError, match='another snapshot'):
        CardCatalog(catalog.path, 's2').search_cards(cursor=cursor)
    with pytest.raises(CatalogError):
        catalog.search_tags('a', cursor='bogus')
    with catalog._connect() as db, pytest.raises(sqlite3.OperationalError):
        db.execute('DELETE FROM cards')
